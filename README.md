# Advanced Telegram Sticker Maker Bot 🎨

A feature-rich Telegram bot that helps users create and manage stickers from various media types. Built with python-telegram-bot and various image processing libraries.

## Features 🚀

- Convert images to stickers (`/stickerify`)
- Add custom text to images (`/addtext`)
- Create memes with top/bottom text (`/meme`)
- Convert GIFs to animated stickers (`/gif2sticker`)
- Save stickers from other users (`/kang`)
- Create and manage sticker packs (`/createstickerpack`, `/addsticker`)
- Convert text messages to styled stickers (`/quote2sticker`)

## Setup 🛠️

1. Clone this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create a `.env` file with your bot token:
   ```
   BOT_TOKEN=your_bot_token_here
   ```
4. Run the bot:
   ```bash
   python sticker_bot.py
   ```

## Deployment on Railway.app 🚂

1. Fork this repository
2. Create a new project on Railway.app
3. Connect your forked repository
4. Add the following environment variable:
   - `BOT_TOKEN`: Your Telegram bot token from @BotFather
5. Deploy!

## Requirements 📋

- Python 3.7+
- FFmpeg (for GIF/video processing)
- Dependencies listed in requirements.txt

## Usage 📱

1. Start a chat with your bot on Telegram
2. Send `/start` to see available commands
3. Send any image, GIF, or video to begin creating stickers
4. Use commands like `/stickerify`, `/addtext`, etc. to modify and create stickers
5. Create your own sticker pack with `/createstickerpack`

## Contributing 🤝

Feel free to open issues and pull requests for any improvements or bug fixes.

## License 📄

MIT License 