import os

# Create cookies file from environment variable
if os.getenv("YT_COOKIES"):
    with open("cookies.txt", "w", encoding="utf-8") as f:
        f.write(os.getenv("YT_COOKIES"))

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Store user links
user_links = {}

# ✅ Detect YouTube link
def is_youtube(url):
    return "youtube.com" in url or "youtu.be" in url


# ✅ Generic video downloader (all platforms)
def download_video(url):
    ydl_opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s',
        'format': 'best[ext=mp4]',
        'noplaylist': True,
               'quiet': True
    }

# ✅ Add cookies only
    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = 'cookies.txt'

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)

        if not os.path.exists(filename):
            filename = os.path.splitext(filename)[0] + ".mp4"

        return filename


# ✅ YouTube video downloader
def download_youtube_video(url):
    ydl_opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s',
        'format': 'best[height<=720][filesize<50M]',
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'quiet': True
    }

    
# ✅ Add cookies only if exist
    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = 'cookies.txt'

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)

        if not os.path.exists(filename):
            filename = os.path.splitext(filename)[0] + ".mp4"

        return filename


# ✅ YouTube audio (MP3)
def download_audio(url):
    ydl_opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s',
        'format': 'bestaudio',
             'noplaylist': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True
    }


# ✅ Add cookies only if exist
    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = 'cookies.txt'

    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
     
        filename = os.path.splitext(ydl.prepare_filename(info))[0] + ".mp3"
        return filename


# ✅ Handle incoming messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    user_id = update.message.from_user.id

    user_links[user_id] = url

    # ✅ If YouTube → show options
    if is_youtube(url):
        keyboard = [
            [
                InlineKeyboardButton("🎥 Video", callback_data="yt_video"),
                InlineKeyboardButton("🎵 MP3", callback_data="yt_audio")
            ]
        ]

        await update.message.reply_text(
            "Choose YouTube download type:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    else:
        # ✅ Other platforms → download directly
        await update.message.reply_text("Downloading video...")

        try:
            file_path = download_video(url)

            await update.message.reply_video(video=open(file_path, "rb"))
            os.remove(file_path)

        except Exception as e:
            await update.message.reply_text(f"Error: {str(e)}")


# ✅ Handle buttons
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    url = user_links.get(user_id)

    if not url:
        await query.edit_message_text("Send link again.")
        return

    await query.edit_message_text("Downloading...")

    try:
        if query.data == "yt_video":
            file_path = download_youtube_video(url)
            await query.message.reply_video(video=open(file_path, "rb"))

        elif query.data == "yt_audio":
            file_path = download_audio(url)
            await query.message.reply_audio(audio=open(file_path, "rb"))

        os.remove(file_path)

    except Exception as e:
        await query.message.reply_text(f"Error: {str(e)}")


# ✅ Start bot
if __name__ == "__main__":
   
    TOKEN = os.getenv("BOT_TOKEN") # Railway env variable
    
 

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("Bot is running...")
    app.run_polling()
