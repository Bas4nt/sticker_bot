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
from telegram import Update, Message, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError, BadRequest
import uvicorn
from fastapi import FastAPI, Response, HTTPException
from fastapi.responses import JSONResponse

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
        """Convert image to sticker format."""
        try:
            message = update.message
            user_id = message.from_user.id
            
            # Get media from message, reply, or last used
            media = None
            if message.photo:
                media = MediaInfo(type='photo', file_id=message.photo[-1].file_id)
            else:
                media = await self.get_last_media(user_id, message, 'photo')
            
            if not media:
                await message.reply_text(
                    "To create a sticker, you can:\n"
                    "1. Send a photo directly\n"
                    "2. Reply to a photo with /stickerify\n"
                    "3. Use /stickerify right after sending a photo"
                )
                return
            
            # Process the image
            file = await context.bot.get_file(media.file_id)
            photo_bytes = await file.download_as_bytearray()
            
            with Image.open(BytesIO(photo_bytes)) as img:
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')
                
                # Resize while maintaining aspect ratio
                img.thumbnail(self.max_sticker_size)
                
                # Create output
                output = BytesIO()
                img.save(output, format='WebP', quality=95)
                output.seek(0)
                
                await message.reply_document(
                    document=output,
                    filename='sticker.webp',
                    caption="Here's your sticker! Use /addsticker to add it to a pack."
                )
        
        except Exception as e:
            await self.handle_error(update, e)

    async def kang_sticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Save sticker to user's pack."""
        try:
            message = update.message
            user_id = message.from_user.id
            
            # Get sticker from message, reply, or last used
            media = None
            if message.sticker:
                media = MediaInfo(
                    type='sticker',
                    file_id=message.sticker.file_id,
                    is_animated=message.sticker.is_animated,
                    is_video=message.sticker.is_video
                )
            else:
                media = await self.get_last_media(user_id, message)
            
            if not media or media.type not in ['sticker', 'photo']:
                await message.reply_text(
                    "To save a sticker to your pack, you can:\n"
                    "1. Send any sticker, then use /kang\n"
                    "2. Reply to any sticker with /kang\n"
                    "3. Send any photo, then use /kang\n"
                    "4. Reply to any photo with /kang"
                )
                return
            
            # Get or create user's sticker pack
            user_packs = await context.bot.get_user_sticker_sets(user_id)
            
            if not user_packs:
                # Create new pack
                pack_name = f"pack_{user_id}_by_{context.bot.username}"
                sticker_format = (
                    'animated' if media.is_animated
                    else 'video' if media.is_video
                    else 'static'
                )
                
                await context.bot.create_new_sticker_set(
                    user_id,
                    pack_name,
                    f"{message.from_user.first_name}'s Sticker Pack",
                    stickers=[],
                    sticker_format=sticker_format
                )
                user_packs = [pack_name]
            
            # Add sticker to pack
            pack = user_packs[0]
            file = await context.bot.get_file(media.file_id)
            sticker_bytes = await file.download_as_bytearray()
            
            # If it's a photo, convert to WebP
            if media.type == 'photo':
                with Image.open(BytesIO(sticker_bytes)) as img:
                    if img.mode != 'RGBA':
                        img = img.convert('RGBA')
                    img.thumbnail(self.max_sticker_size)
                    output = BytesIO()
                    img.save(output, format='WebP', quality=95)
                    sticker_bytes = output.getvalue()
            
            await context.bot.add_sticker_to_set(
                user_id,
                pack.name,
                sticker_bytes,
                '-'  # Emoji
            )
            
            await message.reply_text(
                f"Sticker successfully added to your pack!\n"
                f"Use /addsticker to add more stickers."
            )
        
        except Exception as e:
            await self.handle_error(update, e)

    async def quote_to_sticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Convert text to sticker with proper formatting and error handling."""
        try:
            message = update.message
            
            # Get text from reply or command args
            text = None
            if message.reply_to_message and message.reply_to_message.text:
                text = message.reply_to_message.text
            elif context.args:
                text = ' '.join(context.args)
            
            if not text:
                await message.reply_text(
                    "To create a text sticker, you can:\n"
                    "1. Reply to any message with /quote2sticker\n"
                    "2. Use: /quote2sticker Your Text Here"
                )
                return
            
            # Limit text length
            if len(text) > self.max_text_length:
                text = text[:self.max_text_length - 3] + "..."
            
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
            padding = 20
            x = (img.width - text_width) // 2
            y = (img.height - text_height) // 2
            
            # Draw background with rounded corners and semi-transparency
            background_color = (0, 0, 0, 128)
            draw.rectangle(
                [x-padding, y-padding, x+text_width+padding, y+text_height+padding],
                fill=background_color
            )
            
            # Draw text with anti-aliasing
            draw.text((x, y), wrapped_text, font=self.font, fill='white')
            
            # Optimize and save
            output = BytesIO()
            img.save(output, format='WebP', quality=95, method=6)
            output.seek(0)
            
            await message.reply_document(
                document=output,
                filename='quote.webp',
                caption="Here's your quote sticker! Use /kang to add it to your pack."
            )
        
        except Exception as e:
            await self.handle_error(update, e)

    async def handle_sticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming stickers."""
        try:
            message = update.message
            sticker = message.sticker
            
            # Store sticker in user state
            user_id = message.from_user.id
            self.store_media_state(
                user_id,
                MediaInfo(
                    type='sticker',
                    file_id=sticker.file_id,
                    is_animated=sticker.is_animated,
                    is_video=sticker.is_video
                )
            )
            
            # Provide information about the sticker
            info_text = (
                f"Sticker Info:\n"
                f"Type: {'Animated' if sticker.is_animated else 'Video' if sticker.is_video else 'Static'}\n"
                f"Size: {sticker.width}x{sticker.height}\n"
                f"Emoji: {sticker.emoji or 'None'}\n"
                f"\nUse /kang to add this sticker to your pack!"
            )
            await message.reply_text(info_text)
        
        except Exception as e:
            await self.handle_error(update, e)

    async def handle_animation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle GIFs and videos."""
        try:
            message = update.message
            animation = message.animation or message.video
            
            if animation.file_size > self.max_file_size:
                await message.reply_text(
                    f"File is too large! Maximum size is {self.max_file_size // (1024 * 1024)}MB"
                )
                return
            
            # Store animation in user state
            user_id = message.from_user.id
            self.store_media_state(
                user_id,
                MediaInfo(
                    type='animation' if message.animation else 'video',
                    file_id=animation.file_id
                )
            )
            
            # Provide information about the animation
            info_text = (
                f"Animation Info:\n"
                f"Type: {'GIF' if message.animation else 'Video'}\n"
                f"Size: {animation.width}x{animation.height}\n"
                f"Duration: {animation.duration}s\n"
                f"\nUse /kang to convert this to an animated sticker!"
            )
            await message.reply_text(info_text)
        
        except Exception as e:
            await self.handle_error(update, e)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command with comprehensive help message."""
        try:
            help_text = (
                "Welcome to the Sticker Bot! 🎨\n\n"
                "I can help you create and manage stickers. Here's what I can do:\n\n"
                "📸 *Convert Photos to Stickers*\n"
                "• Send any photo\n"
                "• Use /stickerify with a photo\n"
                "• Reply to a photo with /stickerify\n\n"
                "🎬 *Convert GIFs/Videos to Animated Stickers*\n"
                "• Send any GIF or video\n"
                "• Maximum size: 50MB\n\n"
                "💬 *Create Text Stickers*\n"
                "• Use /quote2sticker Your Text\n"
                "• Reply to any message with /quote2sticker\n\n"
                "📦 *Manage Sticker Packs*\n"
                "• Use /kang to add stickers to your pack\n"
                "• Supports static, animated, and video stickers\n"
                "• Maximum 120 stickers per pack\n\n"
                "Try sending me a photo, GIF, or video to get started!"
            )
            await update.message.reply_text(help_text, parse_mode='Markdown')
        
        except Exception as e:
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
            
            # Add media handlers with proper filters
            application.add_handler(MessageHandler(
                (
                    filters.PHOTO |
                    filters.Sticker.ALL |
                    filters.Document.IMAGE |
                    filters.Document.VIDEO |
                    filters.VIDEO |
                    filters.Animation
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
