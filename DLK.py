# DLK.py - Updated (2025-12-02)
# Repo file: https://github.com/gamingdhana49-dotcom/bot/blob/9f7bb96736bdccc270093b98aaf69c5d437c02c3/DLK.py
#
# Summary of fixes / changes:
# - When playing into a linked channel via /cplay or /cradio, the "Now Playing" UI message is posted into the group
#   that linked the channel (so the UI appears in the group, not inside the channel).
# - Added /cplend and /crend commands (channel-play end) to stop playback in the linked channel from the group.
# - When the track finishes (track_watcher), assistant leaves the voice chat and UI is updated in the correct UI chat.
# - Call resolution: when a linked channel is stored as a username (e.g. @DLKDEVELOPERS), the bot resolves it
#   to a numeric chat.id before attempting to start/join calls. This avoids call_py warnings for string ids.
# - Callbacks (pause/resume/skip/stop/delete) that come from UI chat (group) are mapped to the voice chat ID
#   using a helper, so controls operate on the actual voice call.
# - Minor robustness and logging improvements.
#
# Deploy notes:
# - Ensure ASSISTANT_SESSION is set and assistant account has been added to the target channel with permission to speak.
# - If you store channels as usernames (e.g. @mychannel) set_linked_channel keeps that, but runtime resolves to numeric id.
#
# Full file contents follow.

import os
import re
import time
import asyncio
import logging
import random
import inspect
from typing import Union, Optional, Dict, Any, List
from urllib.parse import urlparse, parse_qs
import subprocess
import json

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import RPCError, FloodWait
try:
    from pyrogram.errors import GroupcallForbidden
except ImportError:
    from pyrogram.errors.exceptions.forbidden_403 import Forbidden
    class GroupcallForbidden(Forbidden):
        pass
    import pyrogram.errors
    pyrogram.errors.GroupcallForbidden = GroupcallForbidden

from pyrogram.client import Client as _PyroClient
from pytgcalls import PyTgCalls
# Try to import MediaStream (older path)
try:
    from pytgcalls.types import MediaStream
except Exception:
    MediaStream = None

# Prefer AudioPiped if available
try:
    from pytgcalls.types.input_stream import AudioPiped
except Exception:
    AudioPiped = None

from dotenv import load_dotenv

try:
    import yt_dlp as youtube_dl
except Exception:
    youtube_dl = None

import aiohttp
import aiofiles
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

try:
    from youtubesearchpython.__future__ import VideosSearch
    VIDEOS_SEARCH_AVAILABLE = True
except Exception:
    VideosSearch = None
    VIDEOS_SEARCH_AVAILABLE = False

try:
    from pymongo import MongoClient
except Exception:
    MongoClient = None

import ntgcalls

load_dotenv()

# ---- Pyrogram "Peer id invalid" fix ----
_original_handle_updates = _PyroClient.handle_updates
async def _safe_handle_updates(self, updates):
    try:
        return await _original_handle_updates(self, updates)
    except ValueError as e:
        if str(e).startswith("Peer id invalid: -100"):
            logging.debug(f"Ignored invalid peer id in updates: {e}")
            return
        raise
_PyroClient.handle_updates = _safe_handle_updates
# ---------------------------

API_ID = int(os.environ.get("API_ID", "") or "")
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Read assistant session and normalise empty/whitespace -> None so Pyrogram does not try to use an empty string
ASSISTANT_SESSION = os.environ.get("ASSISTANT_SESSION")
if ASSISTANT_SESSION:
    ASSISTANT_SESSION = ASSISTANT_SESSION.strip() or None
else:
    ASSISTANT_SESSION = None

OWNER_ID = int(os.getenv("OWNER_ID", "") or "")

MONGO_URI = os.environ.get("MONGO_URI")
MONGO_DBNAME = os.environ.get("MONGO_DBNAME", "dlk_radio")
LOG_CHANNEL_ID = os.environ.get("LOG_CHANNEL_ID", "").strip()

YT_DLP_COOKIES = os.environ.get("YT_DLP_COOKIES")

DEV_LINK = "https://t.me/DLKDEVELOPERS"
SUPPORT_LINK = "https://t.me/DevDLK"

THUMB_CACHE_DIR = "cache"
os.makedirs(THUMB_CACHE_DIR, exist_ok=True)
DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# fallback duration for tracks without metadata
DEFAULT_FALLBACK_DURATION = 240  # 4 minutes

RADIO_STATION = {
    "SirasaFM": "http://live.trusl.com:1170/;",
    "Radio Plus Hitz": "https://altair.streamerr.co/stream/8054",
    "HiruFM": "https://radio.lotustechnologieslk.net:2020/stream/hirufmgarden?1707015384",
    "YFM": "http://live.trusl.com:1180/;",
    "Deep House Music": "http://live.dancemusic.ro:7000/",
    "HITZ FM": "https://stream-173.zeno.fm/uyx7eqengijtv",
    "ShreeFM": "https://207.148.74.192:7874/stream2.mp3",
    "ShaaFM": "https://radio.lotustechnologieslk.net:2020/stream/shaafmgarden",
    "eFM": "https://207.148.74.192:7874/stream",
    "Base Music": "https://base-music.stream.laut.fm/base-music",
    "Ultra Music Festival": "http://prem4.di.fm/umfradio_hi?20a1d1bf879e76&_ic2=1733161375677",
    "SunFM": "https://radio.lotustechnologieslk.net:2020/stream/sunfmgarden",
    "JAM FM": "http://stream.jam.fm/jamfm-nmr/mp3-192/",
}

# runtime state
radio_tasks: Dict[int, asyncio.Task] = {}
radio_paused = set()
# radio_state keyed by voice_chat_id (int). Each state contains ui_chat_id + msg_id etc.
radio_state: Dict[int, Dict[str, Any]] = {}
radio_queue: Dict[int, List[Dict[str, Any]]] = {}
track_watchers: Dict[int, asyncio.Task] = {}
bot_start_time = time.time()

BOT_USERNAME = None
ASSISTANT_USERNAME = None
ASSISTANT_ID = None

bot = Client("dlk_radio_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

assistant = Client(
    "assistant_account",
    session_string=ASSISTANT_SESSION,
    api_id=API_ID,
    api_hash=API_HASH,
)

call_py = PyTgCalls(assistant)

db_client = None
db = None

# linked channel mapping local fallback (if DB disabled)
linked_channels_local: Dict[int, Union[str, int]] = {}

# TRANSLATIONS (same as before - omitted here to keep file reasonable)

TRANSLATIONS = {
    "en": {
        "GROUP_BLOCKED": "❌ This group is blocked from using DLK BOT.",
        "ONLY_ADMINS": "Only admins can use this.",
        "ONLY_ADMINS_SKIP": "Only admins can skip tracks.",
        "ONLY_ADMINS_STOP": "Only admins can stop the playback!",
        "ONLY_ADMINS_RADIO_END": "Only admins can end the radio.",
        "ONLY_ADMINS_RADIO_SKIP": "Only admins can skip radio tracks.",
        "ONLY_ADMINS_RADIO_RESUME": "Only admins can resume the radio.",
        "ONLY_ADMINS_RADIO_BUTTON": "Only admins can control the radio!",
        "ONLY_OWNER_BLOCK": "Only the bot owner can block this group.",
        "ONLY_OWNER_UNBLOCK": "Only the bot owner can unblock this group.",
        "ONLY_OWNER_PANEL": "You are not authorized to view the panel.",
        "QUEUE_EMPTY": "Queue is empty.",
        "QUEUE_HEADER": "Upcoming queue:\n",
        "SKIPPED_NO_QUEUE": "⛔ Skipped. No more tracks in queue.",
        "SKIPPED_NO_QUEUE_RADIO": "⛔ Skipped. No more items in queue.",
        "BOT_STOPPED": "DLK bot stopped & cleaned up.",
        "RADIO_ENDED": "✅ Radio ended and assistant left the voice chat.",
        "FAILED_END_RADIO": "Failed to end the radio.",
        "ADDED_QUEUE": "➕ Added to queue: {title}",
        "ADDED_RADIO_QUEUE": "➕ Added to radio queue: {title}",
        "NOW_PLAYING": "▶️ Now playing: {title}",
        "NOW_PLAYING_QUEUE": "⏭️ Now playing: {title}",
        "PREPARING_AUDIO_REPLY": "Preparing your audio reply...",
        "PLAY_USAGE": "Usage: /play <YouTube url or search terms> OR reply to an audio/voice file and use /play",
        "SEARCHING_STREAM": "🔎 Searching and preparing stream...",
        "YTDLP_FAIL": "❌ Could not extract audio stream. Ensure yt-dlp is installed and cookies.txt set if needed.",
        "FAILED_PLAY_REQUEST": "❌ Failed to play the requested track.",
        "FAILED_PLAY_NEXT": "Failed to play next track: {title}",
        "FAILED_PLAY_NEXT_RADIO": "Failed to play next: {title}",
        "NOTHING_TO_RESUME": "Nothing to resume.",
        "RADIO_RESUMED": "▶️ Radio resumed.",
        "FAILED_RESUME": "Failed to resume the radio.",
        "GROUP_BLOCKED_OK": "✅ This group has been blocked from using DLK BOT.",
        "GROUP_UNBLOCKED_OK": "✅ This group has been unblocked.",
        "FAILED_BLOCK_GROUP": "Failed to block the group.",
        "FAILED_UNBLOCK_GROUP": "Failed to unblock the group.",
        "DB_NOT_CONFIGURED": "Database is not configured. Block list not available.",
        "BLOCK_LIST_EMPTY": "Blocked list is empty.",
        "BLOCK_LIST_HEADER": "Blocked groups:",
        "FAILED_FETCH_BLOCKS": "Failed to fetch blocked list.",
        "MUSIC_SKIP_BTN_NO_QUEUE": "⛔ Skipped. No more tracks in queue.",
        "MUSIC_SKIP_BTN_ALERT": "Skipped. No queue.",
        "MUSIC_SKIP_BTN_FAIL": "Failed to skip to next track.",
        "RADIO_NOTHING_PLAYING": "Nothing is playing.",
        "RADIO_PAUSED": "Paused.",
        "RADIO_PAUSE_FAIL": "Failed to pause the stream.",
        "RADIO_RESUMED_BTN": "Resumed.",
        "RADIO_RESUME_FAIL_BTN": "Failed to resume the stream.",
        "RADIO_STOPPED_BTN": "DLK BOT stopped!",
        "RADIO_STOP_FAIL_BTN": "Failed to stop bot.",
        "STATION_URL_NOT_FOUND": "Station URL not found!",
        "ASSISTANT_BLOCKED_GROUP": "This group is blocked from using DLK BOT.",
        "ASSISTANT_NOT_IN_GROUP": "Assistant is not in this group. Please add the assistant account and try again.",
        "ASSISTANT_INVITE_TEXT": "Assistant not in group. I've created an invite link — add the assistant account manually and give it permission to speak.",
        "ASSISTANT_JOIN_INFO": "🤖 Assistant has joined the group. Please grant it permission to manage voice chats and speak.",
        "ASSISTANT_INVITE_FAIL_TEXT": "Assistant is not in this group and I couldn't create an invite automatically. Please add the assistant account to the group and try again.",
        "ASSISTANT_INVITE_HELP_TEXT": (
            "How to add the assistant account:\n\n"
            "1. Open group info -> Administrators -> Add Administrator\n"
            "2. Search for the assistant account username (the bot created a session string).\n"
            "3. Add it and give it permission to manage voice chats and speak.\n\n"
            "If you used an invite link, use it to add the assistant and then re-run the command."
        ),
        "RADIO_CONNECTING": "🎧 Connecting to {station}...",
        "RATE_LIMIT": "⏳ Rate limit reached! Wait {seconds} seconds.",
        "VOICECHAT_NOT_READY": "❌ Cannot connect to voice chat! Ensure voice chat is active and assistant has permissions.",
        "RADIO_PLAY_FAILED_ASSIST": "Failed to play radio! Assistant error: {error}",
        "RADIO_START_FAIL": "❌ Failed to start radio! Error: {error}",
        "START_TEXT": (
            "👋 Welcome to DLK BOT!\n\n"
            "Commands (groups):\n"
            "- /radio : stations\n"
            "- /play <query|URL> or reply to audio with /play : play music\n"
            "- /cplay : play into linked channel\n"
            "- /cradio : radio into linked channel\n"
            "- /cplend /crend : stop linked channel playback\n"
            "- /pause /resume /stop /skip : playback controls (admins)\n\n"
            "Owner-only: /bl (block group), /unbl (unblock group)\n"
            "Use /lang to change the language."
        ),
        "HOME_TEXT": "👋 DLK BOT Home\n\nUse the buttons to navigate: Menu shows radio stations. Help explains commands.",
        "HELP_TEXT": (
            "DLK BOT help:\n"
            "- Use /play to play YouTube links or search terms.\n"
            "- Reply to an audio/file and use /play to play local audio.\n"
            "- Use /radio to open the radio stations menu.\n"
            "- Use /cplay to play into the linked channel (link with /conet).\n"
            "- Use /cradio to open radio menu for the linked channel.\n"
            "- Use /cplend or /crend to end playback in linked channel.\n"
            "- Use /rpush to add a station or url to the queue.\n"
            "- Use /rskip to skip to next queued station, /rend to end radio, /rresume to resume (admins only).\n"
            "- Admins can use pause/resume/skip/stop via the inline buttons.\n"
            "- Owner-only commands: /bl and /unbl in a group to block/unblock the group.\n"
            "- Use /lang to change bot language in this chat.\n"
        ),
    }
}

LANG_NAMES = {"en": "English 🇬🇧"}
DEFAULT_LANG = "en"

def get_chat_lang(chat_id: int) -> str:
    global db
    try:
        if db is None:
            return DEFAULT_LANG
        row = db.langs.find_one({"chat_id": chat_id})
        if not row:
            return DEFAULT_LANG
        lang = row.get("lang") or DEFAULT_LANG
        if lang not in TRANSLATIONS:
            return DEFAULT_LANG
        return lang
    except Exception:
        return DEFAULT_LANG

def set_chat_lang(chat_id: int, lang: str):
    global db
    if lang not in TRANSLATIONS or db is None:
        return
    try:
        db.langs.update_one(
            {"chat_id": chat_id},
            {"$set": {"chat_id": chat_id, "lang": lang, "ts": time.time()}},
            upsert=True,
        )
    except Exception as e:
        logging.warning(f"Failed to set language for chat {chat_id}: {e}")

def t(chat_id: int, key: str, **kwargs) -> str:
    lang = get_chat_lang(chat_id)
    text = TRANSLATIONS.get(lang, {}).get(key)
    if text is None:
        text = TRANSLATIONS.get(DEFAULT_LANG, {}).get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
    return text

def lang_keyboard(current: str) -> InlineKeyboardMarkup:
    buttons = []
    for code, name in LANG_NAMES.items():
        label = f"✅ {name}" if code == current else name
        buttons.append([InlineKeyboardButton(label, callback_data=f"set_lang_{code}")])
    return InlineKeyboardMarkup(buttons)

# ---------- UTIL ----------
def looks_like_url(text: str) -> bool:
    try:
        p = urlparse(text)
        return bool(p.scheme and p.netloc)
    except Exception:
        return False

def get_youtube_id(url: str) -> Optional[str]:
    try:
        p = urlparse(url)
        if "youtube" in p.netloc or "youtu.be" in p.netloc:
            if p.netloc.endswith("youtu.be"):
                return p.path.lstrip("/")
            qs = parse_qs(p.query)
            if "v" in qs:
                return qs["v"][0]
            match = re.search(r"/embed/([^/?&]+)", p.path)
            if match:
                return match.group(1)
    except Exception:
        pass
    return None

def is_ffmpeg_available() -> bool:
    try:
        res = subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        return res.returncode == 0
    except Exception:
        return False

def extract_audio_url(query: str) -> Optional[Dict[str, Any]]:
    if youtube_dl is None:
        logging.warning("yt_dlp not installed.")
        return None
    target = query if looks_like_url(query) else f"ytsearch1:{query}"
    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    if YT_DLP_COOKIES and os.path.isfile(YT_DLP_COOKIES):
        ydl_opts["cookiefile"] = YT_DLP_COOKIES
    try:
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target, download=False)
            if not info:
                return None
            if "entries" in info and isinstance(info["entries"], list) and info["entries"]:
                info = info["entries"][0]
            stream_url = info.get("url")
            if not stream_url and "formats" in info:
                formats = info.get("formats", [])
                best = None
                # Prefer audio-only formats with highest abr
                for f in sorted(formats, key=lambda x: (x.get("abr") or 0), reverse=True):
                    acodec = f.get("acodec") or ""
                    if acodec != "none" and f.get("url"):
                        best = f.get("url")
                        break
                stream_url = best or stream_url
            if not stream_url:
                logging.warning("yt_dlp: no stream_url")
                return None
            duration = info.get("duration") or info.get("original_duration")
            try:
                if duration is not None:
                    duration = int(duration)
            except Exception:
                duration = None
            return {
                "title": info.get("title") or "Unknown",
                "webpage_url": info.get("webpage_url") or info.get("id") or target,
                "stream_url": stream_url,
                "thumbnail": info.get("thumbnail"),
                "duration": duration,
            }
    except Exception as e:
        logging.warning(f"yt_dlp failed: {e}")
        return None

# ---------- THUMBNAILS ----------
def changeImageSize(maxWidth, maxHeight, image):
    widthRatio = maxWidth / image.size[0]
    heightRatio = maxHeight / image.size[1]
    newWidth = int(widthRatio * image.size[0])
    newHeight = int(heightRatio * image.size[1])
    return image.resize((newWidth, newHeight))

def clear_title(text: str) -> str:
    parts = (text or "").split(" ")
    title = ""
    for i in parts:
        if len(title) + len(i) < 60:
            title += " " + i
    return title.strip()

async def _download_file(url: str, dest: str) -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                f = await aiofiles.open(dest, mode="wb")
                await f.write(await resp.read())
                await f.close()
                return dest
    except Exception as e:
        logging.debug(f"_download_file failed: {e}")
        try:
            if os.path.exists(dest):
                os.remove(dest)
        except Exception:
            pass
        return None

def _create_circular_artwork(image: Image.Image, diameter: int = 520, border: int = 8) -> Image.Image:
    try:
        square = ImageOps.fit(image, (diameter, diameter), centering=(0.5, 0.5))
    except Exception:
        square = image.resize((diameter, diameter), Image.LANCZOS)
    mask = Image.new('L', (diameter, diameter), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, diameter, diameter), fill=255)
    circ = Image.new('RGBA', (diameter, diameter), (0, 0, 0, 0))
    circ.paste(square.convert('RGBA'), (0, 0), mask=mask)
    out_size = diameter + border * 2
    out = Image.new('RGBA', (out_size, out_size), (0, 0, 0, 0))
    shadow = Image.new('RGBA', (out_size, out_size), (0, 0, 0, 0))
    shadow_mask = Image.new('L', (out_size, out_size), 0)
    draw_sm = ImageDraw.Draw(shadow_mask)
    draw_sm.ellipse((border//2, border//2, out_size-border//2, out_size-border//2), fill=200)
    shadow.putalpha(shadow_mask)
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=6))
    out = Image.alpha_composite(out, shadow)
    border_layer = Image.new('RGBA', (out_size, out_size), (255, 255, 255, 0))
    draw_bl = ImageDraw.Draw(border_layer)
    draw_bl.ellipse((border, border, out_size-border, out_size-border), fill=(255, 255, 255, 255))
    inner_margin = border + 4
    draw_bl.ellipse((inner_margin, inner_margin, out_size-inner_margin, out_size-inner_margin), fill=(0, 0, 0, 0))
    out = Image.alpha_composite(out, border_layer)
    out.paste(circ, (border, border), circ)
    return out

async def _process_image_and_overlay(src_path: str, out_key: str, title: str) -> Optional[str]:
    try:
        image = Image.open(src_path).convert("RGBA")
        try:
            background = ImageOps.fit(image, (1280, 720), centering=(0.5, 0.5)).convert("RGBA")
        except Exception:
            background = image.resize((1280, 720), Image.LANCZOS).convert("RGBA")
        background = background.filter(ImageFilter.BoxBlur(8))
        enhancer = ImageEnhance.Brightness(background)
        background = enhancer.enhance(0.7)
        try:
            converter = ImageEnhance.Color(background)
            background = converter.enhance(0.25)
        except Exception:
            pass
        art = _create_circular_artwork(image, diameter=520, border=10)
        art_x = (1280 - art.size[0]) // 10
        art_y = (720 - art.size[1]) // 2
        background.paste(art, (art_x, art_y), art)
        draw = ImageDraw.Draw(background)
        try:
            title_font = ImageFont.truetype("arial.ttf", 48)
            small_font = ImageFont.truetype("arial.ttf", 18)
        except Exception:
            title_font = ImageFont.load_default()
            small_font = ImageFont.load_default()
        draw.text((20, 20), "DLK DEVELOPER", fill="white", font=small_font)
        title_x = art_x + art.size[0] + 30
        title_y = art_y + 30
        shadow_color = (0, 0, 0, 200)
        for dx, dy in ((1, 1), (2, 2)):
            draw.text((title_x+dx, title_y+dy), clear_title(title), fill=shadow_color, font=title_font)
        draw.text((title_x, title_y), clear_title(title), fill="white", font=title_font)
        out_path = os.path.join(THUMB_CACHE_DIR, f"{out_key}.png")
        background.save(out_path)
        return out_path
    except Exception as e:
        logging.debug(f"_process_image_and_overlay failed: {e}")
        return None

async def get_thumb_from_url_or_webpage(thumbnail_url: Optional[str], webpage: Optional[str], title: str) -> Optional[str]:
    if thumbnail_url:
        if os.path.isfile(thumbnail_url):
            key = re.sub(r"[^0-9A-Za-z_-]", "_", os.path.basename(thumbnail_url))[:40]
            return await _process_image_and_overlay(thumbnail_url, key, title)
        if thumbnail_url.startswith("http"):
            key = re.sub(r"[^0-9A-Za-z_-]", "_", thumbnail_url)[:40]
            tmp = os.path.join(THUMB_CACHE_DIR, f"tmp_{key}")
            downloaded = await _download_file(thumbnail_url, tmp)
            if downloaded:
                processed = await _process_image_and_overlay(downloaded, key, title)
                try:
                    os.remove(downloaded)
                except Exception:
                    pass
                return processed
    if webpage:
        vid_id = get_youtube_id(webpage) or re.sub(r"[^0-9A-Za-z_-]", "_", webpage)[:40]
        if VIDEOS_SEARCH_AVAILABLE and vid_id:
            try:
                url = f"https://www.youtube.com/watch?v={vid_id}"
                results = VideosSearch(url, limit=1)
                data = await results.next()
                entries = data.get("result", [])
                if entries:
                    thumb = entries[0].get("thumbnails", [{}])[0].get("url", "").split("?")[0]
                    if thumb:
                        return await get_thumb_from_url_or_webpage(thumb, None, title)
            except Exception:
                pass
        if youtube_dl is not None and vid_id:
            try:
                ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
                if YT_DLP_COOKIES and os.path.isfile(YT_DLP_COOKIES):
                    ydl_opts["cookiefile"] = YT_DLP_COOKIES
                with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid_id}", download=False)
                    thumb = info.get("thumbnail")
                    if thumb:
                        return await get_thumb_from_url_or_webpage(thumb, None, title)
            except Exception:
                pass
    return None

# ---------- DB / LOG ----------
def init_db_sync():
    global db_client, db
    if not MONGO_URI or MongoClient is None:
        logging.info("DB disabled.")
        return
    db_client = MongoClient(MONGO_URI)
    db = db_client[MONGO_DBNAME]
    db.blocked.create_index("chat_id")
    db.logs.create_index("ts")
    db.langs.create_index("chat_id", unique=True)
    db.linked_channels.create_index("group_id", unique=True)
    logging.info(f"Connected to MongoDB: {MONGO_DBNAME}")

def _valid_log_target(lid: str) -> bool:
    if not lid:
        return False
    if lid.startswith("@"):
        return True
    try:
        int(lid)
        return True
    except Exception:
        return False

def log_event_sync(event_type: str, data: dict):
    try:
        if db is not None:
            db.logs.insert_one({"ts": time.time(), "type": event_type, "data": data})
    except Exception as e:
        logging.warning(f"Failed to write log to DB: {e}")
    if not LOG_CHANNEL_ID or not _valid_log_target(LOG_CHANNEL_ID):
        return
    async def _send():
        try:
            target = LOG_CHANNEL_ID
            if not target.startswith("@"):
                target = int(target)
            await bot.send_message(
                target,
                f"🔔 <b>{event_type}</b>\n<pre>{data}</pre>",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logging.warning(f"Failed to send log to channel {LOG_CHANNEL_ID}: {e}")
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send())
    except RuntimeError:
        try:
            asyncio.get_event_loop().create_task(_send())
        except Exception as e:
            logging.warning(f"Failed to schedule log message: {e}")

def is_group_blocked_sync(chat_id: int) -> bool:
    if db is None:
        return False
    return db.blocked.find_one({"chat_id": chat_id}) is not None

def block_group_sync(chat_id: int, by_user: int, reason: Optional[str] = None):
    if db is None:
        return
    db.blocked.update_one(
        {"chat_id": chat_id},
        {"$set": {"chat_id": chat_id, "by": by_user, "reason": reason, "ts": time.time()}},
        upsert=True,
    )

def unblock_group_sync(chat_id: int):
    if db is None:
        return
    db.blocked.delete_one({"chat_id": chat_id})

# Linked channel helpers
def get_linked_channel(group_id: int) -> Optional[Union[int, str]]:
    try:
        if db is not None:
            row = db.linked_channels.find_one({"group_id": group_id})
            if row:
                return row.get("channel")
        return linked_channels_local.get(group_id)
    except Exception as e:
        logging.debug(f"get_linked_channel failed: {e}")
        return linked_channels_local.get(group_id)

def set_linked_channel(group_id: int, channel_identifier: Optional[Union[int, str]]):
    try:
        if db is not None:
            if channel_identifier is None:
                db.linked_channels.delete_one({"group_id": group_id})
            else:
                db.linked_channels.update_one(
                    {"group_id": group_id},
                    {"$set": {"group_id": group_id, "channel": channel_identifier, "ts": time.time()}},
                    upsert=True,
                )
            return
    except Exception as e:
        logging.warning(f"set_linked_channel db op failed: {e}")
    if channel_identifier is None:
        linked_channels_local.pop(group_id, None)
    else:
        linked_channels_local[group_id] = channel_identifier

def find_group_for_channel(channel_identifier: Union[int, str]) -> Optional[int]:
    try:
        if db is not None:
            row = db.linked_channels.find_one({"channel": channel_identifier})
            if row:
                return row.get("group_id")
        for g, ch in linked_channels_local.items():
            if str(ch) == str(channel_identifier):
                return g
    except Exception as e:
        logging.debug(f"find_group_for_channel failed: {e}")
    return None

async def dlk_privilege_validator(subject: Union[Message, CallbackQuery]) -> bool:
    try:
        if isinstance(subject, CallbackQuery):
            user = subject.from_user
            chat = subject.message.chat
            sender_chat = getattr(subject.message, "sender_chat", None)
        else:
            user = subject.from_user
            chat = subject.chat
            sender_chat = getattr(subject, "sender_chat", None)
        if user and user.id == OWNER_ID:
            return True
        if chat.type == "private":
            return False
        if user:
            try:
                member = await bot.get_chat_member(chat.id, user.id)
                status = getattr(member, "status", "").lower()
                if status in ("administrator", "creator"):
                    return True
            except Exception:
                pass
        if sender_chat:
            try:
                member = await bot.get_chat_member(chat.id, sender_chat.id)
                status = getattr(member, "status", "").lower()
                if status in ("administrator", "creator"):
                    return True
            except Exception:
                pass
        return False
    except Exception as e:
        logging.warning(f"Privilege check failed: {e}")
        return False

# helpers to map UI chat (where message/buttons are posted) -> voice chat id
def ui_to_voice_chat(ui_chat_id: int) -> Optional[int]:
    """
    Given a chat id where a player UI exists (group or channel), find the voice_chat_id
    that is currently playing for that UI. If UI is itself a voice chat id, return it.
    """
    try:
        # If voice chat is the same as UI (normal case), return directly if playing
        if ui_chat_id in radio_state:
            return ui_chat_id
        for voice_id, st in radio_state.items():
            try:
                if st.get("ui_chat_id") == ui_chat_id:
                    return voice_id
            except Exception:
                continue
    except Exception as e:
        logging.debug(f"ui_to_voice_chat failed: {e}")
    return None

# ---------- UI ----------
def radio_buttons(page: int = 0, per_page: int = 6):
    stations = sorted(RADIO_STATION.keys())
    total_pages = (len(stations) - 1) // per_page + 1
    start = page * per_page
    end = start + per_page
    current = stations[start:end]
    buttons = []
    for i in range(0, len(current), 2):
        row = [InlineKeyboardButton(current[i], callback_data=f"radio_play_{current[i]}")]
        if i + 1 < len(current):
            row.append(InlineKeyboardButton(current[i+1], callback_data=f"radio_play_{current[i+1]}"))
        buttons.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◁", callback_data=f"radio_page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▷", callback_data=f"radio_page_{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("❌ Close Menu", callback_data="radio_close")])
    return InlineKeyboardMarkup(buttons)

def player_controls_markup(ui_chat_id: int):
    if ui_chat_id in radio_paused:
        controls = [
            InlineKeyboardButton("▷", callback_data="radio_resume"),
            InlineKeyboardButton("‣‣I", callback_data="music_skip"),
            InlineKeyboardButton("▢", callback_data="radio_stop"),
            InlineKeyboardButton("❌", callback_data="player_delete"),
        ]
    else:
        controls = [
            InlineKeyboardButton("II", callback_data="radio_pause"),
            InlineKeyboardButton("‣‣I", callback_data="music_skip"),
            InlineKeyboardButton("▢", callback_data="radio_stop"),
            InlineKeyboardButton("❌", callback_data="player_delete"),
        ]
    bottom = [
        InlineKeyboardButton("👨‍💻 Dev", url=DEV_LINK),
        InlineKeyboardButton("💬 Support", url=SUPPORT_LINK),
    ]
    return InlineKeyboardMarkup([controls, bottom])

# ---------- Safe callback answer ----------
async def safe_query_answer(query: CallbackQuery, text: Optional[str] = None, show_alert: bool = False):
    try:
        if text is None:
            await query.answer()
        else:
            await query.answer(text, show_alert=show_alert)
    except RPCError as e:
        logging.debug(f"safe_query_answer ignored RPCError: {e}")
    except Exception as e:
        logging.debug(f"safe_query_answer failed: {e}")

# ---------- Helpers for introspecting call_py state ----------
async def dump_call_py_state(chat_id: Optional[int] = None) -> Dict[str, Any]:
    data = {"timestamp": time.time(), "chat_id": chat_id, "attrs": {}}
    attrs_to_check = ["active_calls", "_active_calls", "_group_calls", "group_calls", "calls", "_calls", "get_call", "get_active_call", "is_connected", "is_running", "running"]
    for name in attrs_to_check:
        try:
            attr = getattr(call_py, name, None)
            if attr is None:
                data["attrs"][name] = None
                continue
            if inspect.iscoroutinefunction(attr):
                try:
                    val = attr()
                    if inspect.isawaitable(val):
                        val = await val
                    data["attrs"][name] = repr(val)
                except Exception as e:
                    data["attrs"][name] = f"<coroutinefunction-call-failed: {e}>"
            elif inspect.isawaitable(attr):
                try:
                    val = await attr
                    data["attrs"][name] = repr(val)
                except Exception as e:
                    data["attrs"][name] = f"<awaitable-attr-failed: {e}>"
            elif callable(attr) and not isinstance(attr, (dict, list, tuple, set, str, bytes)):
                called = False
                try:
                    val = attr()
                    if inspect.isawaitable(val):
                        val = await val
                    data["attrs"][name] = repr(val)
                    called = True
                except Exception:
                    called = False
                if not called and chat_id is not None:
                    try:
                        val = attr(chat_id)
                        if inspect.isawaitable(val):
                            val = await val
                        data["attrs"][name] = repr(val)
                        called = True
                    except Exception as e:
                        data["attrs"][name] = f"<call-failed: {e}>"
                if not called:
                    try:
                        data["attrs"][name] = repr(attr)
                    except Exception:
                        data["attrs"][name] = "<unreprable>"
            else:
                try:
                    data["attrs"][name] = repr(attr)
                except Exception:
                    data["attrs"][name] = "<unreprable>"
        except Exception as e:
            data["attrs"][name] = f"<error: {e}>"
    return data

# ---------- TIMER / VC HELPERS ----------
async def update_radio_timer(voice_chat_id: int, ui_chat_id: int, msg_id: int, title: str, start_time: float, track_duration: int):
    while True:
        try:
            elapsed = max(0, int(time.time() - start_time))
            remaining = max(0, track_duration - elapsed)
            m, s = divmod(remaining, 60)
            timer = f"{m:02d}:{s:02d}"
            caption = f"🎧 Now Playing: {title}\n⏳ Duration: {timer}"
            await bot.edit_message_caption(
                chat_id=ui_chat_id,
                message_id=msg_id,
                caption=caption,
                reply_markup=player_controls_markup(ui_chat_id),
            )
            if remaining <= 0:
                break
        except Exception as e:
            logging.debug(f"Timer update failed for {voice_chat_id}/{msg_id}: {e}")
            break
        await asyncio.sleep(5)

async def _safe_call_py_method(method_name: str, *args, **kwargs):
    try:
        if not hasattr(call_py, method_name):
            return None
        attr = getattr(call_py, method_name)
        if not callable(attr):
            return None
        result = attr(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
    except Exception as e:
        logging.debug(f"_safe_call_py_method {method_name} failed: {e}")
        return None

# ---------- Async call-active detection (await awaitables) ----------
async def _is_call_active(chat_id: int) -> bool:
    try:
        for getter in ("get_call", "get_active_call"):
            attr = getattr(call_py, getter, None)
            if not attr:
                continue
            try:
                val = attr(chat_id)
                if inspect.isawaitable(val):
                    val = await val
                if val:
                    return True
            except Exception:
                continue

        attr_names = ("active_calls", "_active_calls", "_group_calls", "group_calls", "calls", "_calls")
        for attr_name in attr_names:
            ac = getattr(call_py, attr_name, None)
            if ac is None:
                continue
            if inspect.isawaitable(ac):
                try:
                    ac = await ac
                except Exception:
                    continue
            if inspect.iscoroutinefunction(ac):
                try:
                    res = ac()
                    if inspect.isawaitable(res):
                        res = await res
                    ac = res
                except Exception:
                    continue
            if callable(ac) and not isinstance(ac, (dict, list, tuple, set, str, bytes)):
                try:
                    sig = None
                    try:
                        sig = inspect.signature(ac)
                    except Exception:
                        sig = None
                    called = False
                    if sig is not None and len(sig.parameters) > 0:
                        try:
                            val = ac(chat_id)
                            if inspect.isawaitable(val):
                                val = await val
                            ac = val
                            called = True
                        except Exception:
                            called = False
                    if not called:
                        try:
                            val = ac()
                            if inspect.isawaitable(val):
                                val = await val
                            ac = val
                        except Exception:
                            pass
                except Exception:
                    pass
            if isinstance(ac, dict):
                try:
                    if chat_id in ac:
                        return True
                except Exception:
                    pass
                for v in ac.values():
                    try:
                        if getattr(v, "chat_id", None) == chat_id:
                            return True
                        if getattr(v, "peer_id", None) == chat_id:
                            return True
                    except Exception:
                        pass
            elif isinstance(ac, (list, tuple, set)):
                for item in ac:
                    try:
                        if getattr(item, "chat_id", None) == chat_id:
                            return True
                        if getattr(item, "peer_id", None) == chat_id:
                            return True
                    except Exception:
                        pass
            else:
                try:
                    if getattr(ac, "chat_id", None) == chat_id or getattr(ac, "peer_id", None) == chat_id:
                        return True
                    if hasattr(ac, "group_call") and getattr(ac.group_call, "chat_id", None) == chat_id:
                        return True
                except Exception:
                    pass

        for check in ("is_connected", "is_running", "running"):
            attr = getattr(call_py, check, None)
            if not attr:
                continue
            try:
                if callable(attr):
                    res = attr()
                    if inspect.isawaitable(res):
                        res = await res
                    if isinstance(res, bool) and res:
                        return True
                elif isinstance(attr, bool) and attr:
                    return True
            except Exception:
                pass
        return False
    except Exception:
        return False

async def _force_leave_call(chat_id: int):
    try:
        try:
            if hasattr(call_py, "leave_group_call"):
                res = call_py.leave_group_call(chat_id)
                if inspect.isawaitable(res):
                    await res
                logging.debug(f"_force_leave_call: leave_group_call used for {chat_id}")
                return
        except Exception as e:
            logging.debug(f"_force_leave_call leave_group_call failed {chat_id}: {e}")
        try:
            await _safe_call_py_method("leave_call", chat_id)
            logging.debug(f"_force_leave_call: leave_call fallback used for {chat_id}")
        except Exception as e2:
            logging.debug(f"_force_leave_call leave_call fallback failed {chat_id}: {e2}")
    except Exception as e:
        logging.debug(f"_force_leave_call failed totally: {e}")

async def leave_voice_chat(chat_id: int, cancel_watchers: bool = True):
    try:
        if chat_id in radio_tasks:
            radio_tasks[chat_id].cancel()
            radio_tasks.pop(chat_id, None)
        if cancel_watchers and chat_id in track_watchers:
            try:
                track_watchers[chat_id].cancel()
            except Exception:
                pass
            track_watchers.pop(chat_id, None)
        if chat_id in radio_paused:
            radio_paused.discard(chat_id)
        radio_state.pop(chat_id, None)
        try:
            await _force_leave_call(chat_id)
        except Exception as e:
            logging.debug(f"force leave vc failed {chat_id}: {e}")
    except Exception as e:
        logging.warning(f"leave_voice_chat failed {chat_id}: {e}")

def store_play_state(
    voice_chat_id: int,
    ui_chat_id: int,
    title: str,
    url: str,
    msg_id: int,
    start_time: Optional[float],
    elapsed: float = 0.0,
    paused: bool = False,
    duration: Optional[int] = None,
):
    state = {
        "voice_chat_id": voice_chat_id,
        "ui_chat_id": ui_chat_id,
        "station": title,
        "url": url,
        "msg_id": msg_id,
        "start_time": start_time,
        "elapsed": elapsed,
        "paused": paused,
        "duration": duration,
        "ts": time.time(),
    }
    radio_state[voice_chat_id] = state

# ---------- robust start stream ----------
async def _start_stream_in_call(chat_id: int, stream_source: str) -> bool:
    if not stream_source:
        logging.debug("_start_stream_in_call: no stream_source provided")
        return False

    ffmpeg_ok = is_ffmpeg_available()
    logging.debug(f"_start_stream_in_call: AudioPiped={'yes' if AudioPiped else 'no'}, MediaStream={'yes' if MediaStream else 'no'}, ffmpeg={'yes' if ffmpeg_ok else 'no'}")

    async def _try_and_verify(call_coro_callable):
        try:
            result = call_coro_callable()
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            logging.debug(f"_try_and_verify: call raised: {e}")
        for attempt in range(6):
            await asyncio.sleep(0.5)
            try:
                active = await _is_call_active(chat_id)
                logging.debug(f"_try_and_verify: attempt {attempt} active={active}")
                if active:
                    return True
            except Exception:
                pass
        return False

    # 1) AudioPiped (requires ffmpeg)
    if AudioPiped is not None and ffmpeg_ok:
        try:
            logging.debug(f"Trying AudioPiped for chat {chat_id} with source {stream_source}")
            audio_stream = None
            try:
                audio_stream = AudioPiped(stream_source)
            except Exception as e:
                logging.debug(f"AudioPiped(...) constructor failed: {e}; will try raw url")
                audio_stream = None
            methods_and_args = []
            for method_name in ("join_group_call", "join_call", "play", "play_stream", "start_playout", "start_stream", "start"):
                if audio_stream is not None:
                    methods_and_args.append((method_name, (chat_id, audio_stream), {}))
                    methods_and_args.append((method_name, (chat_id,), {"input_stream": audio_stream}))
                    methods_and_args.append((method_name, (chat_id,), {"audio_stream": audio_stream}))
                    methods_and_args.append((method_name, (chat_id,), {"stream": audio_stream}))
                methods_and_args.append((method_name, (chat_id, stream_source), {}))
                methods_and_args.append((method_name, (chat_id,), {"input_stream": stream_source}))
                methods_and_args.append((method_name, (chat_id,), {"audio_stream": stream_source}))
            for method_name, args, kwargs in methods_and_args:
                if not hasattr(call_py, method_name):
                    continue
                def make_call(method=method_name, a=args, kw=kwargs):
                    return getattr(call_py, method)(*a, **kw)
                logging.debug(f"AudioPiped: attempting {method_name} args={args} kwargs={kwargs}")
                ok = await _try_and_verify(make_call)
                logging.info(f"Attempted AudioPiped {method_name} for chat {chat_id}, verified={ok}")
                if ok:
                    logging.info(f"Stream started using AudioPiped via {method_name} for chat {chat_id}")
                    return True
        except Exception as e:
            logging.debug(f"AudioPiped attempt failed for chat {chat_id}: {e}")

    # 2) MediaStream
    if MediaStream is not None:
        try:
            logging.debug(f"Trying MediaStream for chat {chat_id} with source {stream_source}")
            try:
                ms = MediaStream(stream_source)
            except Exception as e:
                logging.debug(f"MediaStream(...) constructor failed: {e}; will try raw url")
                ms = None
            methods_and_args = []
            for method_name in ("join_group_call", "join_call", "play", "play_stream", "start_playout", "start_stream", "start"):
                if ms is not None:
                    methods_and_args.append((method_name, (chat_id, ms), {}))
                    methods_and_args.append((method_name, (chat_id,), {"input_stream": ms}))
                    methods_and_args.append((method_name, (chat_id,), {"media_stream": ms}))
                methods_and_args.append((method_name, (chat_id, stream_source), {}))
                methods_and_args.append((method_name, (chat_id,), {"input_stream": stream_source}))
            for method_name, args, kwargs in methods_and_args:
                if not hasattr(call_py, method_name):
                    continue
                def make_call(method=method_name, a=args, kw=kwargs):
                    return getattr(call_py, method)(*a, **kw)
                logging.debug(f"MediaStream: attempting {method_name} args={args} kwargs={kwargs}")
                ok = await _try_and_verify(make_call)
                logging.info(f"Attempted MediaStream {method_name} for chat {chat_id}, verified={ok}")
                if ok:
                    logging.info(f"Stream started using MediaStream via {method_name} for chat {chat_id}")
                    return True
        except Exception as e:
            logging.debug(f"MediaStream attempt failed for chat {chat_id}: {e}")

    # 3) raw safe calls
    candidates = [
        ("join_group_call", (chat_id, stream_source), {}),
        ("join_call", (chat_id, stream_source), {}),
        ("play", (chat_id, stream_source), {}),
        ("play_stream", (chat_id, stream_source), {}),
        ("start_playout", (chat_id, stream_source), {}),
        ("start_stream", (chat_id, stream_source), {}),
        ("start", (chat_id, stream_source), {}),
    ]
    for name, args, kwargs in candidates:
        if not hasattr(call_py, name):
            continue
        try:
            logging.debug(f"Attempting safe_call {name} with raw stream source")
            res = await _safe_call_py_method(name, *args, **kwargs)
            logging.info(f"Attempted safe_call {name} for chat {chat_id}, result={res}")
            for attempt in range(6):
                await asyncio.sleep(0.5)
                if await _is_call_active(chat_id):
                    logging.info(f"Stream started using safe_call {name} for chat {chat_id}")
                    return True
        except Exception as e:
            logging.debug(f"_safe_call_py_method {name} failed for chat {chat_id}: {e}")
            continue

    # ntgcalls fallback
    try:
        if hasattr(ntgcalls, "init") or hasattr(ntgcalls, "create"):
            logging.debug("Trying ntgcalls fallback attempts")
            for method_name in ("join_group_call", "join", "play"):
                if hasattr(call_py, method_name):
                    try:
                        res = getattr(call_py, method_name)(chat_id, stream_source)
                        if inspect.isawaitable(res):
                            await res
                    except Exception:
                        pass
                    await asyncio.sleep(0.8)
                    if await _is_call_active(chat_id):
                        logging.info(f"Stream started using ntgcalls fallback {method_name} for chat {chat_id}")
                        return True
    except Exception as e:
        logging.debug(f"ntgcalls fallback failed: {e}")

    # nothing worked: dump call_py internals for debugging
    try:
        state = await dump_call_py_state(chat_id)
        logging.warning(f"All attempts to start stream failed for chat {chat_id}. call_py state: {json.dumps(state, default=str)[:4000]}")
    except Exception as e:
        logging.warning(f"All attempts to start stream failed for chat {chat_id}. (failed to dump state: {e})")
    return False

# ---------- prepare_entry_from_reply ----------
async def prepare_entry_from_reply(reply_msg: Message) -> Optional[Dict[str, Any]]:
    try:
        media_field = None
        if reply_msg.voice:
            media_field = reply_msg.voice
        elif reply_msg.audio:
            media_field = reply_msg.audio
        elif reply_msg.document:
            media_field = reply_msg.document
        if media_field is None:
            return None
        ext = os.path.splitext(getattr(media_field, "file_name", "") or "")[1] or ""
        if not ext:
            mime = getattr(media_field, "mime_type", "") or ""
            if "ogg" in mime or "opus" in mime:
                ext = ".ogg"
            elif "mpeg" in mime or "mp3" in mime:
                ext = ".mp3"
            elif "wav" in mime:
                ext = ".wav"
            else:
                ext = ".raw"
        base_name = f"audio_{int(time.time())}_{random.randint(1000,9999)}"
        download_path = os.path.join(DOWNLOADS_DIR, base_name + ext)
        local_path = await bot.download_media(reply_msg, file_name=download_path)
        title = (
            getattr(media_field, "title", None)
            or getattr(media_field, "file_name", None)
            or reply_msg.caption
            or "Telegram Audio"
        )
        duration = getattr(media_field, "duration", None) or None
        thumb_path = None
        if reply_msg.photo:
            tmp_img = os.path.join(THUMB_CACHE_DIR, f"photo_{base_name}.jpg")
            thumb_path_local = await bot.download_media(reply_msg.photo, file_name=tmp_img)
            thumb_path = await _process_image_and_overlay(thumb_path_local, base_name, title)
            try:
                os.remove(thumb_path_local)
            except Exception:
                pass
        else:
            thumb_attr = getattr(media_field, "thumb", None)
            if thumb_attr:
                tmp_img = os.path.join(THUMB_CACHE_DIR, f"thumb_{base_name}.jpg")
                try:
                    thumb_local = await bot.download_media(thumb_attr, file_name=tmp_img)
                    thumb_path = await _process_image_and_overlay(thumb_local, base_name, title)
                    try:
                        os.remove(thumb_local)
                    except Exception:
                        pass
                except Exception:
                    thumb_path = None
        entry = {
            "title": title,
            "stream_url": local_path,
            "webpage": None,
            "thumbnail": thumb_path,
            "duration": duration,
            "is_local": True,
        }
        return entry
    except Exception as e:
        logging.debug(f"prepare_entry_from_reply failed: {e}")
        return None

# ---------- track_watcher ----------
async def track_watcher(voice_chat_id: int, duration: int, msg_id: int):
    try:
        await asyncio.sleep(max(1, duration) + 2)
        q = radio_queue.get(voice_chat_id, [])
        if q:
            next_entry = q.pop(0)
            radio_queue[voice_chat_id] = q
            await play_entry(voice_chat_id, next_entry)
            log_event_sync("music_auto_skipped", {"chat_id": voice_chat_id, "title": next_entry.get("title")})
        else:
            try:
                state = radio_state.get(voice_chat_id)
                ui_chat_id = state.get("ui_chat_id") if state else voice_chat_id
                try:
                    await leave_voice_chat(voice_chat_id, cancel_watchers=False)
                except Exception:
                    pass
                try:
                    await bot.edit_message_caption(
                        chat_id=ui_chat_id,
                        message_id=msg_id,
                        caption=t(ui_chat_id, "BOT_STOPPED"),
                        reply_markup=None,
                    )
                except Exception as e:
                    logging.debug(f"track_watcher edit caption failed {voice_chat_id}/{msg_id}: {e}")
            except Exception:
                pass
            log_event_sync("music_track_autostop", {"chat_id": voice_chat_id})
    except asyncio.CancelledError:
        return
    except Exception as e:
        logging.debug(f"track_watcher error {voice_chat_id}: {e}")

# ---------- play_entry ----------
async def play_entry(voice_chat_id: int, entry: dict, reply_message: Optional[Message] = None, ui_chat_id: Optional[int] = None):
    """
    voice_chat_id: numeric chat id where assistant should join the voice chat (int)
    ui_chat_id: chat id where the "Now Playing" message should be posted (group id). If None, defaults to voice_chat_id.
    """
    try:
        if voice_chat_id in radio_tasks:
            radio_tasks[voice_chat_id].cancel()
            radio_tasks.pop(voice_chat_id, None)

        stream_source = entry["stream_url"]
        # start stream in voice chat
        started = await _start_stream_in_call(voice_chat_id, stream_source)
        if not started:
            logging.error("Failed to start streaming in call for %s", voice_chat_id)
            return False

        thumb_path = None
        thumb_val = entry.get("thumbnail")
        title = entry.get("title") or "Unknown"
        if thumb_val and isinstance(thumb_val, str) and os.path.isfile(thumb_val):
            thumb_path = thumb_val
        else:
            if thumb_val and isinstance(thumb_val, str) and thumb_val.startswith("http"):
                thumb_path = await get_thumb_from_url_or_webpage(thumb_val, entry.get("webpage"), title)
            else:
                thumb_path = await get_thumb_from_url_or_webpage(None, entry.get("webpage"), title)

        # UI chat where the player will be shown
        ui_chat = ui_chat_id or voice_chat_id

        caption = f"🎧 {t(ui_chat, 'NOW_PLAYING', title=title)}"
        try:
            if thumb_path and os.path.isfile(thumb_path):
                msg = await bot.send_photo(
                    ui_chat,
                    photo=thumb_path,
                    caption=caption,
                    reply_markup=player_controls_markup(ui_chat),
                )
            else:
                msg = await bot.send_photo(
                    ui_chat,
                    photo="https://files.catbox.moe/08qhi9.jpg",
                    caption=caption,
                    reply_markup=player_controls_markup(ui_chat),
                )
        except Exception:
            # fallback: try without thumbnail
            try:
                msg = await bot.send_message(ui_chat, caption, reply_markup=player_controls_markup(ui_chat))
            except Exception:
                logging.debug("Failed to send player UI message")
                msg = None

        duration = entry.get("duration")
        try:
            if duration is not None:
                duration = int(duration)
        except Exception:
            duration = None
        if not duration or duration <= 0:
            duration = DEFAULT_FALLBACK_DURATION
        start_time = time.time()

        if msg:
            msg_id = msg.id
        else:
            msg_id = 0

        store_play_state(
            voice_chat_id,
            ui_chat,
            title,
            entry.get("stream_url"),
            msg_id,
            start_time,
            elapsed=0.0,
            paused=False,
            duration=duration,
        )

        radio_paused.discard(voice_chat_id)
        # schedule timer updates to UI (if we have a valid msg_id)
        if msg_id:
            radio_tasks[voice_chat_id] = asyncio.create_task(
                update_radio_timer(voice_chat_id, ui_chat, msg_id, title, start_time, duration)
            )
        if voice_chat_id in track_watchers:
            try:
                track_watchers[voice_chat_id].cancel()
            except Exception:
                pass
        track_watchers[voice_chat_id] = asyncio.create_task(track_watcher(voice_chat_id, duration, msg_id))
        log_event_sync("music_started", {"voice_chat_id": voice_chat_id, "ui_chat": ui_chat, "title": title})
        return True
    except Exception:
        logging.error("Play entry failed", exc_info=True)
        try:
            await leave_voice_chat(voice_chat_id)
        except Exception:
            pass
        return False

# ---------- /play ----------
@bot.on_message(filters.group & filters.command(["play", "p"]))
async def cmd_play(_, message: Message):
    chat_id = message.chat.id
    user = message.from_user
    if is_group_blocked_sync(chat_id):
        return await message.reply_text(t(chat_id, "GROUP_BLOCKED"))
    try:
        assistant_user = await assistant.get_me()
        assistant_id = assistant_user.id
    except Exception:
        assistant_id = None
    assistant_present = False
    if assistant_id:
        try:
            await assistant.get_chat_member(chat_id, assistant_id)
            assistant_present = True
        except RPCError:
            assistant_present = False
    if not assistant_present:
        if not ASSISTANT_SESSION:
            return await message.reply_text(
                "Assistant session is not configured (ASSISTANT_SESSION). Set it in Heroku config vars and restart the app."
            )
        try:
            invite = await bot.create_chat_invite_link(chat_id, member_limit=1, name="DLK BOT assistant")
            invite_link = invite.invite_link
            try:
                await assistant.join_chat(invite_link)
                assistant_present = True
                try:
                    await bot.send_message(chat_id, t(chat_id, "ASSISTANT_JOIN_INFO"), disable_web_page_preview=True)
                except Exception:
                    pass
            except Exception:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("📋 Invite Link", url=invite_link)]])
                await message.reply_text(t(chat_id, "ASSISTANT_INVITE_TEXT"), reply_markup=kb)
                return
        except Exception:
            return await message.reply_text(t(chat_id, "ASSISTANT_NOT_IN_GROUP"))
    entry = None
    info_msg = None
    if message.reply_to_message:
        entry = await prepare_entry_from_reply(message.reply_to_message)
        if entry:
            info_msg = await message.reply_text(t(chat_id, "PREPARING_AUDIO_REPLY"))
    if not entry:
        query = None
        if len(message.command) > 1:
            query = message.text.split(None, 1)[1]
        elif message.reply_to_message and message.reply_to_message.text:
            query = message.reply_to_message.text
        if not query:
            return await message.reply_text(t(chat_id, "PLAY_USAGE"))
        info_msg = await message.reply_text(t(chat_id, "SEARCHING_STREAM"))
        info = extract_audio_url(query)
        if info is None or not info.get("stream_url"):
            await info_msg.edit_text(t(chat_id, "YTDLP_FAIL"))
            return
        entry = {
            "title": info.get("title"),
            "stream_url": info.get("stream_url"),
            "webpage": info.get("webpage_url"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "is_local": False,
        }
    if chat_id not in radio_queue:
        radio_queue[chat_id] = []
    current_state = radio_state.get(chat_id)
    if current_state and not current_state.get("paused"):
        radio_queue[chat_id].append(entry)
        try:
            if info_msg:
                await info_msg.edit_text(t(chat_id, "ADDED_QUEUE", title=entry["title"]))
        except Exception:
            pass
        log_event_sync("music_queued", {"chat_id": chat_id, "title": entry["title"], "by": user.id})
        return
    ok = await play_entry(chat_id, entry, reply_message=message, ui_chat_id=chat_id)
    if ok:
        try:
            if info_msg:
                await info_msg.edit_text(t(chat_id, "NOW_PLAYING", title=entry["title"]))
        except Exception:
            pass
    else:
        if AudioPiped is not None and not is_ffmpeg_available():
            try:
                await message.reply_text(
                    "❌ Failed to play. ffmpeg not found in environment. "
                    "Install ffmpeg (on Heroku add ffmpeg buildpack) and restart. "
                    "Also ensure the assistant account has permission to manage voice chats and speak."
                )
            except Exception:
                pass
        try:
            if info_msg:
                await info_msg.edit_text(t(chat_id, "FAILED_PLAY_REQUEST"))
        except Exception:
            pass

# ---------- /conet (link group -> channel) ----------
@bot.on_message(filters.group & filters.command(["conet", "conlink"]))
async def cmd_conet(_, message: Message):
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text("Only admins can use this.")
    args = None
    if len(message.command) > 1:
        args = message.text.split(None, 1)[1].strip()
    if not args:
        cur = get_linked_channel(chat_id)
        if cur:
            return await message.reply_text(f"This group is linked to channel: {cur}\nUse /conet unlink to remove.")
        return await message.reply_text("Usage: /conet <@channelusername or -100...id> OR /conet unlink")
    if args.lower() in ("unlink", "remove", "none"):
        set_linked_channel(chat_id, None)
        await message.reply_text("✅ Unlinked channel from this group.")
        log_event_sync("conet_unlinked", {"group_id": chat_id, "by": message.from_user.id})
        return
    channel_ident = args
    if channel_ident.startswith("https://t.me/") or channel_ident.startswith("http://t.me/"):
        try:
            channel_ident = channel_ident.split("t.me/")[-1].strip("/")
            if channel_ident.isdigit():
                channel_ident = int(channel_ident)
            else:
                channel_ident = "@" + channel_ident
        except Exception:
            pass
    else:
        if re.match(r"^-?\d+$", channel_ident):
            try:
                channel_ident = int(channel_ident)
            except Exception:
                pass
        elif not channel_ident.startswith("@"):
            channel_ident = "@" + channel_ident
    set_linked_channel(chat_id, channel_ident)
    await message.reply_text(f"✅ Linked this group to channel: {channel_ident}")
    log_event_sync("conet_linked", {"group_id": chat_id, "channel": channel_ident, "by": message.from_user.id})

# ---------- /cplay (play into linked channel) ----------
@bot.on_message(filters.group & filters.command(["cplay", "cp"]))
async def cmd_cplay(_, message: Message):
    group_id = message.chat.id
    if is_group_blocked_sync(group_id):
        return await message.reply_text(t(group_id, "GROUP_BLOCKED"))
    channel_ident = get_linked_channel(group_id)
    if not channel_ident:
        return await message.reply_text("No channel linked. Use /conet <@channelusername or -100id> to link a channel.")
    # resolve numeric id (bot.get_chat handles @username or numeric)
    try:
        chat_obj = await bot.get_chat(channel_ident)
        voice_chat_id = chat_obj.id
    except Exception as e:
        logging.debug(f"Failed to resolve linked channel identifier {channel_ident}: {e}")
        return await message.reply_text("Failed to resolve linked channel. Ensure the channel exists and the bot has access.")

    # ensure assistant present in target channel
    try:
        assistant_user = await assistant.get_me()
        assistant_id = assistant_user.id
    except Exception:
        assistant_id = None
    assistant_present = False
    if assistant_id:
        try:
            await assistant.get_chat_member(voice_chat_id, assistant_id)
            assistant_present = True
        except RPCError:
            assistant_present = False
    if not assistant_present:
        invite_link = None
        try:
            invite = await bot.create_chat_invite_link(voice_chat_id, member_limit=1, name="DLK BOT assistant")
            invite_link = invite.invite_link
        except Exception:
            invite_link = None
        if invite_link:
            try:
                await assistant.join_chat(invite_link)
                assistant_present = True
                try:
                    await bot.send_message(group_id, t(group_id, "ASSISTANT_JOIN_INFO"), disable_web_page_preview=True)
                except Exception:
                    pass
            except Exception:
                pass
        if not assistant_present:
            kb = None
            if invite_link:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("📋 Invite Link", url=invite_link)]])
                await message.reply_text(
                    "Assistant is not present in the linked channel. I created an invite link – add the assistant to the channel and give it permission to speak.",
                    reply_markup=kb,
                )
            else:
                await message.reply_text(
                    "Assistant is not present in the linked channel. Please add the assistant account to the channel and give it permission to manage voice chats and speak."
                )
            return

    entry = None
    info_msg = None
    if message.reply_to_message:
        entry = await prepare_entry_from_reply(message.reply_to_message)
        if entry:
            info_msg = await message.reply_text("Preparing audio to play in linked channel...")
    if not entry:
        query = None
        if len(message.command) > 1:
            query = message.text.split(None, 1)[1]
        elif message.reply_to_message and message.reply_to_message.text:
            query = message.reply_to_message.text
        if not query:
            return await message.reply_text("Usage: /cplay <YouTube url or search terms> OR reply to audio in this group and use /cplay")
        info_msg = await message.reply_text("Searching and preparing stream for linked channel...")
        info = extract_audio_url(query)
        if info is None or not info.get("stream_url"):
            await info_msg.edit_text(t(group_id, "YTDLP_FAIL"))
            return
        entry = {
            "title": info.get("title"),
            "stream_url": info.get("stream_url"),
            "webpage": info.get("webpage_url"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "is_local": False,
        }

    if voice_chat_id not in radio_queue:
        radio_queue[voice_chat_id] = []
    current_state = radio_state.get(voice_chat_id)
    # ui_chat_id for this play should be the group that linked channel (so user sees UI in group)
    ui_chat_for_ui = group_id
    if current_state and not current_state.get("paused"):
        radio_queue[voice_chat_id].append(entry)
        try:
            if info_msg:
                await info_msg.edit_text(t(group_id, "ADDED_QUEUE", title=entry["title"]))
        except Exception:
            pass
        log_event_sync("cplay_queued", {"group_id": group_id, "channel": voice_chat_id, "title": entry["title"], "by": message.from_user.id})
        return
    ok = await play_entry(voice_chat_id, entry, reply_message=message, ui_chat_id=ui_chat_for_ui)
    if ok:
        try:
            if info_msg:
                await info_msg.edit_text(f"Now playing in channel (via assistant).")
        except Exception:
            pass
        await message.reply_text(f"Started playing in linked channel (via assistant): {entry['title']}")
        log_event_sync("cplay_started", {"group_id": group_id, "channel": voice_chat_id, "title": entry["title"], "by": message.from_user.id})
    else:
        try:
            if info_msg:
                await info_msg.edit_text(t(group_id, "FAILED_PLAY_REQUEST"))
        except Exception:
            pass
        await message.reply_text("Failed to start playback in linked channel. ❌ Failed to play the requested track.")

# ---------- /cradio (open radio menu to play into linked channel) ----------
@bot.on_message(filters.group & filters.command(["cradio"]))
async def cmd_cradio(_, message: Message):
    group_id = message.chat.id
    channel_ident = get_linked_channel(group_id)
    if not channel_ident:
        return await message.reply_text("No channel linked. Use /conet <@channelusername or -100id> to link a channel.")
    kb = radio_buttons(0)
    await message.reply_text(f"📻 Radio Stations - choose one to play in linked channel {channel_ident} (UI will be shown here):", reply_markup=kb)

# ---------- /cplend and /crend - end playback in linked channel ----------
@bot.on_message(filters.group & filters.command(["cplend", "crend"]))
async def cmd_cplend(_, message: Message):
    group_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text("Only admins can use this.")
    channel_ident = get_linked_channel(group_id)
    if not channel_ident:
        return await message.reply_text("No channel linked for this group.")
    try:
        chat_obj = await bot.get_chat(channel_ident)
        voice_chat_id = chat_obj.id
    except Exception as e:
        logging.debug(f"Failed to resolve linked channel for cplend: {e}")
        return await message.reply_text("Failed to resolve linked channel.")
    state = radio_state.get(voice_chat_id)
    ui_chat = state.get("ui_chat_id") if state else group_id
    msg_id = state.get("msg_id") if state else None
    await leave_voice_chat(voice_chat_id)
    if msg_id:
        try:
            await bot.edit_message_caption(chat_id=ui_chat, message_id=msg_id, caption=t(ui_chat, "BOT_STOPPED"), reply_markup=None)
        except Exception:
            pass
    await message.reply_text("Stopped playback in linked channel.")
    log_event_sync("cplend", {"group_id": group_id, "channel": voice_chat_id, "by": message.from_user.id})

# ---------- skip /queue /stop callbacks adapted to UI->voice mapping ----------
@bot.on_callback_query(filters.regex("^music_skip$"))
async def cb_music_skip(_, query: CallbackQuery):
    ui_chat = query.message.chat.id
    voice_chat = ui_to_voice_chat(ui_chat) or ui_chat
    if not await dlk_privilege_validator(query):
        return await safe_query_answer(query, t(ui_chat, "ONLY_ADMINS_SKIP"), show_alert=True)
    q = radio_queue.get(voice_chat, [])
    if not q:
        await leave_voice_chat(voice_chat)
        try:
            await query.message.edit_caption(
                caption=t(ui_chat, "MUSIC_SKIP_BTN_NO_QUEUE"),
                reply_markup=None,
            )
        except Exception:
            pass
        await safe_query_answer(query, t(ui_chat, "MUSIC_SKIP_BTN_ALERT"), show_alert=True)
        log_event_sync("music_skipped_stop", {"chat_id": voice_chat, "by": query.from_user.id if query.from_user else None})
        return
    next_entry = q.pop(0)
    radio_queue[voice_chat] = q
    if voice_chat in track_watchers:
        try:
            track_watchers[voice_chat].cancel()
        except Exception:
            pass
        track_watchers.pop(voice_chat, None)
    ok = await play_entry(voice_chat, next_entry, ui_chat_id=ui_chat)
    if ok:
        try:
            await query.message.edit_caption(
                caption=t(ui_chat, "NOW_PLAYING_QUEUE", title=next_entry["title"]),
                reply_markup=player_controls_markup(ui_chat),
            )
        except Exception:
            pass
        await safe_query_answer(query, t(ui_chat, "MUSIC_SKIP_BTN_ALERT"), show_alert=False)
        log_event_sync("music_skipped", {"chat_id": voice_chat, "title": next_entry["title"], "by": query.from_user.id if query.from_user else None})
    else:
        await safe_query_answer(query, t(ui_chat, "MUSIC_SKIP_BTN_FAIL"), show_alert=True)

@bot.on_callback_query(filters.regex("^radio_pause$"))
async def radio_pause_cb(_, query: CallbackQuery):
    ui_chat = query.message.chat.id
    voice_chat = ui_to_voice_chat(ui_chat) or ui_chat
    if not await dlk_privilege_validator(query):
        return await safe_query_answer(query, t(ui_chat, "ONLY_ADMINS_RADIO_BUTTON"), show_alert=True)
    state = radio_state.get(voice_chat)
    if not state:
        return await safe_query_answer(query, t(ui_chat, "RADIO_NOTHING_PLAYING"), show_alert=True)
    try:
        await _safe_call_py_method("pause_stream", voice_chat)
        await _safe_call_py_method("pause", voice_chat)
        start_time = state.get("start_time") or time.time()
        elapsed = time.time() - start_time if start_time else state.get("elapsed", 0.0)
        state["paused"] = True
        state["elapsed"] = elapsed
        state["start_time"] = None
        radio_paused.add(voice_chat)
        store_play_state(
            voice_chat,
            state.get("ui_chat_id") or ui_chat,
            state.get("station"),
            state.get("url"),
            state.get("msg_id"),
            None,
            elapsed=elapsed,
            paused=True,
            duration=state.get("duration"),
        )
        try:
            await query.message.edit_reply_markup(reply_markup=player_controls_markup(ui_chat))
        except Exception:
            pass
        await safe_query_answer(query, t(ui_chat, "RADIO_PAUSED"), show_alert=False)
        log_event_sync("radio_paused", {"chat_id": voice_chat, "by": query.from_user.id if query.from_user else None})
    except Exception as e:
        logging.debug(f"Pause failed: {e}")
        await safe_query_answer(query, t(ui_chat, "RADIO_PAUSE_FAIL"), show_alert=True)

@bot.on_callback_query(filters.regex("^radio_resume$"))
async def radio_resume_cb(_, query: CallbackQuery):
    ui_chat = query.message.chat.id
    voice_chat = ui_to_voice_chat(ui_chat) or ui_chat
    if not await dlk_privilege_validator(query):
        return await safe_query_answer(query, t(ui_chat, "ONLY_ADMINS_RADIO_BUTTON"), show_alert=True)
    state = radio_state.get(voice_chat)
    if not state:
        return await safe_query_answer(query, t(ui_chat, "NOTHING_TO_RESUME_BTN"), show_alert=True)
    try:
        await _safe_call_py_method("resume_stream", voice_chat)
        await _safe_call_py_method("resume", voice_chat)
        elapsed = state.get("elapsed", 0.0) or 0.0
        start_time = time.time() - elapsed
        state["paused"] = False
        state["elapsed"] = 0.0
        state["start_time"] = start_time
        radio_paused.discard(voice_chat)
        duration = state.get("duration")
        store_play_state(
            voice_chat,
            state.get("ui_chat_id") or ui_chat,
            state.get("station"),
            state.get("url"),
            state.get("msg_id"),
            start_time,
            elapsed=0.0,
            paused=False,
            duration=duration,
        )
        if duration is not None:
            if voice_chat in radio_tasks:
                try:
                    radio_tasks[voice_chat].cancel()
                except Exception:
                    pass
                radio_tasks.pop(voice_chat, None)
            radio_tasks[voice_chat] = asyncio.create_task(
                update_radio_timer(voice_chat, state.get("ui_chat_id") or ui_chat, state.get("msg_id"), state.get("station"), start_time, duration)
            )
        try:
            await query.message.edit_reply_markup(reply_markup=player_controls_markup(ui_chat))
        except Exception:
            pass
        await safe_query_answer(query, t(ui_chat, "RADIO_RESUMED_BTN"), show_alert=False)
        log_event_sync("radio_resumed", {"chat_id": voice_chat, "by": query.from_user.id if query.from_user else None})
    except Exception as e:
        logging.debug(f"Resume failed: {e}")
        await safe_query_answer(query, t(ui_chat, "RADIO_RESUME_FAIL_BTN"), show_alert=True)

@bot.on_callback_query(filters.regex("^radio_stop$"))
async def cb_radio_stop(_, query: CallbackQuery):
    ui_chat = query.message.chat.id
    voice_chat = ui_to_voice_chat(ui_chat) or ui_chat
    if not await dlk_privilege_validator(query):
        return await safe_query_answer(query, t(ui_chat, "ONLY_ADMINS_RADIO_BUTTON"), show_alert=True)
    try:
        await leave_voice_chat(voice_chat)
        try:
            await query.message.delete()
        except Exception:
            try:
                await query.message.edit_caption(
                    caption=t(ui_chat, "RADIO_STOPPED_BTN"),
                    reply_markup=None,
                )
            except Exception:
                pass
        await safe_query_answer(query, t(ui_chat, "RADIO_STOPPED_BTN"), show_alert=False)
        log_event_sync("radio_stopped", {"chat_id": voice_chat, "by": query.from_user.id if query.from_user else None})
    except Exception as e:
        logging.error(f"Stop failed via callback: {e}", exc_info=True)
        await safe_query_answer(query, t(ui_chat, "RADIO_STOP_FAIL_BTN"), show_alert=True)

# ---------- DELETE (❌) button handler ----------
@bot.on_callback_query(filters.regex("^player_delete$"))
async def cb_player_delete(_, query: CallbackQuery):
    ui_chat = query.message.chat.id
    user = query.from_user
    voice_chat = ui_to_voice_chat(ui_chat) or ui_chat
    # find associated group for permission check
    group_id = find_group_for_channel(voice_chat)
    allowed = False
    if user and user.id == OWNER_ID:
        allowed = True
    elif group_id:
        try:
            member = await bot.get_chat_member(group_id, user.id)
            status = getattr(member, "status", "").lower()
            if status in ("administrator", "creator"):
                allowed = True
        except Exception:
            allowed = False
    else:
        allowed = (user and user.id == OWNER_ID)
    if not allowed:
        await safe_query_answer(query, "Only the linked group admins or owner can delete this.", show_alert=True)
        return
    try:
        await leave_voice_chat(voice_chat)
    except Exception:
        pass
    try:
        await query.message.delete()
    except Exception:
        try:
            await query.message.edit_caption(caption=t(ui_chat, "BOT_STOPPED"), reply_markup=None)
        except Exception:
            pass
    await safe_query_answer(query, "Deleted and stopped.")
    log_event_sync("player_deleted", {"voice_chat": voice_chat, "ui_chat": ui_chat, "by": user.id if user else None})

# ---------- RADIO BUTTON PLAY ----------
@bot.on_callback_query(filters.regex("^radio_play_"))
async def play_radio_station(_, query: CallbackQuery):
    ui_chat = query.message.chat.id
    station = query.data.replace("radio_play_", "")
    url = RADIO_STATION.get(station)
    user = query.from_user
    # If this UI belongs to a group which linked a channel, play into the linked channel and show UI in the group.
    linked = get_linked_channel(ui_chat)
    if linked:
        try:
            chat_obj = await bot.get_chat(linked)
            voice_chat = chat_obj.id
        except Exception as e:
            logging.debug(f"Failed to resolve linked channel for radio_play: {e}")
            await safe_query_answer(query, "Failed to resolve linked channel.", show_alert=True)
            return
        ui_chat_for_ui = ui_chat
    else:
        # no linked channel: treat UI chat as the voice chat
        voice_chat = ui_chat
        ui_chat_for_ui = ui_chat

    if is_group_blocked_sync(voice_chat):
        await safe_query_answer(query, t(ui_chat_for_ui, "ASSISTANT_BLOCKED_GROUP"), show_alert=True)
        return
    if not url:
        return await safe_query_answer(query, t(ui_chat_for_ui, "STATION_URL_NOT_FOUND"), show_alert=True)
    try:
        try:
            assistant_user = await assistant.get_me()
            assistant_id = assistant_user.id
        except Exception:
            assistant_id = None
        assistant_present = False
        if assistant_id:
            try:
                await assistant.get_chat_member(voice_chat, assistant_id)
                assistant_present = True
            except RPCError:
                assistant_present = False
        if not assistant_present:
            if not ASSISTANT_SESSION:
                await safe_query_answer(query, "Assistant session is not configured. Set ASSISTANT_SESSION and restart.", show_alert=True)
                return
            try:
                invite = await bot.create_chat_invite_link(voice_chat, member_limit=1, name="DLK BOT assistant")
                invite_link = invite.invite_link
                try:
                    await assistant.join_chat(invite_link)
                    assistant_present = True
                    try:
                        await bot.send_message(ui_chat_for_ui, t(ui_chat_for_ui, "ASSISTANT_JOIN_INFO"), disable_web_page_preview=True)
                    except Exception:
                        pass
                except Exception as e_join:
                    logging.warning(f"Assistant failed to join via invite: {e_join}")
                    assistant_present = False
                    help_kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("📋 Invite Link", url=invite_link)],
                        [InlineKeyboardButton("ℹ️ How to add assistant", callback_data="assistant_invite_help")],
                        [InlineKeyboardButton("❌ Dismiss", callback_data="radio_close")],
                    ])
                    await query.message.reply_text(
                        t(ui_chat_for_ui, "ASSISTANT_INVITE_TEXT"),
                        reply_markup=help_kb,
                    )
                    await safe_query_answer(query)
                    return
            except Exception as e_inv:
                logging.warning(f"Cannot create invite/join assistant: {e_inv}")
                await query.message.reply_text(t(ui_chat_for_ui, "ASSISTANT_INVITE_FAIL_TEXT"))
                await safe_query_answer(query)
                return
        # start the stream robustly
        started = await _start_stream_in_call(voice_chat, url)
        if not started:
            await leave_voice_chat(voice_chat)
            if AudioPiped is not None and not is_ffmpeg_available():
                await query.message.reply_text(
                    "❌ Failed to start radio: ffmpeg is not installed in the environment. "
                    "Install ffmpeg (on Heroku add ffmpeg buildpack) and restart the bot. "
                    "Also ensure the assistant account is present and has permission to speak."
                )
            else:
                await query.message.reply_text(t(ui_chat_for_ui, "RADIO_PLAY_FAILED_ASSIST", error="assistant failed to start stream"))
            await safe_query_answer(query, "Failed to start radio", show_alert=True)
            return

        msg = await query.message.edit_caption(
            caption=f"🎧 {station}\n🔴 LIVE Radio",
            reply_markup=player_controls_markup(ui_chat_for_ui),
        )
        start_time = time.time()
        store_play_state(voice_chat, ui_chat_for_ui, station, url, msg.id, start_time, elapsed=0.0, paused=False, duration=None)
        radio_paused.discard(voice_chat)
        await safe_query_answer(query, f"Now playing {station} via assistant!", show_alert=False)
        log_event_sync("radio_started", {"voice_chat": voice_chat, "station": station, "by": user.id if user else None})
    except FloodWait as e:
        await leave_voice_chat(voice_chat)
        wait_time = getattr(e, "value", None) or getattr(e, "x", None) or "unknown"
        await query.message.reply_text(t(ui_chat, "RATE_LIMIT", seconds=wait_time))
        await safe_query_answer(query, f"Wait {wait_time}s", show_alert=True)
    except ntgcalls.TelegramServerError:
        await leave_voice_chat(voice_chat)
        await query.message.reply_text(t(ui_chat_for_ui, "VOICECHAT_NOT_READY"))
        await safe_query_answer(query, "Voice chat not ready!", show_alert=True)
    except RPCError as e:
        await leave_voice_chat(voice_chat)
        await query.message.reply_text(t(ui_chat_for_ui, "RADIO_PLAY_FAILED_ASSIST", error=str(e)))
        await safe_query_answer(query)
    except Exception as e:
        await leave_voice_chat(voice_chat)
        logging.error("General radio play error", exc_info=True)
        await query.message.reply_text(t(ui_chat_for_ui, "RADIO_START_FAIL", error=str(e)))
        await safe_query_answer(query)

# ---------- START / HELP / LANG ----------
@bot.on_message(filters.command(["start"]) & filters.private)
async def start_private(_, message: Message):
    chat_id = message.chat.id
    text = t(chat_id, "START_TEXT")
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏠 Home", callback_data="home"),
            InlineKeyboardButton("❓ Help", callback_data="help_info"),
        ],
        [
            InlineKeyboardButton("📻 Menu", callback_data="radio_page_0"),
            InlineKeyboardButton("🌐 Language", callback_data="open_lang_menu"),
        ],
        [
            InlineKeyboardButton("👨‍💻 Dev", url=DEV_LINK),
            InlineKeyboardButton("💬 Support", url=SUPPORT_LINK),
        ],
    ])
    await message.reply_text(text, reply_markup=kb)

@bot.on_callback_query(filters.regex("^home$"))
async def cb_home(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    text = t(chat_id, "HOME_TEXT")
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📻 Menu", callback_data="radio_page_0"),
            InlineKeyboardButton("❓ Help", callback_data="help_info"),
        ],
        [
            InlineKeyboardButton("🌐 Language", callback_data="open_lang_menu"),
        ],
        [
            InlineKeyboardButton("👨‍💻 Dev", url=DEV_LINK),
            InlineKeyboardButton("💬 Support", url=SUPPORT_LINK),
        ],
    ])
    await safe_query_answer(query)
    try:
        await query.message.edit_text(text, reply_markup=kb)
    except Exception:
        await query.message.reply_text(text, reply_markup=kb)

@bot.on_callback_query(filters.regex("^assistant_invite_help$"))
async def assistant_invite_help(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    help_text = t(chat_id, "ASSISTANT_INVITE_HELP_TEXT")
    await safe_query_answer(query)
    await query.message.reply_text(help_text)

@bot.on_callback_query(filters.regex("^help_info$"))
async def cb_help_info(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    help_text = t(chat_id, "HELP_TEXT")
    await safe_query_answer(query)
    await query.message.reply_text(help_text)

@bot.on_message(filters.group & filters.command(["lang", "setlang"]))
async def cmd_set_language_group(_, message: Message):
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text(t(chat_id, "ONLY_ADMINS"))
    current = get_chat_lang(chat_id)
    text = (
        t(chat_id, "LANG_MENU_TITLE")
        + "\n\n"
        + t(chat_id, "CHOOSE_LANG")
        + "\n"
        + t(chat_id, "LANG_CURRENT", lang_name=LANG_NAMES.get(current, current))
    )
    await message.reply_text(text, reply_markup=lang_keyboard(current))

@bot.on_message(filters.private & filters.command(["lang", "setlang"]))
async def cmd_set_language_pm(_, message: Message):
    chat_id = message.chat.id
    current = get_chat_lang(chat_id)
    text = (
        t(chat_id, "LANG_MENU_TITLE")
        + "\n\n"
        + t(chat_id, "CHOOSE_LANG")
        + "\n"
        + t(chat_id, "LANG_CURRENT", lang_name=LANG_NAMES.get(current, current))
    )
    await message.reply_text(text, reply_markup=lang_keyboard(current))

@bot.on_callback_query(filters.regex(r"^set_lang_(.+)$"))
async def cb_set_language(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    lang_code = query.data.split("_", 2)[-1]
    if lang_code not in LANG_NAMES:
        await safe_query_answer(query, t(chat_id, "UNKNOWN_LANG"), show_alert=True)
        return
    set_chat_lang(chat_id, lang_code)
    current = lang_code
    text = (
        t(chat_id, "LANG_CHANGED", lang_name=LANG_NAMES[lang_code])
        + "\n\n"
        + t(chat_id, "LANG_CURRENT", lang_name=LANG_NAMES[lang_code])
    )
    try:
        await query.message.edit_text(text, reply_markup=lang_keyboard(current))
    except Exception:
        await query.message.reply_text(text, reply_markup=lang_keyboard(current))
    await safe_query_answer(query)

@bot.on_callback_query(filters.regex("^open_lang_menu$"))
async def cb_open_lang_menu(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    current = get_chat_lang(chat_id)
    text = (
        t(chat_id, "LANG_MENU_TITLE")
        + "\n\n"
        + t(chat_id, "CHOOSE_LANG")
        + "\n"
        + t(chat_id, "LANG_CURRENT", lang_name=LANG_NAMES.get(current, current))
    )
    await safe_query_answer(query)
    try:
        await query.message.edit_text(text, reply_markup=lang_keyboard(current))
    except Exception:
        await query.message.reply_text(text, reply_markup=lang_keyboard(current))

# ---------- RADIO MENU PAGE / CLOSE ----------
@bot.on_callback_query(filters.regex(r"^radio_page_(\d+)$"))
async def cb_radio_page(_, query: CallbackQuery):
    try:
        m = re.match(r"radio_page_(\d+)", query.data)
        if not m:
            await safe_query_answer(query)
            return
        page = int(m.group(1))
        kb = radio_buttons(page)
        try:
            await query.message.edit_text("📻 Radio Stations - choose one:", reply_markup=kb)
        except Exception:
            try:
                await query.message.edit_reply_markup(reply_markup=kb)
            except Exception:
                pass
        await safe_query_answer(query)
    except Exception as e:
        logging.debug(f"radio_page handler failed: {e}")
        try:
            await safe_query_answer(query, "Failed to load page.", show_alert=True)
        except Exception:
            pass

@bot.on_callback_query(filters.regex(r"^radio_close$"))
async def cb_radio_close(_, query: CallbackQuery):
    try:
        try:
            await query.message.delete()
        except Exception:
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        await safe_query_answer(query)
    except Exception as e:
        logging.debug(f"radio_close handler failed: {e}")
        try:
            await safe_query_answer(query, "Failed to close menu.", show_alert=True)
        except Exception:
            pass

# ---------- OWNER debug command ----------
@bot.on_message(filters.private & filters.command(["debug_call_status"]))
async def cmd_debug_call_status(_, message: Message):
    chat_id = message.chat.id
    if not message.from_user or message.from_user.id != OWNER_ID:
        return await message.reply_text("Only owner can use this.")
    try:
        state = await dump_call_py_state(None)
        txt = json.dumps(state, default=str, indent=2)[:3500]
        await message.reply_text(f"call_py internals:\n<pre>{txt}</pre>", disable_web_page_preview=True)
    except Exception as e:
        await message.reply_text(f"Failed to dump call_py state: {e}")

# ---------- MAIN ----------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.info("Starting DLK Bot...")

    try:
        init_db_sync()
    except Exception as e:
        logger.warning(f"Database initialization failed: {e}")

    assistant_started = False
    if ASSISTANT_SESSION:
        try:
            assistant.start()
            assistant_started = True
            logger.info("Assistant (user) client started.")
        except Exception as e:
            logger.warning(f"Assistant start failed: {e}")
            assistant_started = False
    else:
        logger.warning("ASSISTANT_SESSION is not set - assistant (user) client will not be started. Voice features disabled.")

    if assistant_started:
        try:
            call_py.start()
            logger.info("PyTgCalls started.")
        except Exception as e:
            logger.warning(f"PyTgCalls start failed: {e}")

    logger.info(f"AudioPiped available: {'yes' if AudioPiped else 'no'}; MediaStream available: {'yes' if MediaStream else 'no'}; ffmpeg present: {'yes' if is_ffmpeg_available() else 'no'}")

    bot.start()

    try:
        if assistant_started:
            me = assistant.get_me()
            ASSISTANT_USERNAME = me.username
            ASSISTANT_ID = me.id
        else:
            ASSISTANT_USERNAME = "assistant"
            ASSISTANT_ID = None
    except Exception:
        ASSISTANT_USERNAME = "assistant"
        ASSISTANT_ID = None

    try:
        bot_me = bot.get_me()
        BOT_USERNAME = bot_me.username
    except Exception:
        BOT_USERNAME = None

    log_event_sync("bot_started", {"ts": time.time(), "owner": OWNER_ID})

    from pyrogram import idle
    try:
        idle()
    finally:
        try:
            if assistant_started:
                try:
                    call_py.stop()
                except Exception:
                    pass
                try:
                    assistant.stop()
                except Exception:
                    pass
            bot.stop()
        except Exception:
            pass
