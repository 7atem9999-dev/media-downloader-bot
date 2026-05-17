import os
import logging
import sys

# DEBUG - remove after fixing
print("=== ENV DEBUG ===")
print(f"BOT_TOKEN exists: {'BOT_TOKEN' in os.environ}")
print(f"BOT_TOKEN value: '{os.getenv('BOT_TOKEN', 'NOT FOUND')}'")
print(f"All env keys: {list(os.environ.keys())}")
print("=================")
sys.stdout.flush()
from collections import OrderedDict
from urllib.parse import urlparse

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
DOWNLOAD_DIR = "downloads"
MAX_FILE_SIZE_BYTES = 49 * 1024 * 1024   # 49 MB (Telegram bot limit is 50 MB)
MAX_USER_CACHE = 500                      # Max entries in user_links dict

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Cookies setup
# ─────────────────────────────────────────────
COOKIES_FILE = "cookies.txt"

if os.getenv("YT_COOKIES"):
    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        f.write(cookies.strip())
    logger.info("Cookies file written from environment variable.")


# ─────────────────────────────────────────────
# Bounded user-link cache (prevents memory leak)
# ─────────────────────────────────────────────
class BoundedDict(OrderedDict):
    """OrderedDict that evicts the oldest entry when it exceeds max_size."""

    def __init__(self, max_size: int):
        super().__init__()
        self.max_size = max_size

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self.max_size:
            self.popitem(last=False)


user_links: BoundedDict = BoundedDict(max_size=MAX_USER_CACHE)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def is_valid_url(url: str) -> bool:
    """Return True only for http/https URLs with a real hostname."""
    try:
        result = urlparse(url.strip())
        return result.scheme in ("http", "https") and bool(result.netloc)
    except Exception:
        return False


def is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url


def _base_opts() -> dict:
    """Common yt-dlp options shared by all download functions."""
    opts = {
        "outtmpl": f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
       
    }
    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    return opts


def _resolve_filename(ydl: yt_dlp.YoutubeDL, info: dict, fallback_ext: str = "mp4") -> str:
    """
    Return the actual downloaded file path.
    Falls back to swapping the extension when the file isn't found at the
    exact path yt-dlp reports (common after post-processing).
    """
    filename = ydl.prepare_filename(info)
    if os.path.exists(filename):
        return filename

    swapped = os.path.splitext(filename)[0] + f".{fallback_ext}"
    if os.path.exists(swapped):
        return swapped

    # Last resort: find the most recently modified file in the download dir
    files = [
        os.path.join(DOWNLOAD_DIR, f)
        for f in os.listdir(DOWNLOAD_DIR)
    ]
    if files:
        return max(files, key=os.path.getmtime)

    raise FileNotFoundError(f"Downloaded file not found: {filename}")


def _check_size(file_path: str) -> None:
    """Raise ValueError if the file exceeds Telegram's upload limit."""
    size = os.path.getsize(file_path)
    if size > MAX_FILE_SIZE_BYTES:
        os.remove(file_path)
        raise ValueError(
            f"File is too large ({size / (1024*1024):.1f} MB). "
            "Telegram bots can only send files up to 49 MB."
        )


def _safe_remove(file_path: str) -> None:
    """Delete a file, silently ignoring errors."""
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except OSError as e:
        logger.warning("Could not remove %s: %s", file_path, e)


# ─────────────────────────────────────────────
# Download functions
# ─────────────────────────────────────────────
def download_generic_video(url: str) -> str:
    """Download a video from any yt-dlp-supported platform (non-YouTube)."""
    opts = _base_opts()
    opts["format"] = "best[ext=mp4]/best"

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return _resolve_filename(ydl, info, fallback_ext="mp4")


def download_youtube_video(url: str) -> str:
    """Download a YouTube video (≤720p, ≤49 MB)."""
    opts = _base_opts()
    opts["format"] = "bestvideo[height<=720]+bestaudio/best[height<=720]"
    opts["merge_output_format"] = "mp4"

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return _resolve_filename(ydl, info, fallback_ext="mp4")


def download_audio(url: str) -> str:
    """Extract audio from a YouTube URL and convert to MP3 @ 192 kbps."""
    opts = _base_opts()
    opts["format"] = "bestaudio/best"
    opts["postprocessors"] = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }
    ]

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return _resolve_filename(ydl, info, fallback_ext="mp3")


# ─────────────────────────────────────────────
# Command handlers
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Hello! Send me a video link and I'll download it for you.\n\n"
        "✅ Supported: YouTube, Instagram, TikTok, Twitter/X, Facebook, and more.\n"
        "🎥 YouTube links will let you choose between Video or MP3."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ℹ️ *How to use this bot:*\n\n"
        "1. Paste any video URL.\n"
        "2. For YouTube, pick *Video* or *MP3*.\n"
        "3. For other sites, the video downloads automatically.\n\n"
        "⚠️ Files larger than 49 MB cannot be sent via Telegram.",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
# Message handler
# ─────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = update.message.text.strip()
    user_id = update.message.from_user.id

    # Validate URL first
    if not is_valid_url(url):
        await update.message.reply_text(
            "⚠️ That doesn't look like a valid URL. "
            "Please send a link starting with http:// or https://"
        )
        return

    user_links[user_id] = url

    if is_youtube(url):
        keyboard = [
            [
                InlineKeyboardButton("🎥 Video (MP4)", callback_data="yt_video"),
                InlineKeyboardButton("🎵 Audio (MP3)", callback_data="yt_audio"),
            ]
        ]
        await update.message.reply_text(
            "🎬 YouTube link detected!\nWhat would you like to download?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # Non-YouTube: download immediately
    status_msg = await update.message.reply_text("⏳ Downloading, please wait...")
    file_path = None

    try:
        file_path = download_generic_video(url)
        _check_size(file_path)

        await status_msg.edit_text("📤 Uploading...")
        with open(file_path, "rb") as video_file:
            await update.message.reply_video(video=video_file)

        await status_msg.delete()

    except ValueError as e:
        await status_msg.edit_text(f"⚠️ {e}")
    except Exception as e:
        logger.exception("Error downloading %s for user %s", url, user_id)
        await status_msg.edit_text(f"❌ Download failed:\n{e}")
    finally:
        _safe_remove(file_path)


# ─────────────────────────────────────────────
# Callback (button) handler
# ─────────────────────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    url = user_links.get(user_id)
    if not url:
        await query.edit_message_text("⚠️ Session expired. Please send the link again.")
        return

    await query.edit_message_text("⏳ Downloading, please wait...")
    file_path = None

    try:
        if query.data == "yt_video":
            file_path = download_youtube_video(url)
            _check_size(file_path)
            await query.edit_message_text("📤 Uploading video...")
            with open(file_path, "rb") as video_file:
                await query.message.reply_video(video=video_file)

        elif query.data == "yt_audio":
            file_path = download_audio(url)
            _check_size(file_path)
            await query.edit_message_text("📤 Uploading audio...")
            with open(file_path, "rb") as audio_file:
                await query.message.reply_audio(audio=audio_file)

        await query.delete_message()

        # Clean up the cached URL after successful download
        user_links.pop(user_id, None)

    except ValueError as e:
        await query.edit_message_text(f"⚠️ {e}")
    except Exception as e:
        logger.exception("Error handling callback %s for user %s", query.data, user_id)
        await query.edit_message_text(f"❌ Download failed:\n{e}")
    finally:
        _safe_remove(file_path)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    TOKEN = "8747088201:AAEe24CHxDdW3DHX4U0iKJOE668MfxNAVZ8"
    if not TOKEN:
        raise ValueError(
            "BOT_TOKEN environment variable is not set. "
            "Add it to your Railway (or other host) environment variables."
        )

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot is running...")
    app.run_polling()
