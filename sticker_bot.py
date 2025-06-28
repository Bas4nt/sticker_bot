# Requirements and dependencies for the Telegram Sticker Bot
# python-telegram-bot==20.7
# Pillow==10.1.0
# moviepy==1.0.3
# python-dotenv==1.0.0
# fastapi==0.104.1
# uvicorn==0.24.0

import os
import tempfile
import logging
from typing import Dict, List
from datetime import datetime
import asyncio
from io import BytesIO
import textwrap
import threading
import uvicorn
from fastapi import FastAPI

# Environment and Telegram imports
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Image processing imports
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import VideoFileClip

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
PORT = int(os.getenv('PORT', 8080))

# Create FastAPI app for health checks
app = FastAPI()

@app.get("/")
def health_check():
    return {"status": "healthy"}

# Global variables for user states and temporary data
user_states: Dict[int, dict] = {}
temp_files: List[str] = []

class StickerBot:
    def __init__(self):
        # Initialize bot configurations
        self.supported_image_types = {'image/jpeg', 'image/png', 'image/webp'}
        self.supported_animation_types = {'video/mp4', 'image/gif', 'application/x-tgsticker'}
        self.max_sticker_size = (512, 512)
        self.max_file_size = 50 * 1024 * 1024  # 50MB limit
        self.user_states = {}

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Handle the /start command
        welcome_message = (
            "Welcome to the Advanced Sticker Maker Bot!\n\n"
            "Here's what I can do for you:\n\n"
            "/stickerify - Convert any image to a Telegram sticker\n"
            "/addtext - Add custom text to your image\n"
            "/meme - Create a meme with top/bottom text\n"
            "/gif2sticker - Convert GIF to animated sticker\n"
            "/kang - Save any sticker to your pack\n"
            "/createstickerpack - Create a new sticker pack\n"
            "/addsticker - Add sticker to existing pack\n"
            "/quote2sticker - Convert text to styled sticker\n"
            "/help - Show detailed instructions\n\n"
            "Send me any image, GIF, or video to get started!"
        )
        await update.message.reply_text(welcome_message)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Handle the /help command
        help_text = (
            "Detailed Usage Instructions:\n\n"
            "Image to Sticker:\n"
            "1. Send any image\n"
            "2. Use /stickerify to convert it\n"
            "3. Choose your pack to add it\n\n"
            "Adding Text:\n"
            "1. Send an image\n"
            "2. Use /addtext followed by your text\n"
            "3. Choose position and style\n\n"
            "Creating Memes:\n"
            "1. Send an image\n"
            "2. Use /meme\n"
            "3. Send top text\n"
            "4. Send bottom text\n\n"
            "GIF to Sticker:\n"
            "1. Send a GIF/short video\n"
            "2. Use /gif2sticker\n"
            "3. Wait for conversion\n\n"
            "Sticker Pack Management:\n"
            "- /createstickerpack - Create new pack\n"
            "- /addsticker - Add to existing pack\n"
            "- /kang - Save others' stickers\n\n"
            "Text to Sticker:\n"
            "- /quote2sticker - Reply to any message\n\n"
            "Need more help? Feel free to ask!"
        )
        await update.message.reply_text(help_text)

    async def stickerify(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Convert image to sticker format
        message = update.message
        
        # Check if it's a reply or direct image
        if message.photo:
            photo = message.photo[-1]
        elif message.reply_to_message and message.reply_to_message.photo:
            photo = message.reply_to_message.photo[-1]
        else:
            await message.reply_text(
                "Please send an image or reply to an image with /stickerify\n"
                "You can also just send any image directly and I'll convert it to a sticker!"
            )
            return

        # Download the photo
        photo_file = await context.bot.get_file(photo.file_id)
        photo_bytes = await photo_file.download_as_bytearray()
        
        # Process image
        with Image.open(BytesIO(photo_bytes)) as img:
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            
            img.thumbnail(self.max_sticker_size)
            
            output = BytesIO()
            img.save(output, format='WebP')
            output.seek(0)
            
            await message.reply_document(
                document=output,
                filename='sticker.webp',
                caption="Here's your sticker! Use /addsticker to add it to a pack."
            )

    async def add_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Add text to image with customization options
        message = update.message
        
        if not context.args:
            await message.reply_text(
                "Please use this format:\n"
                "1. Send an image\n"
                "2. Send: /addtext Your Text Here"
            )
            return
            
        # Check if it's a reply or direct image
        if message.photo:
            photo = message.photo[-1]
        elif message.reply_to_message and message.reply_to_message.photo:
            photo = message.reply_to_message.photo[-1]
        else:
            await message.reply_text(
                "Please send an image with /addtext Your Text\n"
                "Or reply to an image with /addtext Your Text"
            )
            return

        text = ' '.join(context.args)
        
        # Process image
        photo_file = await context.bot.get_file(photo.file_id)
        photo_bytes = await photo_file.download_as_bytearray()
        
        with Image.open(BytesIO(photo_bytes)) as img:
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            
            draw = ImageDraw.Draw(img)
            
            font_size = int(img.width * 0.1)
            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except:
                font = ImageFont.load_default()

            text_bbox = draw.textbbox((0, 0), text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            
            x = (img.width - text_width) // 2
            y = (img.height - text_height) // 2
            
            outline_color = 'black'
            text_color = 'white'
            outline_width = 2
            
            for adj in range(-outline_width, outline_width+1):
                for adj2 in range(-outline_width, outline_width+1):
                    draw.text((x+adj, y+adj2), text, font=font, fill=outline_color)
            
            draw.text((x, y), text, font=font, fill=text_color)
            
            output = BytesIO()
            img.save(output, format='WebP')
            output.seek(0)
            
            await message.reply_document(
                document=output,
                filename='text_sticker.webp',
                caption="Here's your sticker with text!"
            )

    async def handle_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Universal handler for photos, videos, GIFs, and stickers
        message = update.message
        
        if message.photo:
            # Handle photos
            await self.stickerify(update, context)
        elif message.sticker:
            # Handle stickers (both static and animated)
            await self.handle_sticker(update, context)
        elif message.animation or message.video:
            # Handle GIFs and videos
            await self.handle_animation(update, context)
        elif message.document:
            # Handle documents that might be GIFs or images
            mime_type = message.document.mime_type
            if mime_type in self.supported_image_types:
                await self.stickerify(update, context)
            elif mime_type in self.supported_animation_types:
                await self.handle_animation(update, context)

    async def handle_animation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Handle GIFs and videos
        message = update.message
        
        # Get the file
        if message.animation:
            file_id = message.animation.file_id
        elif message.video:
            file_id = message.video.file_id
        else:
            file_id = message.document.file_id
        
        await message.reply_text("Converting to animated sticker... Please wait.")
        
        try:
            # Download file
            file = await context.bot.get_file(file_id)
            
            with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_file:
                await file.download_to_drive(temp_file.name)
                temp_files.append(temp_file.name)
                
                # Convert to WebM
                clip = VideoFileClip(temp_file.name)
                
                # Ensure it meets Telegram's requirements
                if clip.duration > 3:
                    clip = clip.subclip(0, 3)
                
                # Resize if needed while maintaining aspect ratio
                if clip.size[0] > 512 or clip.size[1] > 512:
                    if clip.size[1] > clip.size[0]:
                        clip = clip.resize(height=512)
                        if clip.size[0] > 512:
                            clip = clip.resize(width=512)
                    else:
                        clip = clip.resize(width=512)
                        if clip.size[1] > 512:
                            clip = clip.resize(height=512)
                
                output_path = temp_file.name.replace('.mp4', '.webm')
                clip.write_videofile(output_path, codec='libvpx-vp9', audio=False)
                clip.close()
                
                # Send the sticker
                with open(output_path, 'rb') as webm_file:
                    await message.reply_document(
                        document=webm_file,
                        filename='animated_sticker.webm',
                        caption=(
                            "Here's your animated sticker!\n"
                            "Use /addsticker to add it to your pack or /createstickerpack to create a new pack."
                        )
                    )
                
                # Cleanup
                os.unlink(output_path)
                os.unlink(temp_file.name)
                temp_files.remove(temp_file.name)
        
        except Exception as e:
            await message.reply_text(f"Sorry, couldn't convert the animation: {str(e)}")

    async def handle_sticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Handle incoming stickers
        message = update.message
        sticker = message.sticker
        
        # Save the sticker information in user state
        user_id = message.from_user.id
        self.user_states[user_id] = {
            'last_sticker': sticker
        }
        
        # Different messages based on sticker type
        if sticker.is_animated:
            sticker_type = "animated sticker"
        elif sticker.is_video:
            sticker_type = "video sticker"
        else:
            sticker_type = "sticker"
            
        await message.reply_text(
            f"Nice {sticker_type}! You can:\n"
            "1. Use /kang to add it to your pack\n"
            "2. Use /createstickerpack to create a new pack\n"
            "3. Send me any other media (photo, GIF, video) to create more stickers!"
        )

    async def create_meme(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Create a meme with top and bottom text
        user_id = update.effective_user.id
        user_states[user_id] = {
            'waiting_for': 'top_text',
            'photo': None
        }
        
        await update.message.reply_text("Please send the image you want to make into a meme.")

    async def handle_meme_state(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Handle meme creation state machine
        user_id = update.effective_user.id
        state = user_states.get(user_id, {})
        
        if state.get('waiting_for') == 'top_text':
            if update.message.photo:
                state['photo'] = update.message.photo[-1].file_id
                state['waiting_for'] = 'bottom_text'
                await update.message.reply_text("Great! Now send me the top text for your meme.")
            else:
                await update.message.reply_text("Please send an image first.")
                
        elif state.get('waiting_for') == 'bottom_text':
            state['top_text'] = update.message.text
            state['waiting_for'] = 'processing'
            await update.message.reply_text("Perfect! Now send me the bottom text.")
            
        elif state.get('waiting_for') == 'processing':
            await self.generate_meme(
                update,
                context,
                state['photo'],
                state['top_text'],
                update.message.text
            )
            del user_states[user_id]

    async def generate_meme(self, update: Update, context: ContextTypes.DEFAULT_TYPE, photo_id: str, top_text: str, bottom_text: str):
        # Generate meme from image and texts
        photo_file = await context.bot.get_file(photo_id)
        photo_bytes = await photo_file.download_as_bytearray()
        
        with Image.open(BytesIO(photo_bytes)) as img:
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            
            draw = ImageDraw.Draw(img)
            
            # Calculate font size based on image size
            font_size = int(img.width * 0.15)  # Larger font for memes
            try:
                font = ImageFont.truetype("impact.ttf", font_size)
            except:
                try:
                    font = ImageFont.truetype("arial.ttf", font_size)
                except:
                    font = ImageFont.load_default()

            # Add top text
            top_text = top_text.upper()
            text_bbox = draw.textbbox((0, 0), top_text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            x = (img.width - text_width) // 2
            y = 10
            
            # Draw outline
            outline_color = 'black'
            text_color = 'white'
            outline_width = 3
            
            for adj in range(-outline_width, outline_width+1):
                for adj2 in range(-outline_width, outline_width+1):
                    draw.text((x+adj, y+adj2), top_text, font=font, fill=outline_color)
            draw.text((x, y), top_text, font=font, fill=text_color)

            # Add bottom text
            bottom_text = bottom_text.upper()
            text_bbox = draw.textbbox((0, 0), bottom_text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            x = (img.width - text_width) // 2
            y = img.height - text_height - 10
            
            for adj in range(-outline_width, outline_width+1):
                for adj2 in range(-outline_width, outline_width+1):
                    draw.text((x+adj, y+adj2), bottom_text, font=font, fill=outline_color)
            draw.text((x, y), bottom_text, font=font, fill=text_color)
            
            # Save as WebP
            output = BytesIO()
            img.save(output, format='WebP')
            output.seek(0)
            
            await update.message.reply_document(
                document=output,
                filename='meme.webp',
                caption="Here's your meme sticker!"
            )

    async def gif_to_sticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Convert GIF to animated sticker
        if not update.message.reply_to_message or not (
            update.message.reply_to_message.animation or 
            update.message.reply_to_message.video
        ):
            await update.message.reply_text("Please reply to a GIF or video with /gif2sticker")
            return

        # Get the file
        message = update.message.reply_to_message
        file_id = message.animation.file_id if message.animation else message.video.file_id
        
        # Download file
        file = await context.bot.get_file(file_id)
        
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_file:
            await file.download_to_drive(temp_file.name)
            temp_files.append(temp_file.name)
            
            # Convert to WebM
            clip = VideoFileClip(temp_file.name)
            
            # Ensure it meets Telegram's requirements
            if clip.duration > 3:
                clip = clip.subclip(0, 3)
            
            # Resize if needed
            if clip.size[0] > 512 or clip.size[1] > 512:
                clip = clip.resize(height=512) if clip.size[1] > clip.size[0] else clip.resize(width=512)
            
            output_path = temp_file.name.replace('.mp4', '.webm')
            clip.write_videofile(output_path, codec='libvpx-vp9', audio=False)
            clip.close()
            
            # Send the sticker
            with open(output_path, 'rb') as webm_file:
                await update.message.reply_document(
                    document=webm_file,
                    filename='animated_sticker.webm',
                    caption="Here's your animated sticker!"
                )
            
            # Cleanup
            os.unlink(output_path)
            os.unlink(temp_file.name)
            temp_files.remove(temp_file.name)

    async def create_sticker_pack(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Create a new sticker pack
        message = update.message
        user_id = message.from_user.id
        
        if not context.args:
            await message.reply_text(
                "Please provide a name for your sticker pack:\n"
                "/createstickerpack <pack_name>\n\n"
                "The bot supports:\n"
                "- Static stickers (from photos)\n"
                "- Animated stickers (from GIFs/videos)\n"
                "- Video stickers (from videos)"
            )
            return
        
        pack_name = f"{context.args[0]}_{user_id}_by_{context.bot.username}"
        pack_title = ' '.join(context.args)
        
        try:
            # Check if user has a sticker ready
            if user_id in self.user_states and 'last_sticker' in self.user_states[user_id]:
                sticker = self.user_states[user_id]['last_sticker']
                sticker_format = (
                    'animated' if sticker.is_animated else 
                    'video' if sticker.is_video else 
                    'static'
                )
            else:
                sticker_format = 'static'  # Default to static if no sticker is ready
            
            await context.bot.create_new_sticker_set(
                user_id,
                pack_name,
                pack_title,
                stickers=[],
                sticker_format=sticker_format
            )
            
            await message.reply_text(
                f"Sticker pack created successfully!\n"
                f"Pack name: {pack_name}\n\n"
                f"Now you can:\n"
                f"1. Send me any sticker to add with /kang\n"
                f"2. Send photos for static stickers\n"
                f"3. Send GIFs/videos for animated stickers"
            )
            
        except Exception as e:
            await message.reply_text(f"Failed to create sticker pack: {str(e)}")

    async def add_sticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add a sticker to an existing pack"""
        if not update.message.reply_to_message or not (
            update.message.reply_to_message.photo or
            update.message.reply_to_message.document
        ):
            await update.message.reply_text(
                "Please reply to an image or sticker with /addsticker"
            )
            return
        
        user = update.effective_user
        
        # Get user's sticker packs
        try:
            user_packs = await context.bot.get_user_sticker_sets(user.id)
            
            if not user_packs:
                await update.message.reply_text(
                    "You don't have any sticker packs. Create one first with /createstickerpack"
                )
                return
            
            # Create keyboard with pack options
            keyboard = []
            for pack in user_packs:
                keyboard.append([InlineKeyboardButton(
                    pack.title,
                    callback_data=f"add_to_pack:{pack.name}"
                )])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "Choose a sticker pack to add this sticker to:",
                reply_markup=reply_markup
            )
            
        except Exception as e:
            await update.message.reply_text(f"Error: {str(e)}")

    async def quote_to_sticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Convert text message to styled text sticker
        if not update.message.reply_to_message or not update.message.reply_to_message.text:
            await update.message.reply_text("Please reply to a text message with /quote2sticker")
            return
        
        text = update.message.reply_to_message.text
        
        # Create image with text
        font_size = 40
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except:
            font = ImageFont.load_default()
        
        # Wrap text
        max_width = 20
        wrapped_text = textwrap.fill(text, width=max_width)
        
        # Calculate image size
        padding = 20
        img = Image.new('RGBA', (512, 512), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        
        # Draw text
        text_bbox = draw.textbbox((0, 0), wrapped_text, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        
        x = (img.width - text_width) // 2
        y = (img.height - text_height) // 2
        
        # Add subtle background
        background_color = (0, 0, 0, 128)
        draw.rectangle([x-padding, y-padding, x+text_width+padding, y+text_height+padding],
                      fill=background_color)
        
        # Draw text
        draw.text((x, y), wrapped_text, font=font, fill='white')
        
        # Save as WebP
        output = BytesIO()
        img.save(output, format='WebP')
        output.seek(0)
        
        await update.message.reply_document(
            document=output,
            filename='quote.webp',
            caption="Here's your quote sticker!"
        )

    async def kang_sticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Save sticker to user's pack
        message = update.message
        user_id = message.from_user.id
        
        # Check if it's a reply to a sticker or using last seen sticker
        if message.reply_to_message and message.reply_to_message.sticker:
            sticker = message.reply_to_message.sticker
        elif user_id in self.user_states and 'last_sticker' in self.user_states[user_id]:
            sticker = self.user_states[user_id]['last_sticker']
        else:
            await message.reply_text(
                "Please either:\n"
                "1. Reply to a sticker with /kang\n"
                "2. Send a sticker first, then use /kang"
            )
            return
        
        try:
            user_packs = await context.bot.get_user_sticker_sets(user_id)
            
            if not user_packs:
                # Create new pack
                pack_name = f"pack_{user_id}_by_{context.bot.username}"
                await context.bot.create_new_sticker_set(
                    user_id,
                    pack_name,
                    f"{message.from_user.first_name}'s Sticker Pack",
                    stickers=[],
                    sticker_format='static' if not sticker.is_animated else 'animated'
                )
                user_packs = [pack_name]
            
            pack = user_packs[0]
            sticker_file = await context.bot.get_file(sticker.file_id)
            sticker_bytes = await sticker_file.download_as_bytearray()
            
            await context.bot.add_sticker_to_set(
                user_id,
                pack.name,
                sticker_bytes,
                '-'
            )
            
            await message.reply_text(
                f"Sticker successfully added to your pack!\n"
                f"Use /addsticker to add more stickers."
            )
            
        except Exception as e:
            await message.reply_text(f"Failed to add sticker: {str(e)}")

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Handle errors
        logger.error(f"Update {update} caused error {context.error}")
        await update.message.reply_text(
            "Sorry, an error occurred. Please try again later."
        )

def run_health_check_server():
    # Run the FastAPI server for health checks
    uvicorn.run(app, host="0.0.0.0", port=PORT)

def main():
    # Start the bot and health check server
    health_check_thread = threading.Thread(target=run_health_check_server)
    health_check_thread.daemon = True
    health_check_thread.start()

    # Create application and bot instance
    application = Application.builder().token(BOT_TOKEN).build()
    bot = StickerBot()
    
    # Add command handlers
    application.add_handler(CommandHandler('start', bot.start_command))
    application.add_handler(CommandHandler('help', bot.help_command))
    application.add_handler(CommandHandler('stickerify', bot.stickerify))
    application.add_handler(CommandHandler('addtext', bot.add_text))
    application.add_handler(CommandHandler('meme', bot.create_meme))
    application.add_handler(CommandHandler('gif2sticker', bot.gif_to_sticker))
    application.add_handler(CommandHandler('createstickerpack', bot.create_sticker_pack))
    application.add_handler(CommandHandler('addsticker', bot.add_sticker))
    application.add_handler(CommandHandler('quote2sticker', bot.quote_to_sticker))
    application.add_handler(CommandHandler('kang', bot.kang_sticker))
    
    # Add message handlers for direct interactions
    application.add_handler(MessageHandler(
        (filters.PHOTO | 
         filters.Sticker.ALL | 
         filters.Document.ALL | 
         filters.VIDEO | 
         filters.Document.ANIMATION),
        bot.handle_media
    ))
    
    # Add error handler
    application.add_error_handler(bot.error_handler)
    
    # Start the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main() 
