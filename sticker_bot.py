"""
# requirements.txt:
# python-telegram-bot==20.7
# Pillow==10.1.0
# moviepy==1.0.3
# python-dotenv==1.0.0

import os
import tempfile
import logging
from typing import Dict, List
from datetime import datetime
import asyncio
from io import BytesIO
import textwrap

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

# Global variables for user states and temporary data
user_states: Dict[int, dict] = {}
temp_files: List[str] = []

class StickerBot:
    def __init__(self):
        """Initialize the bot with necessary configurations"""
        self.supported_image_types = {'image/jpeg', 'image/png', 'image/webp'}
        self.supported_animation_types = {'video/mp4', 'image/gif'}
        self.max_sticker_size = (512, 512)
        self.max_file_size = 50 * 1024 * 1024  # 50MB limit

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle the /start command"""
        welcome_message = """
🎨 Welcome to the Advanced Sticker Maker Bot! 🎨

Here's what I can do for you:

/stickerify - Convert any image to a Telegram sticker
/addtext - Add custom text to your image
/meme - Create a meme with top/bottom text
/gif2sticker - Convert GIF to animated sticker
/kang - Save any sticker to your pack
/createstickerpack - Create a new sticker pack
/addsticker - Add sticker to existing pack
/quote2sticker - Convert text to styled sticker
/help - Show detailed instructions

Send me any image, GIF, or video to get started! 🚀
"""
        await update.message.reply_text(welcome_message)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle the /help command"""
        help_text = """
📖 Detailed Usage Instructions:

🖼 Image to Sticker:
1. Send any image
2. Use /stickerify to convert it
3. Choose your pack to add it

✍️ Adding Text:
1. Send an image
2. Use /addtext followed by your text
3. Choose position and style

🎭 Creating Memes:
1. Send an image
2. Use /meme
3. Send top text
4. Send bottom text

🎬 GIF to Sticker:
1. Send a GIF/short video
2. Use /gif2sticker
3. Wait for conversion

📦 Sticker Pack Management:
- /createstickerpack - Create new pack
- /addsticker - Add to existing pack
- /kang - Save others' stickers

💭 Text to Sticker:
- /quote2sticker - Reply to any message

Need more help? Feel free to ask! 😊
"""
        await update.message.reply_text(help_text)

    async def stickerify(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Convert image to sticker format"""
        if not update.message.reply_to_message or not update.message.reply_to_message.photo:
            await update.message.reply_text("Please reply to an image with /stickerify")
            return

        photo = update.message.reply_to_message.photo[-1]
        
        # Download the photo
        photo_file = await context.bot.get_file(photo.file_id)
        photo_bytes = await photo_file.download_as_bytearray()
        
        # Process image
        with Image.open(BytesIO(photo_bytes)) as img:
            # Convert to RGBA
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            
            # Resize maintaining aspect ratio
            img.thumbnail(self.max_sticker_size)
            
            # Save as WebP
            output = BytesIO()
            img.save(output, format='WebP')
            output.seek(0)
            
            # Send as document to preserve as sticker
            await update.message.reply_document(
                document=output,
                filename='sticker.webp',
                caption="Here's your sticker! Use /addsticker to add it to a pack."
            )

    async def add_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add text to image with customization options"""
        if not context.args:
            await update.message.reply_text("Please provide the text after /addtext")
            return
            
        if not update.message.reply_to_message or not update.message.reply_to_message.photo:
            await update.message.reply_text("Please reply to an image with /addtext")
            return

        text = ' '.join(context.args)
        photo = update.message.reply_to_message.photo[-1]
        
        # Download the photo
        photo_file = await context.bot.get_file(photo.file_id)
        photo_bytes = await photo_file.download_as_bytearray()
        
        # Process image
        with Image.open(BytesIO(photo_bytes)) as img:
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            
            # Create draw object
            draw = ImageDraw.Draw(img)
            
            # Calculate font size based on image size
            font_size = int(img.width * 0.1)
            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except:
                font = ImageFont.load_default()

            # Calculate text position (centered)
            text_bbox = draw.textbbox((0, 0), text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            
            x = (img.width - text_width) // 2
            y = (img.height - text_height) // 2
            
            # Add text with outline
            outline_color = 'black'
            text_color = 'white'
            outline_width = 2
            
            # Draw outline
            for adj in range(-outline_width, outline_width+1):
                for adj2 in range(-outline_width, outline_width+1):
                    draw.text((x+adj, y+adj2), text, font=font, fill=outline_color)
            
            # Draw main text
            draw.text((x, y), text, font=font, fill=text_color)
            
            # Save as WebP
            output = BytesIO()
            img.save(output, format='WebP')
            output.seek(0)
            
            await update.message.reply_document(
                document=output,
                filename='text_sticker.webp',
                caption="Here's your sticker with text!"
            )

    async def create_meme(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Create a meme with top and bottom text"""
        # Store user state
        user_id = update.effective_user.id
        user_states[user_id] = {
            'waiting_for': 'top_text',
            'photo': None
        }
        
        await update.message.reply_text("Please send the image you want to make into a meme.")

    async def handle_meme_state(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle meme creation state machine"""
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
        """Generate meme from image and texts"""
        # Download the photo
        photo_file = await context.bot.get_file(photo_id)
        photo_bytes = await photo_file.download_as_bytearray()
        
        with Image.open(BytesIO(photo_bytes)) as img:
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            
            draw = ImageDraw.Draw(img)
            
            # Calculate font size
            font_size = int(img.width * 0.1)
            try:
                font = ImageFont.truetype("impact.ttf", font_size)
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
            outline_width = 2
            
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
        """Convert GIF to animated sticker"""
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
        """Create a new sticker pack"""
        user = update.effective_user
        
        if not context.args:
            await update.message.reply_text(
                "Please provide a name for your sticker pack:\n"
                "/createstickerpack <pack_name>"
            )
            return
        
        pack_name = f"{context.args[0]}_{user.id}_by_{context.bot.username}"
        pack_title = ' '.join(context.args)
        
        try:
            # Create pack with a placeholder sticker
            await context.bot.create_new_sticker_set(
                user.id,
                pack_name,
                pack_title,
                stickers=[],
                sticker_format='static'
            )
            
            await update.message.reply_text(
                f"Sticker pack created successfully!\n"
                f"Pack name: {pack_name}\n"
                f"Use /addsticker to add stickers to this pack."
            )
            
        except Exception as e:
            await update.message.reply_text(f"Failed to create sticker pack: {str(e)}")

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
        """Convert text message to styled text sticker"""
        if not update.message.reply_to_message or not update.message.reply_to_message.text:
            await update.message.reply_text("Please reply to a text message with /quote2sticker")
            return
        
        text = update.message.reply_to_message.text
        
        # Create image with text
        # Calculate image size based on text length
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
        """Save someone else's sticker to own pack"""
        if not update.message.reply_to_message or not update.message.reply_to_message.sticker:
            await update.message.reply_text("Please reply to a sticker with /kang")
            return
        
        sticker = update.message.reply_to_message.sticker
        user = update.effective_user
        
        # Download sticker
        sticker_file = await context.bot.get_file(sticker.file_id)
        sticker_bytes = await sticker_file.download_as_bytearray()
        
        # Get user's sticker packs or create new one
        try:
            user_packs = await context.bot.get_user_sticker_sets(user.id)
            
            if not user_packs:
                # Create new pack
                pack_name = f"kang_pack_{user.id}_by_{context.bot.username}"
                await context.bot.create_new_sticker_set(
                    user.id,
                    pack_name,
                    f"{user.first_name}'s Kanged Stickers",
                    stickers=[],
                    sticker_format='static' if not sticker.is_animated else 'animated'
                )
                user_packs = [pack_name]
            
            # Add to first pack
            pack = user_packs[0]
            await context.bot.add_sticker_to_set(
                user.id,
                pack.name,
                sticker_bytes,
                '👍'
            )
            
            await update.message.reply_text(
                f"Sticker successfully kanged to pack: {pack.title}"
            )
            
        except Exception as e:
            await update.message.reply_text(f"Failed to kang sticker: {str(e)}")

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Update {update} caused error {context.error}")
        await update.message.reply_text(
            "Sorry, an error occurred. Please try again later."
        )

def main():
    """Start the bot"""
    # Create application and bot instance
    application = Application.builder().token(BOT_TOKEN).build()
    bot = StickerBot()
    
    # Add handlers
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
    
    # Add error handler
    application.add_error_handler(bot.error_handler)
    
    # Start the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main() 