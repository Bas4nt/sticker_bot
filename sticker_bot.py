#!/usr/bin/env python3
"""
Telegram Sticker Bot
A professional bot for creating and managing Telegram stickers.
"""

import os
import sys
import logging
import textwrap
from io import BytesIO
from typing import Optional, Dict, Any, Union, List
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Third-party imports
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from telegram import Update, Message, Bot, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
)
from telegram.error import TelegramError, BadRequest
import uvicorn
from fastapi import FastAPI, Response, HTTPException
from fastapi.responses import JSONResponse
from telegram.helpers import escape_markdown

# Configure logging with more detail
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('sticker_bot.log')
    ]
)
logger = logging.getLogger(__name__)

# Load and validate environment variables
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
PORT = int(os.getenv('PORT', '8080'))
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set!")

# FastAPI app for health checks
app = FastAPI(title="Sticker Bot API", version="1.0.0")

@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint for monitoring."""
    try:
        # Create a temporary bot instance to check Telegram API connectivity
        bot = Bot(BOT_TOKEN)
        me = await bot.get_me()
        await bot.close()
        
        return JSONResponse(
            content={
                "status": "healthy",
                "timestamp": datetime.utcnow().isoformat(),
                "bot_username": me.username
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}", exc_info=True)
        return JSONResponse(
            content={
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            },
            status_code=503
        )

@dataclass
class MediaInfo:
    """Data class for storing media information."""
    type: str
    file_id: str
    is_animated: bool = False
    is_video: bool = False
    mime_type: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    file_size: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert MediaInfo to dictionary."""
        return {k: v for k, v in self.__dict__.items() if v is not None}

class StickerBot:
    """Professional Telegram Sticker Bot implementation."""
    
    def __init__(self):
        """Initialize the StickerBot with configuration and state management."""
        # Media configurations
        self.supported_image_types = {'image/jpeg', 'image/png', 'image/webp'}
        self.supported_animation_types = {'video/mp4', 'image/gif', 'application/x-tgsticker'}
        self.max_sticker_size = (512, 512)
        self.max_file_size = 50 * 1024 * 1024  # 50MB limit
        self.max_text_length = 200
        
        # State management
        self.user_states: Dict[int, Dict[str, Any]] = {}
        self.cleanup_interval = 3600  # Clean up user states older than 1 hour
        
        # Font configuration
        self.font_size = 40
        self.font = self._initialize_font()
        
        # Sticker pack configuration
        self.max_stickers_per_pack = 120
        self.default_emoji = "🎨"
    
    def _initialize_font(self) -> ImageFont.FreeTypeFont:
        """Initialize the font for text stickers with fallbacks."""
        font_paths = [
            "arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Arial.ttf",  # macOS
            "C:\\Windows\\Fonts\\arial.ttf",    # Windows
        ]
        
        for font_path in font_paths:
            try:
                return ImageFont.truetype(font_path, self.font_size)
            except OSError:
                continue
        
        logger.warning("No TrueType fonts found, using default font")
        return ImageFont.load_default()
    
    def _clean_old_states(self) -> None:
        """Clean up old user states to prevent memory leaks."""
        current_time = datetime.utcnow()
        to_remove = []
        
        for user_id, state in self.user_states.items():
            last_update = datetime.fromisoformat(state.get('last_update', ''))
            if (current_time - last_update).total_seconds() > self.cleanup_interval:
                to_remove.append(user_id)
        
        for user_id in to_remove:
            del self.user_states[user_id]
    
    async def handle_error(self, update: Update, error: Exception) -> None:
        """Global error handler for all bot operations."""
        user_id = update.effective_user.id if update and update.effective_user else "Unknown"
        
        # Log the error with context
        error_context = {
            'user_id': user_id,
            'update_id': update.update_id if update else None,
            'chat_id': update.effective_chat.id if update and update.effective_chat else None,
            'error_type': type(error).__name__,
            'error_message': str(error)
        }
        logger.error(f"Error occurred: {error_context}", exc_info=True)
        
        # Prepare user-friendly error message
        error_message = "An error occurred while processing your request."
        
        if isinstance(error, TelegramError):
            if "file is too big" in str(error).lower():
                error_message = f"The file is too large. Maximum size is {self.max_file_size // (1024 * 1024)}MB."
            elif "wrong file type" in str(error).lower():
                error_message = "This file type is not supported."
            elif "STICKERSET_INVALID" in str(error):
                error_message = "Failed to create sticker pack. Please try again or contact support."
        elif isinstance(error, UnidentifiedImageError):
            error_message = "Failed to process the image. Please make sure it's a valid image file."
        elif isinstance(error, OSError):
            error_message = "Failed to process the file. Please try again with a different file."
        
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    f"❌ {error_message}\n\nIf this issue persists, please contact support."
                )
        except Exception as e:
            logger.error(f"Failed to send error message: {str(e)}")

    def store_media_state(self, user_id: int, media_info: MediaInfo) -> None:
        """Store media information in user state with timestamp."""
        self.user_states[user_id] = {
            'last_media': media_info.to_dict(),
            'last_update': datetime.utcnow().isoformat()
        }

    async def handle_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Universal handler for all media types."""
        try:
            message = update.message
            user_id = message.from_user.id
            
            # Clean up old states periodically
            self._clean_old_states()
            
            media_info = None
            if message.photo:
                media_info = MediaInfo(
                    type='photo',
                    file_id=message.photo[-1].file_id,
                    width=message.photo[-1].width,
                    height=message.photo[-1].height,
                    file_size=message.photo[-1].file_size
                )
                await self.stickerify(update, context)
            elif message.sticker:
                media_info = MediaInfo(
                    type='sticker',
                    file_id=message.sticker.file_id,
                    is_animated=message.sticker.is_animated,
                    is_video=message.sticker.is_video,
                    width=message.sticker.width,
                    height=message.sticker.height,
                    file_size=message.sticker.file_size
                )
                await self.handle_sticker(update, context)
            elif message.animation:
                media_info = MediaInfo(
                    type='animation',
                    file_id=message.animation.file_id,
                    width=message.animation.width,
                    height=message.animation.height,
                    file_size=message.animation.file_size
                )
                await self.handle_animation(update, context)
            elif message.video:
                if message.video.file_size > self.max_file_size:
                    raise ValueError(f"Video file too large (max {self.max_file_size // (1024 * 1024)}MB)")
                
                media_info = MediaInfo(
                    type='video',
                    file_id=message.video.file_id,
                    width=message.video.width,
                    height=message.video.height,
                    file_size=message.video.file_size
                )
                await self.handle_animation(update, context)
            elif message.document:
                if message.document.file_size > self.max_file_size:
                    raise ValueError(f"File too large (max {self.max_file_size // (1024 * 1024)}MB)")
                
                mime_type = message.document.mime_type
                media_info = MediaInfo(
                    type='document',
                    file_id=message.document.file_id,
                    mime_type=mime_type,
                    file_size=message.document.file_size
                )
                
                if mime_type in self.supported_image_types:
                    await self.stickerify(update, context)
                elif mime_type in self.supported_animation_types:
                    await self.handle_animation(update, context)
                else:
                    await message.reply_text(
                        "Unsupported file type. Please send an image, GIF, or video."
                    )
                    return
            
            if media_info:
                self.store_media_state(user_id, media_info)
        
        except ValueError as ve:
            await message.reply_text(str(ve))
        except Exception as e:
            await self.handle_error(update, e)

    async def get_last_media(
        self,
        user_id: int,
        message: Message,
        required_type: Optional[str] = None
    ) -> Optional[MediaInfo]:
        """Get media from reply or last used media with validation."""
        try:
            # Check reply message first
            if message.reply_to_message:
                reply = message.reply_to_message
                if reply.photo:
                    return MediaInfo(
                        type='photo',
                        file_id=reply.photo[-1].file_id,
                        width=reply.photo[-1].width,
                        height=reply.photo[-1].height,
                        file_size=reply.photo[-1].file_size
                    )
                elif reply.sticker:
                    return MediaInfo(
                        type='sticker',
                        file_id=reply.sticker.file_id,
                        is_animated=reply.sticker.is_animated,
                        is_video=reply.sticker.is_video,
                        width=reply.sticker.width,
                        height=reply.sticker.height,
                        file_size=reply.sticker.file_size
                    )
                elif reply.animation:
                    return MediaInfo(
                        type='animation',
                        file_id=reply.animation.file_id,
                        width=reply.animation.width,
                        height=reply.animation.height,
                        file_size=reply.animation.file_size
                    )
                elif reply.video:
                    if reply.video.file_size > self.max_file_size:
                        raise ValueError(f"Video file too large (max {self.max_file_size // (1024 * 1024)}MB)")
                    return MediaInfo(
                        type='video',
                        file_id=reply.video.file_id,
                        width=reply.video.width,
                        height=reply.video.height,
                        file_size=reply.video.file_size
                    )
                elif reply.document:
                    if reply.document.file_size > self.max_file_size:
                        raise ValueError(f"File too large (max {self.max_file_size // (1024 * 1024)}MB)")
                    return MediaInfo(
                        type='document',
                        file_id=reply.document.file_id,
                        mime_type=reply.document.mime_type,
                        file_size=reply.document.file_size
                    )
            
            # Check last used media
            if user_id in self.user_states and 'last_media' in self.user_states[user_id]:
                last_media = self.user_states[user_id]['last_media']
                if required_type is None or last_media['type'] == required_type:
                    return MediaInfo(**last_media)
            
            return None
        
        except ValueError as ve:
            raise ve
        except Exception as e:
            logger.error(f"Error getting last media: {str(e)}", exc_info=True)
            return None

    async def stickerify(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Convert image to sticker format with beautiful feedback."""
        try:
            message = update.message
            user_id = message.from_user.id
            
            # Send processing message
            processing_msg = await message.reply_text("🔄 Processing your image...")
            
            # Get media from message, reply, or last used
            media = None
            if message.photo:
                media = MediaInfo(
                    type='photo',
                    file_id=message.photo[-1].file_id,
                    width=message.photo[-1].width,
                    height=message.photo[-1].height,
                    file_size=message.photo[-1].file_size
                )
            else:
                media = await self.get_last_media(user_id, message, 'photo')
            
            if not media:
                await processing_msg.edit_text(
                    "❌ No image found!\n\n"
                    "*How to create a sticker:*\n"
                    "1️⃣ Send a photo directly\n"
                    "2️⃣ Reply to a photo with /stickerify\n"
                    "3️⃣ Use /stickerify right after sending a photo",
                    parse_mode='Markdown'
                )
                return
            
            # Check file size
            if media.file_size and media.file_size > self.max_file_size:
                await processing_msg.edit_text(
                    f"❌ Image too large! Maximum size is {self.max_file_size // (1024 * 1024)}MB"
                )
                return
            
            # Update processing message
            await processing_msg.edit_text("🎨 Creating your sticker...")
            
            # Process the image
            file = await context.bot.get_file(media.file_id)
            photo_bytes = await file.download_as_bytearray()
            
            with Image.open(BytesIO(photo_bytes)) as img:
                # Convert to RGBA
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')
                
                # Check dimensions
                original_size = (img.width, img.height)
                resized = False
                
                if img.width > 512 or img.height > 512:
                    # Resize while maintaining aspect ratio
                    img.thumbnail(self.max_sticker_size, Image.Resampling.LANCZOS)
                    resized = True
                
                # Optimize output
                output = BytesIO()
                img.save(output, format='WebP', quality=95, method=6)
                output.seek(0)
                
                # Create keyboard for next actions
                keyboard = [
                    [InlineKeyboardButton("➕ Add to Pack", callback_data=f"add_to_pack_{user_id}")],
                    [InlineKeyboardButton("🔄 Create Another", callback_data="create_another")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Delete processing message
                await processing_msg.delete()
                
                # Send the sticker with info
                size_info = f"Original: {original_size[0]}x{original_size[1]}"
                if resized:
                    size_info += f" → Resized: {img.width}x{img.height}"
                
                await message.reply_document(
                    document=output,
                    filename='sticker.webp',
                    caption=(
                        "✅ *Your sticker is ready!*\n\n"
                        f"📊 {size_info}\n\n"
                        "👉 Use /kang to add this to your sticker pack"
                    ),
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
        
        except Exception as e:
            await self.handle_error(update, e)

    async def kang_sticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Save sticker to user's pack with beautiful feedback and visuals."""
        try:
            message = update.message
            user_id = message.from_user.id
            
            # Send processing message
            processing_msg = await message.reply_text("🔄 Processing your sticker request...")
            
            # Get sticker from message, reply, or last used
            media = None
            if message.sticker:
                media = MediaInfo(
                    type='sticker',
                    file_id=message.sticker.file_id,
                    is_animated=message.sticker.is_animated,
                    is_video=message.sticker.is_video,
                    width=message.sticker.width,
                    height=message.sticker.height,
                    file_size=message.sticker.file_size
                )
            else:
                media = await self.get_last_media(user_id, message)
            
            if not media or media.type not in ['sticker', 'photo']:
                await processing_msg.edit_text(
                    "❌ *No sticker or photo found!*\n\n"
                    "*How to add a sticker to your pack:*\n"
                    "1️⃣ Send any sticker, then use /kang\n"
                    "2️⃣ Reply to any sticker with /kang\n"
                    "3️⃣ Send any photo, then use /kang\n"
                    "4️⃣ Reply to any photo with /kang",
                    parse_mode='Markdown'
                )
                return
            
            # Check file size
            if media.file_size and media.file_size > self.max_file_size:
                await processing_msg.edit_text(
                    f"❌ File too large! Maximum size is {self.max_file_size // (1024 * 1024)}MB"
                )
                return
            
            # Update processing message
            await processing_msg.edit_text("🎨 Adding to your sticker pack...")
            
            try:
                # Get user's sticker packs
                user_packs = await context.bot.get_user_sticker_sets(user_id)
                
                # Find or create appropriate pack
                pack_name = None
                sticker_format = (
                    'animated' if media.is_animated
                    else 'video' if media.is_video
                    else 'static'
                )
                
                # Find pack with matching format
                for pack in user_packs:
                    if pack.sticker_format == sticker_format:
                        # Check if pack is full
                        if len(pack.stickers) < self.max_stickers_per_pack:
                            pack_name = pack.name
                            break
                
                # Create new pack if needed
                if not pack_name:
                    pack_type_name = "Animated" if sticker_format == "animated" else "Video" if sticker_format == "video" else "Static"
                    await processing_msg.edit_text(f"🆕 Creating a new {pack_type_name} sticker pack...")
                    
                    pack_name = f"pack_{user_id}_{len(user_packs) + 1}_by_{context.bot.username}"
                    await context.bot.create_new_sticker_set(
                        user_id,
                        pack_name,
                        f"{message.from_user.first_name}'s {sticker_format.title()} Pack",
                        stickers=[],
                        sticker_format=sticker_format
                    )
                
                # Download and process media
                await processing_msg.edit_text("📥 Downloading media...")
                file = await context.bot.get_file(media.file_id)
                sticker_bytes = await file.download_as_bytearray()
                
                # Convert photo to WebP if needed
                if media.type == 'photo':
                    await processing_msg.edit_text("🖼️ Converting photo to sticker format...")
                    with Image.open(BytesIO(sticker_bytes)) as img:
                        if img.mode != 'RGBA':
                            img = img.convert('RGBA')
                        img.thumbnail(self.max_sticker_size, Image.Resampling.LANCZOS)
                        output = BytesIO()
                        img.save(output, format='WebP', quality=95, method=6)
                        sticker_bytes = output.getvalue()
                
                # Add sticker to pack
                await processing_msg.edit_text("📦 Adding to sticker pack...")
                await context.bot.add_sticker_to_set(
                    user_id,
                    pack_name,
                    sticker_bytes,
                    self.default_emoji
                )
                
                # Get pack link and information
                pack_link = f"https://t.me/addstickers/{pack_name}"
                
                # Get updated pack info
                pack_info = await context.bot.get_sticker_set(pack_name)
                sticker_count = len(pack_info.stickers)
                slots_left = self.max_stickers_per_pack - sticker_count
                
                # Create keyboard with pack link
                keyboard = [
                    [InlineKeyboardButton("👀 View Pack", url=pack_link)],
                    [InlineKeyboardButton("➕ Add Another", callback_data="add_another")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Success message
                await processing_msg.edit_text(
                    f"✅ *Sticker successfully added!*\n\n"
                    f"📦 *Pack:* `{pack_info.title}`\n"
                    f"🔢 *Stickers in pack:* {sticker_count}/{self.max_stickers_per_pack}\n"
                    f"🎯 *Slots left:* {slots_left}\n\n"
                    "Click below to view your pack:",
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            
            except BadRequest as e:
                if "STICKERSET_INVALID" in str(e):
                    # Pack was deleted or doesn't exist
                    await processing_msg.edit_text(
                        "⚠️ Failed to find your sticker pack. Creating a new one..."
                    )
                    # Remove invalid pack from user's state
                    if user_id in self.user_states:
                        self.user_states[user_id].pop('pack_name', None)
                    # Retry the operation
                    await self.kang_sticker(update, context)
                else:
                    raise
        
        except Exception as e:
            await self.handle_error(update, e)

    async def quote_to_sticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Convert text to sticker with beautiful styling and feedback."""
        try:
            message = update.message
            user_id = message.from_user.id
            
            # Get text from reply or command args
            text = None
            if message.reply_to_message and message.reply_to_message.text:
                text = message.reply_to_message.text
            elif context.args:
                text = ' '.join(context.args)
            
            if not text:
                await message.reply_text(
                    "❌ *No text provided!*\n\n"
                    "*How to create a text sticker:*\n"
                    "1️⃣ Reply to any message with /quote2sticker\n"
                    "2️⃣ Use: /quote2sticker Your Text Here",
                    parse_mode='Markdown'
                )
                return
            
            # Send processing message
            processing_msg = await message.reply_text("🔤 Creating your text sticker...")
            
            # Limit text length
            original_length = len(text)
            truncated = False
            if len(text) > self.max_text_length:
                text = text[:self.max_text_length - 3] + "..."
                truncated = True
            
            # Choose a random background color
            import random
            bg_colors = [
                (52, 152, 219, 180),  # Blue
                (155, 89, 182, 180),  # Purple
                (52, 73, 94, 180),    # Dark
                (22, 160, 133, 180),  # Green
                (231, 76, 60, 180),   # Red
                (241, 196, 15, 180),  # Yellow
            ]
            bg_color = random.choice(bg_colors)
            
            # Wrap text with proper width calculation
            max_width = 20
            wrapped_text = textwrap.fill(text, width=max_width)
            
            # Create image with proper size and transparency
            img = Image.new('RGBA', (512, 512), (255, 255, 255, 0))
            draw = ImageDraw.Draw(img)
            
            # Calculate text size and position
            text_bbox = draw.textbbox((0, 0), wrapped_text, font=self.font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            
            # Add padding
            padding = 30
            x = (img.width - text_width) // 2
            y = (img.height - text_height) // 2
            
            # Draw background with rounded corners
            rect_x0 = x - padding
            rect_y0 = y - padding
            rect_x1 = x + text_width + padding
            rect_y1 = y + text_height + padding
            radius = 30
            
            # Draw rounded rectangle
            draw.rectangle((rect_x0, rect_y0 + radius, rect_x1, rect_y1 - radius), fill=bg_color)
            draw.rectangle((rect_x0 + radius, rect_y0, rect_x1 - radius, rect_y1), fill=bg_color)
            draw.ellipse((rect_x0, rect_y0, rect_x0 + 2 * radius, rect_y0 + 2 * radius), fill=bg_color)
            draw.ellipse((rect_x1 - 2 * radius, rect_y0, rect_x1, rect_y0 + 2 * radius), fill=bg_color)
            draw.ellipse((rect_x0, rect_y1 - 2 * radius, rect_x0 + 2 * radius, rect_y1), fill=bg_color)
            draw.ellipse((rect_x1 - 2 * radius, rect_y1 - 2 * radius, rect_x1, rect_y1), fill=bg_color)
            
            # Add subtle shadow for text
            shadow_offset = 2
            draw.text((x + shadow_offset, y + shadow_offset), wrapped_text, font=self.font, fill=(0, 0, 0, 100))
            
            # Draw text with anti-aliasing
            draw.text((x, y), wrapped_text, font=self.font, fill='white')
            
            # Add a subtle pattern overlay for texture
            pattern_opacity = 10  # Very subtle
            for i in range(0, img.width, 4):
                for j in range(0, img.height, 4):
                    if (i + j) % 8 == 0:
                        if rect_x0 <= i <= rect_x1 and rect_y0 <= j <= rect_y1:
                            img.putpixel((i, j), (255, 255, 255, pattern_opacity))
            
            # Optimize and save
            output = BytesIO()
            img.save(output, format='WebP', quality=95, method=6)
            output.seek(0)
            
            # Create keyboard for next actions
            keyboard = [
                [InlineKeyboardButton("➕ Add to Pack", callback_data=f"add_to_pack_{user_id}")],
                [
                    InlineKeyboardButton("🎨 Change Style", callback_data="change_style"),
                    InlineKeyboardButton("🔄 New Quote", callback_data="new_quote")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Delete processing message
            await processing_msg.delete()
            
            # Send the sticker with info
            char_info = f"{len(text)} characters"
            if truncated:
                char_info += f" (truncated from {original_length})"
            
            await message.reply_document(
                document=output,
                filename='quote.webp',
                caption=(
                    "✅ *Your text sticker is ready!*\n\n"
                    f"📊 {char_info}\n\n"
                    "👉 Use /kang to add this to your sticker pack"
                ),
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        
        except Exception as e:
            await self.handle_error(update, e)

    async def handle_sticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming stickers with beautiful feedback."""
        try:
            message = update.message
            sticker = message.sticker
            user_id = message.from_user.id
            
            # Store sticker in user state
            self.store_media_state(
                user_id,
                MediaInfo(
                    type='sticker',
                    file_id=sticker.file_id,
                    is_animated=sticker.is_animated,
                    is_video=sticker.is_video,
                    width=sticker.width,
                    height=sticker.height,
                    file_size=sticker.file_size
                )
            )
            
            # Get sticker type name
            sticker_type = "Animated" if sticker.is_animated else "Video" if sticker.is_video else "Static"
            
            # Get emoji if available
            emoji_display = sticker.emoji if sticker.emoji else "None"
            
            # Create keyboard for actions
            keyboard = [
                [InlineKeyboardButton("➕ Add to Pack", callback_data=f"add_to_pack_{user_id}")],
                [
                    InlineKeyboardButton("🔄 Convert Format", callback_data="convert_sticker"),
                    InlineKeyboardButton("ℹ️ More Info", callback_data="sticker_info")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Provide information about the sticker
            info_text = (
                f"✨ *Sticker Details*\n\n"
                f"📊 *Type:* {sticker_type}\n"
                f"📐 *Size:* {sticker.width}x{sticker.height}\n"
                f"😀 *Emoji:* {emoji_display}\n"
                f"🆔 *Set:* {sticker.set_name or 'Not in a set'}\n\n"
                f"Use /kang to add this sticker to your pack!"
            )
            
            await message.reply_text(
                info_text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        
        except Exception as e:
            await self.handle_error(update, e)

    async def handle_animation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle GIFs and videos with beautiful feedback."""
        try:
            message = update.message
            animation = message.animation or message.video
            user_id = message.from_user.id
            
            if animation.file_size > self.max_file_size:
                await message.reply_text(
                    f"❌ *File too large!*\n\n"
                    f"Maximum size is {self.max_file_size // (1024 * 1024)}MB.\n"
                    f"Your file is {animation.file_size // (1024 * 1024)}MB.",
                    parse_mode='Markdown'
                )
                return
            
            # Store animation in user state
            media_type = 'animation' if message.animation else 'video'
            self.store_media_state(
                user_id,
                MediaInfo(
                    type=media_type,
                    file_id=animation.file_id,
                    width=animation.width,
                    height=animation.height,
                    file_size=animation.file_size
                )
            )
            
            # Get media type name
            media_type_name = "GIF" if message.animation else "Video"
            
            # Create keyboard for actions
            keyboard = [
                [InlineKeyboardButton("➕ Add to Pack", callback_data=f"add_to_pack_{user_id}")],
                [
                    InlineKeyboardButton("🔄 Convert to Sticker", callback_data="convert_to_sticker"),
                    InlineKeyboardButton("✂️ Trim", callback_data="trim_animation")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Calculate file size in MB with one decimal place
            file_size_mb = animation.file_size / (1024 * 1024)
            file_size_display = f"{file_size_mb:.1f}MB"
            
            # Provide information about the animation
            info_text = (
                f"✨ *{media_type_name} Details*\n\n"
                f"📊 *Type:* {media_type_name}\n"
                f"📐 *Size:* {animation.width}x{animation.height}\n"
                f"⏱️ *Duration:* {animation.duration}s\n"
                f"📦 *File Size:* {file_size_display}\n\n"
                f"Use /kang to convert this to an animated sticker!"
            )
            
            await message.reply_text(
                info_text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        
        except Exception as e:
            await self.handle_error(update, e)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command with comprehensive help message."""
        try:
            user = update.effective_user
            user_first_name = escape_markdown(user.first_name, version=1)
            message = update.effective_message

            welcome_text = (
                f"*Welcome to Sticker Master Bot!* 🎨✨\n\n"
                f"Hello {user_first_name}! I'm your personal sticker creation assistant.\n"
                f"I can transform your media into beautiful stickers in seconds!\n\n"
            )
            
            features_text = (
                "🌟 *What I can do for you:*\n\n"
                "🖼️ *Transform Photos into Stickers*\n"
                "• Send any photo or use /stickerify\n"
                "• Perfect for creating custom emoji stickers\n\n"
                "✨ *Create Animated Stickers*\n"
                "• Send any GIF or video\n"
                "• I'll convert it to Telegram's sticker format\n\n"
                "💬 *Create Text Stickers*\n"
                "• Use /quote2sticker Your Text\n"
                "• Great for quotes, jokes, or messages\n\n"
                "📦 *Manage Your Sticker Packs*\n"
                "• Use /kang to add stickers to your personal pack\n"
                "• I'll organize static, animated, and video stickers\n\n"
            )
            
            usage_text = (
                "👉 *Getting Started*\n"
                "Simply send me any photo, GIF, or video to begin!\n"
                "I'll guide you through the rest of the process."
            )
            
            keyboard = [
                [
                    InlineKeyboardButton("Create Photo Sticker", callback_data="help_photo"),
                    InlineKeyboardButton("Create Text Sticker", callback_data="help_text")
                ],
                [
                    InlineKeyboardButton("Create Animated Sticker", callback_data="help_animated"),
                    InlineKeyboardButton("Manage Packs", callback_data="help_packs")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Use effective_message to handle both commands and callbacks
            if query := update.callback_query:
                await query.edit_message_text(
                    welcome_text + features_text + usage_text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            else:
                await message.reply_text(
                    welcome_text + features_text + usage_text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
        
        except Exception as e:
            await self.handle_error(update, e)

    async def handle_button_press(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline button presses from various bot messages."""
        try:
            query = update.callback_query
            data = query.data
            
            # Answer the callback query to remove the loading state on the user's end
            await query.answer()
            
            # --- Help Menu Buttons ---
            if data == "help_photo":
                help_text = (
                    "*📸 Creating Photo Stickers*\n\n"
                    "To create a static sticker:\n"
                    "1. Send me any photo.\n"
                    "2. I'll automatically convert it to a sticker.\n"
                    "3. Use /kang to save it to your pack.\n\n"
                    "💡 *Pro Tip:* For best results, use square images."
                )
                await query.edit_message_text(text=help_text, parse_mode='Markdown')
            
            elif data == "help_text":
                help_text = (
                    "*💬 Creating Text Stickers*\n\n"
                    "To create a text sticker:\n"
                    "1. Use command: `/quote2sticker Your text here`\n"
                    "2. Or reply to any message with /quote2sticker.\n\n"
                    "💡 *Pro Tip:* Keep text short for better readability."
                )
                await query.edit_message_text(text=help_text, parse_mode='Markdown')

            elif data == "help_animated":
                help_text = (
                    "*🎬 Creating Animated Stickers*\n\n"
                    "To create an animated sticker:\n"
                    "1. Send me any GIF or short video.\n"
                    "2. I'll convert it to the sticker format.\n"
                    "3. Use /kang to save it to your pack.\n\n"
                    "💡 *Pro Tip:* Keep animations under 3 seconds."
                )
                await query.edit_message_text(text=help_text, parse_mode='Markdown')

            elif data == "help_packs":
                help_text = (
                    "*📦 Managing Sticker Packs*\n\n"
                    "To manage your sticker packs:\n"
                    "1. Create stickers using any method.\n"
                    "2. Use /kang to add them to your pack.\n"
                    "3. I'll automatically create separate packs for different sticker types.\n\n"
                    "💡 *Pro Tip:* You can have up to 120 stickers per pack."
                )
                await query.edit_message_text(text=help_text, parse_mode='Markdown')
            
            elif data == "back_to_main":
                # This will re-render the start menu by editing the current message
                await self.start(update, context)

            # --- Action Buttons ---
            elif data.startswith("add_to_pack_"):
                await query.message.reply_text(
                    "To add this sticker to a pack, please reply to the message containing the sticker and use the `/kang` command."
                )
            
            # --- Placeholder Buttons ---
            else:
                await query.answer(
                    "This feature is coming soon!",
                    show_alert=True
                )
                
        except BadRequest as e:
            if "Message is not modified" in str(e):
                # Ignore this error, it happens when a user clicks the same button twice
                pass
            else:
                logger.error(f"BadRequest in button handler: {str(e)}", exc_info=True)
                await self.handle_error(update, e)
                
        except Exception as e:
            logger.error(f"Error handling button: {str(e)}", exc_info=True)
            await self.handle_error(update, e)

    def run(self) -> None:
        """Run the bot with proper error handling and logging."""
        try:
            # Create application with persistence
            application = Application.builder().token(BOT_TOKEN).build()
            
            # Add command handlers
            application.add_handler(CommandHandler('start', self.start))
            application.add_handler(CommandHandler('stickerify', self.stickerify))
            application.add_handler(CommandHandler('kang', self.kang_sticker))
            application.add_handler(CommandHandler('quote2sticker', self.quote_to_sticker))
            
            # Add callback query handler for inline buttons
            application.add_handler(CallbackQueryHandler(self.handle_button_press))
            
            # Add media handlers with proper filters
            application.add_handler(MessageHandler(
                (
                    filters.PHOTO |
                    filters.Sticker.ALL |
                    filters.Document.IMAGE |
                    filters.Document.VIDEO |
                    filters.VIDEO |
                    filters.ANIMATION
                ),
                self.handle_media
            ))
            
            # Add error handler
            application.add_error_handler(self.handle_error)
            
            # Start FastAPI server for health checks
            import uvicorn
            import asyncio
            from concurrent.futures import ThreadPoolExecutor
            
            def run_fastapi():
                uvicorn.run(app, host="0.0.0.0", port=PORT)
            
            # Run FastAPI in a separate thread
            executor = ThreadPoolExecutor(max_workers=1)
            asyncio.get_event_loop().run_in_executor(executor, run_fastapi)
            
            # Start the bot
            logger.info(f"Starting bot on port {PORT}...")
            application.run_polling(allowed_updates=Update.ALL_TYPES)
        
        except Exception as e:
            logger.critical(f"Bot crashed: {str(e)}", exc_info=True)
            raise

if __name__ == '__main__':
    try:
        bot = StickerBot()
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.critical(f"Fatal error: {str(e)}", exc_info=True)
        sys.exit(1) 
