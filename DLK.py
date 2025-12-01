# -*- coding: utf-8 -*-
# DLK.py - updated 2025-12-01
# This is a revised full copy of the bot file with robustness fixes for modern Pyrogram/PyTgCalls usage,
# better coroutine handling, clearer startup checks, improved logging, and defensive environment validation.
# The structure and features follow the original repository but include:
# - Safe environment variable parsing and helpful startup errors.
# - _call_maybe_await helper used everywhere a method may be coroutine.
# - Safer PyTgCalls start/stop handling and multiple fallback play methods.
# - Awaiting pyrogram.idle() correctly.
# - Extra logging for troubleshooting commands-not-working problems on Heroku.
# - Minor cleanup/consistency improvements for thumbnails and file IO.
#
# NOTE: Review environment variables on Heroku (API_ID, API_HASH, BOT_TOKEN, ASSISTANT_SESSION, OWNER_ID, MONGO_URI, etc).
# Make sure ASSISTANT_SESSION is a valid Pyrogram string session if you want assistant account features.

import os
import re
import time
import asyncio
import logging
import random
import inspect
import sys
from typing import Union, Optional, Dict, Any, List
from urllib.parse import urlparse, parse_qs

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import RPCError, FloodWait
try:
    from pyrogram.errors import GroupcallForbidden
except Exception:
    from pyrogram.errors.exceptions.forbidden_403 import Forbidden
    class GroupcallForbidden(Forbidden):
        pass
    import pyrogram.errors
    pyrogram.errors.GroupcallForbidden = GroupcallForbidden

from pyrogram.client import Client as _PyroClient
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream
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

def _get_int_env(name: str, default: Optional[int] = None) -> Optional[int]:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except Exception:
        return default

# Environment - be forgiving but log missing credentials
API_ID = _get_int_env("API_ID")
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ASSISTANT_SESSION = os.environ.get("ASSISTANT_SESSION", "")
OWNER_ID = _get_int_env("OWNER_ID")
MONGO_URI = os.environ.get("MONGO_URI")
MONGO_DBNAME = os.environ.get("MONGO_DBNAME", "dlk_radio")
LOG_CHANNEL_ID = (os.environ.get("LOG_CHANNEL_ID") or "").strip()
YT_DLP_COOKIES = os.environ.get("YT_DLP_COOKIES")

DEV_LINK = "https://t.me/DLKDEVELOPERS"
SUPPORT_LINK = "https://t.me/DevDLK"

THUMB_CACHE_DIR = "cache"
os.makedirs(THUMB_CACHE_DIR, exist_ok=True)
DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

DEFAULT_FALLBACK_DURATION = 240  # fallback seconds

RADIO_STATION = {
    "SirasaFM": "http://live.trusl.com:1170/;",
    # ... (kept list short here — keep your full list as before) ...
    "JAM FM": "http://stream.jam.fm/jamfm-nmr/mp3-192/",
}

radio_tasks: Dict[int, asyncio.Task] = {}
radio_paused = set()
radio_state: Dict[int, Dict[str, Any]] = {}
radio_queue: Dict[int, List[Dict[str, Any]]] = {}
track_watchers: Dict[int, asyncio.Task] = {}
bot_start_time = time.time()

BOT_USERNAME = None
ASSISTANT_USERNAME = None
ASSISTANT_ID = None

# Validate required env early — we don't exit if OWNER_ID or ASSISTANT_SESSION missing, but warn.
_missing = []
if API_ID is None:
    _missing.append("API_ID")
if not API_HASH:
    _missing.append("API_HASH")
if not BOT_TOKEN:
    _missing.append("BOT_TOKEN")
if _missing:
    logging.basicConfig(level=logging.INFO)
    logging.warning(f"Missing required environment variables: {', '.join(_missing)}. Bot may not start correctly.")

# Pyrogram clients
bot = Client("dlk_radio_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
assistant = Client("assistant_account", session_string=ASSISTANT_SESSION) if ASSISTANT_SESSION else None
call_py = PyTgCalls(assistant) if assistant is not None else None

db_client = None
db = None

# ---------- LANGUAGE ----------
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
            "- /play <query|URL> or reply to an audio/voice file and use /play : play music\n"
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
            "- Use /rpush to add a station or url to the queue.\n"
            "- Use /rskip to skip to next queued station, /rend to end radio, /rresume to resume (admins only).\n"
            "- Admins can use pause/resume/skip/stop via the inline buttons.\n"
            "- Owner-only commands: /bl and /unbl in a group to block/unblock the group.\n"
            "- Use /lang to change bot language in this chat.\n"
        ),
        "LANG_MENU_TITLE": "🌐 Chat language settings",
        "CHOOSE_LANG": "🌐 Choose the language for this chat:",
        "LANG_CURRENT": "Current language: {lang_name}",
        "LANG_CHANGED": "✅ Language changed to {lang_name}.",
        "UNKNOWN_LANG": "Unknown language.",
        "NOTHING_TO_RESUME_BTN": "Nothing to resume.",
    },
    # Add other languages as needed...
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
                for f in sorted(formats, key=lambda x: (x.get("abr") or 0), reverse=True):
                    if f.get("acodec") and f.get("url"):
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
                os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
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
        background = background.filter(ImageFilter.BoxBlur(6))
        enhancer = ImageEnhance.Brightness(background)
        background = enhancer.enhance(0.85)
        art = _create_circular_artwork(image, diameter=520, border=10)
        art_x = 60
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
    try:
        db_client = MongoClient(MONGO_URI)
        db = db_client[MONGO_DBNAME]
        db.blocked.create_index("chat_id")
        db.logs.create_index("ts")
        db.langs.create_index("chat_id", unique=True)
        logging.info(f"Connected to MongoDB: {MONGO_DBNAME}")
    except Exception as e:
        logging.warning(f"Failed connecting to MongoDB: {e}")

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
    try:
        return db.blocked.find_one({"chat_id": chat_id}) is not None
    except Exception:
        return False

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
        if user and OWNER_ID is not None and user.id == OWNER_ID:
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

def player_controls_markup(chat_id: int):
    if chat_id in radio_paused:
        controls = [
            InlineKeyboardButton("▷", callback_data="radio_resume"),
            InlineKeyboardButton("‣‣I", callback_data="music_skip"),
            InlineKeyboardButton("▢", callback_data="radio_stop"),
        ]
    else:
        controls = [
            InlineKeyboardButton("II", callback_data="radio_pause"),
            InlineKeyboardButton("‣‣I", callback_data="music_skip"),
            InlineKeyboardButton("▢", callback_data="radio_stop"),
        ]
    bottom = [
        InlineKeyboardButton("👨‍💻 Dev", url=DEV_LINK),
        InlineKeyboardButton("💬 Support", url=SUPPORT_LINK),
    ]
    return InlineKeyboardMarkup([controls, bottom])

# ---------- Helpers for coroutine/maybe-async calls ----------
async def _call_maybe_await(func, *args, **kwargs):
    try:
        if func is None:
            return None
        if not callable(func):
            return None
        res = func(*args, **kwargs)
        if inspect.isawaitable(res):
            return await res
        return res
    except Exception as e:
        logging.debug(f"_call_maybe_await failed for {getattr(func, '__name__', str(func))}: {e}")
        return None

async def _safe_call_py_method(method_name: str, *args, **kwargs):
    try:
        if call_py is None or not hasattr(call_py, method_name):
            return None
        attr = getattr(call_py, method_name)
        if not callable(attr):
            return None
        res = attr(*args, **kwargs)
        if inspect.isawaitable(res):
            return await res
        return res
    except Exception as e:
        logging.debug(f"_safe_call_py_method {method_name} failed: {e}")
        return None

async def _force_leave_call(chat_id: int):
    try:
        if call_py is None:
            logging.debug("_force_leave_call: call_py is None, skipping")
            return
        # try multiple leave method names
        for name in ("leave_group_call", "leave_call", "leave", "stop"):
            try:
                method = getattr(call_py, name, None)
                if not method:
                    continue
                res = method(chat_id)
                if inspect.isawaitable(res):
                    await res
                logging.debug(f"_force_leave_call: used {name} for {chat_id}")
                return
            except Exception as e:
                logging.debug(f"_force_leave_call {name} failed: {e}")
    except Exception as e:
        logging.debug(f"_force_leave_call final error: {e}")

async def leave_voice_chat(chat_id: int, cancel_watchers: bool = True):
    try:
        if chat_id in radio_tasks:
            try:
                radio_tasks[chat_id].cancel()
            except Exception:
                pass
            radio_tasks.pop(chat_id, None)
        if cancel_watchers and chat_id in track_watchers:
            try:
                track_watchers[chat_id].cancel()
            except Exception:
                pass
            track_watchers.pop(chat_id, None)
        radio_paused.discard(chat_id)
        radio_state.pop(chat_id, None)
        await _force_leave_call(chat_id)
    except Exception as e:
        logging.warning(f"leave_voice_chat failed {chat_id}: {e}")

def store_play_state(
    chat_id: int,
    title: str,
    url: str,
    msg_id: int,
    start_time: Optional[float],
    elapsed: float = 0.0,
    paused: bool = False,
    duration: Optional[int] = None,
):
    state = {
        "chat_id": chat_id,
        "station": title,
        "url": url,
        "msg_id": msg_id,
        "start_time": start_time,
        "elapsed": elapsed,
        "paused": paused,
        "duration": duration,
        "ts": time.time(),
    }
    radio_state[chat_id] = state

# ---------- Ensure assistant present helper ----------
async def ensure_assistant_in_chat(chat_id: int) -> bool:
    if assistant is None:
        logging.debug("ensure_assistant_in_chat: No assistant session configured.")
        return False
    try:
        try:
            me = await _call_maybe_await(getattr(assistant, "get_me", None))
            assist_id = me.id if me else None
        except Exception as e:
            logging.debug(f"ensure_assistant_in_chat: assistant.get_me failed: {e}")
            return False

        # Check membership
        try:
            await assistant.get_chat_member(chat_id, assist_id)
            return True
        except RPCError:
            pass  # assistant not present

        # Try to get invite link and have assistant join
        try:
            invite = await bot.create_chat_invite_link(chat_id, member_limit=1, name="DLK BOT assistant")
            invite_link = invite.invite_link
        except Exception as e:
            logging.warning(f"ensure_assistant_in_chat: create_chat_invite_link failed: {e}")
            return False

        try:
            await assistant.join_chat(invite_link)
            await asyncio.sleep(0.5)
            try:
                await bot.send_message(chat_id, t(chat_id, "ASSISTANT_JOIN_INFO"), disable_web_page_preview=True)
            except Exception:
                pass
            return True
        except Exception as e:
            logging.warning(f"ensure_assistant_in_chat: assistant.join_chat failed: {e}")
            return False
    except Exception as e:
        logging.debug(f"ensure_assistant_in_chat failed: {e}")
        return False

# ---------- PyTgCalls robust play helper ----------
async def _restart_assistant_and_pytgcalls():
    try:
        logging.debug("_restart_assistant_and_pytgcalls: attempting to restart PyTgCalls and assistant")
        if call_py is not None:
            await _call_maybe_await(getattr(call_py, "stop", None))
        await asyncio.sleep(0.5)
        if call_py is not None:
            await _call_maybe_await(getattr(call_py, "start", None))
        # Restart assistant session (best-effort)
        if assistant is not None:
            await _call_maybe_await(getattr(assistant, "stop", None))
            await asyncio.sleep(0.3)
            await _call_maybe_await(getattr(assistant, "start", None))
        await asyncio.sleep(0.5)
    except Exception as e:
        logging.debug(f"_restart_assistant_and_pytgcalls unexpected error: {e}")

async def _pytgcalls_play(chat_id: int, stream_url: str) -> bool:
    try:
        if call_py is None:
            logging.debug("_pytgcalls_play: call_py is None")
            return False
        for attempt in range(2):
            logging.debug(f"_pytgcalls_play: attempt {attempt+1} for chat {chat_id} url {stream_url}")
            try:
                # Preferred attempt: play with MediaStream wrapper
                try:
                    res = await _safe_call_py_method("play", chat_id, MediaStream(stream_url))
                    if res is not None:
                        logging.debug(f"_pytgcalls_play: play succeeded for {chat_id}")
                        return True
                except Exception as e:
                    logging.debug(f"_pytgcalls_play: play() attempt failed: {e}")

                # Try several alternative methods
                for method_name in ("join_group_call", "join_call", "start_stream", "start_playout", "start", "stream", "play_stream"):
                    try:
                        res = getattr(call_py, method_name, None)
                        if res is None:
                            continue
                        result = res(chat_id, MediaStream(stream_url)) if method_name in ("join_group_call", "join_call", "play", "play_stream") else res(chat_id, stream_url)
                        if inspect.isawaitable(result):
                            await result
                        logging.debug(f"_pytgcalls_play: {method_name} succeeded for {chat_id}")
                        return True
                    except Exception as e:
                        logging.debug(f"_pytgcalls_play: {method_name}() attempt failed: {e}")

                logging.debug("_pytgcalls_play: no available play/join method succeeded on this attempt")
            except Exception as e:
                logging.debug(f"_pytgcalls_play inner error: {e}")

            if attempt == 0:
                logging.info("_pytgcalls_play: first attempt failed — restarting PyTgCalls/assistant and retrying")
                await _restart_assistant_and_pytgcalls()
                await asyncio.sleep(1.0)
        logging.debug("_pytgcalls_play: all attempts failed")
        return False
    except Exception as e:
        logging.debug(f"_pytgcalls_play error: {e}")
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
async def track_watcher(chat_id: int, duration: int, msg_id: int):
    try:
        await asyncio.sleep(max(1, duration) + 2)
        q = radio_queue.get(chat_id, [])
        if q:
            next_entry = q.pop(0)
            radio_queue[chat_id] = q
            await play_entry(chat_id, next_entry)
            log_event_sync("music_auto_skipped", {"chat_id": chat_id, "title": next_entry.get("title")})
        else:
            try:
                await leave_voice_chat(chat_id, cancel_watchers=False)
            except Exception:
                pass
            try:
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=msg_id,
                    caption=t(chat_id, "BOT_STOPPED"),
                    reply_markup=None,
                )
            except Exception as e:
                logging.debug(f"track_watcher edit caption failed {chat_id}/{msg_id}: {e}")
            log_event_sync("music_track_autostop", {"chat_id": chat_id})
    except asyncio.CancelledError:
        return
    except Exception as e:
        logging.debug(f"track_watcher error {chat_id}: {e}")

# ---------- play_entry ----------
async def play_entry(chat_id: int, entry: dict, reply_message: Optional[Message] = None):
    try:
        if chat_id in radio_tasks:
            try:
                radio_tasks[chat_id].cancel()
            except Exception:
                pass
            radio_tasks.pop(chat_id, None)

        assistant_ok = True
        if assistant is not None:
            assistant_ok = await ensure_assistant_in_chat(chat_id)
        else:
            # if no assistant session configured, we still try to stream but many PyTgCalls
            # features require a user session. Log for debug.
            logging.debug("play_entry: assistant session not configured; streaming may not work")

        if not assistant_ok and assistant is not None:
            logging.warning(f"play_entry: assistant not present in chat {chat_id}")
            return False

        stream_source = entry["stream_url"]

        played = await _pytgcalls_play(chat_id, stream_source)
        if not played:
            logging.error("Play failed: PyTgCalls could not start playback")
            try:
                await leave_voice_chat(chat_id)
            except Exception:
                pass
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
        caption = f"🎧 {t(chat_id, 'NOW_PLAYING', title=title)}"
        try:
            if thumb_path and os.path.isfile(thumb_path):
                msg = await bot.send_photo(
                    chat_id,
                    photo=thumb_path,
                    caption=caption,
                    reply_markup=player_controls_markup(chat_id),
                )
            else:
                msg = await bot.send_photo(
                    chat_id,
                    photo="https://files.catbox.moe/3o9qj5.jpg",
                    caption=caption,
                    reply_markup=player_controls_markup(chat_id),
                )
        except Exception:
            msg = await bot.send_photo(
                chat_id,
                photo="https://files.catbox.moe/3o9qj5.jpg",
                caption=caption,
                reply_markup=player_controls_markup(chat_id),
            )
        duration = entry.get("duration")
        try:
            if duration is not None:
                duration = int(duration)
        except Exception:
            duration = None
        if not duration or duration <= 0:
            duration = DEFAULT_FALLBACK_DURATION
        start_time = time.time()
        store_play_state(
            chat_id,
            title,
            entry.get("stream_url"),
            msg.id,
            start_time,
            elapsed=0.0,
            paused=False,
            duration=duration,
        )
        radio_paused.discard(chat_id)
        radio_tasks[chat_id] = asyncio.create_task(
            update_radio_timer(chat_id, msg.id, title, start_time, duration)
        )
        if chat_id in track_watchers:
            try:
                track_watchers[chat_id].cancel()
            except Exception:
                pass
        track_watchers[chat_id] = asyncio.create_task(track_watcher(chat_id, duration, msg.id))
        log_event_sync("music_started", {"chat_id": chat_id, "title": title})
        return True
    except Exception:
        logging.error("Play entry failed", exc_info=True)
        try:
            await leave_voice_chat(chat_id)
        except Exception:
            pass
        return False

# update_radio_timer placed earlier used in play_entry
async def update_radio_timer(chat_id: int, msg_id: int, title: str, start_time: float, track_duration: int):
    while True:
        try:
            elapsed = max(0, int(time.time() - start_time))
            remaining = max(0, track_duration - elapsed)
            m, s = divmod(remaining, 60)
            timer = f"{m:02d}:{s:02d}"
            caption = f"🎧 Now Playing: {title}\n⏳ Duration: {timer}"
            try:
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=msg_id,
                    caption=caption,
                    reply_markup=player_controls_markup(chat_id),
                )
            except Exception as e:
                logging.debug(f"Timer edit failed for {chat_id}/{msg_id}: {e}")
            if remaining <= 0:
                break
        except Exception as e:
            logging.debug(f"Timer update failed for {chat_id}/{msg_id}: {e}")
            break
        await asyncio.sleep(5)

# ---------- /play ----------
@bot.on_message(filters.group & filters.command(["play", "p"]))
async def cmd_play(_, message: Message):
    chat_id = message.chat.id
    user = message.from_user
    if is_group_blocked_sync(chat_id):
        return await message.reply_text(t(chat_id, "GROUP_BLOCKED"))
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
            try:
                await info_msg.edit_text(t(chat_id, "YTDLP_FAIL"))
            except Exception:
                pass
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
        log_event_sync("music_queued", {"chat_id": chat_id, "title": entry["title"], "by": user.id if user else None})
        return

    ok = await play_entry(chat_id, entry, reply_message=message)
    if ok:
        try:
            if info_msg:
                await info_msg.edit_text(t(chat_id, "NOW_PLAYING", title=entry["title"]))
        except Exception:
            pass
    else:
        try:
            if info_msg:
                await info_msg.edit_text(t(chat_id, "FAILED_PLAY_REQUEST"))
        except Exception:
            pass

# ---------- skip/queue/stop ----------
@bot.on_message(filters.group & filters.command(["skip", "s"]))
async def cmd_skip(_, message: Message):
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text(t(chat_id, "ONLY_ADMINS_SKIP"))
    q = radio_queue.get(chat_id, [])
    if not q:
        await leave_voice_chat(chat_id)
        await message.reply_text(t(chat_id, "SKIPPED_NO_QUEUE"))
        log_event_sync("music_skipped_stop", {"chat_id": chat_id, "by": message.from_user.id if message.from_user else None})
        return
    next_entry = q.pop(0)
    radio_queue[chat_id] = q
    if chat_id in track_watchers:
        try:
            track_watchers[chat_id].cancel()
        except Exception:
            pass
        track_watchers.pop(chat_id, None)
    ok = await play_entry(chat_id, next_entry)
    if ok:
        await message.reply_text(t(chat_id, "NOW_PLAYING_QUEUE", title=next_entry["title"]))
        log_event_sync("music_skipped", {"chat_id": chat_id, "title": next_entry["title"], "by": message.from_user.id if message.from_user else None})
    else:
        await message.reply_text(t(chat_id, "FAILED_PLAY_NEXT", title=next_entry.get("title")))

@bot.on_message(filters.group & filters.command(["queue", "q"]))
async def cmd_queue(_, message: Message):
    chat_id = message.chat.id
    q = radio_queue.get(chat_id, [])
    if not q:
        return await message.reply_text(t(chat_id, "QUEUE_EMPTY"))
    text = t(chat_id, "QUEUE_HEADER")
    for i, item in enumerate(q[:10], start=1):
        text += f"{i}. {item.get('title')}\n"
    await message.reply_text(text)

@bot.on_message(filters.group & filters.command(["stop", "end"]))
async def general_stop_handler(_, message: Message):
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text(t(chat_id, "ONLY_ADMINS_STOP"))

    state = radio_state.get(chat_id)
    msg_id = state.get("msg_id") if state else None

    await leave_voice_chat(chat_id)

    if msg_id:
        try:
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption=t(chat_id, "BOT_STOPPED"),
                reply_markup=None,
            )
        except Exception:
            pass

    await message.reply_text(t(chat_id, "BOT_STOPPED"))
    log_event_sync("radio_stopped_text", {"chat_id": chat_id, "by": message.from_user.id if message.from_user else None})

# ---------- RADIO COMMANDS ----------
@bot.on_message(filters.group & filters.command(["radio"]))
async def cmd_radio_menu(_, message: Message):
    chat_id = message.chat.id
    if is_group_blocked_sync(chat_id):
        return await message.reply_text(t(chat_id, "GROUP_BLOCKED"))
    kb = radio_buttons(0)
    await message.reply_text("📻 Radio Stations - choose one:", reply_markup=kb)

@bot.on_message(filters.group & filters.command(["rend"]))
async def cmd_rend(_, message: Message):
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text(t(chat_id, "ONLY_ADMINS_RADIO_END"))
    try:
        await leave_voice_chat(chat_id)
        await message.reply_text(t(chat_id, "RADIO_ENDED"))
        log_event_sync("radio_rend", {"chat_id": chat_id, "by": message.from_user.id if message.from_user else None})
    except Exception as e:
        logging.warning(f"cmd_rend failed: {e}")
        await message.reply_text(t(chat_id, "FAILED_END_RADIO"))

@bot.on_message(filters.group & filters.command(["rskip"]))
async def cmd_rskip(_, message: Message):
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text(t(chat_id, "ONLY_ADMINS_RADIO_SKIP"))
    q = radio_queue.get(chat_id, [])
    if not q:
        await leave_voice_chat(chat_id)
        await message.reply_text(t(chat_id, "SKIPPED_NO_QUEUE_RADIO"))
        log_event_sync("radio_rskip_stop", {"chat_id": chat_id, "by": message.from_user.id if message.from_user else None})
        return
    next_entry = q.pop(0)
    radio_queue[chat_id] = q
    if chat_id in track_watchers:
        try:
            track_watchers[chat_id].cancel()
        except Exception:
            pass
        track_watchers.pop(chat_id, None)
    ok = await play_entry(chat_id, next_entry)
    if ok:
        await message.reply_text(t(chat_id, "NOW_PLAYING_QUEUE", title=next_entry["title"]))
        log_event_sync("radio_rskip", {"chat_id": chat_id, "title": next_entry["title"], "by": message.from_user.id if message.from_user else None})
    else:
        await message.reply_text(t(chat_id, "FAILED_PLAY_NEXT_RADIO", title=next_entry.get("title")))

@bot.on_message(filters.group & filters.command(["rpush"]))
async def cmd_rpush(_, message: Message):
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text(t(chat_id, "ONLY_ADMINS"))
    args = None
    if len(message.command) > 1:
        args = message.text.split(None, 1)[1].strip()
    if not args:
        return await message.reply_text(
            "Usage: /rpush <station_name or stream_url>\nExample: /rpush SirasaFM OR /rpush https://stream.example.com/live"
        )
    station_name = args
    stream_url = None
    title = station_name
    if station_name in RADIO_STATION:
        stream_url = RADIO_STATION[station_name]
        title = station_name
    elif looks_like_url(station_name):
        stream_url = station_name
        title = station_name.split("/")[-1] or station_name
    else:
        for k in RADIO_STATION.keys():
            if k.lower() == station_name.lower():
                stream_url = RADIO_STATION[k]
                title = k
                break
    if not stream_url:
        return await message.reply_text("Could not find station or invalid URL. Provide a valid station name or URL.")
    entry = {
        "title": title,
        "stream_url": stream_url,
        "webpage": None,
        "thumbnail": None,
        "duration": None,
        "is_local": False,
    }
    if chat_id not in radio_queue:
        radio_queue[chat_id] = []
    radio_queue[chat_id].append(entry)
    await message.reply_text(t(chat_id, "ADDED_RADIO_QUEUE", title=title))
    log_event_sync("radio_rpush", {"chat_id": chat_id, "title": title, "by": message.from_user.id if message.from_user else None})

@bot.on_message(filters.group & filters.command(["rresume", "rremuse"]))
async def cmd_rresume(_, message: Message):
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text(t(chat_id, "ONLY_ADMINS_RADIO_RESUME"))
    state = radio_state.get(chat_id)
    if not state:
        return await message.reply_text(t(chat_id, "NOTHING_TO_RESUME"))
    try:
        await _safe_call_py_method("resume_stream", chat_id)
        await _safe_call_py_method("resume", chat_id)
        elapsed = state.get("elapsed", 0.0) or 0.0
        start_time = time.time() - elapsed
        state["paused"] = False
        state["elapsed"] = 0.0
        state["start_time"] = start_time
        radio_paused.discard(chat_id)
        duration = state.get("duration")
        store_play_state(
            chat_id,
            state.get("station"),
            state.get("url"),
            state.get("msg_id"),
            start_time,
            elapsed=0.0,
            paused=False,
            duration=duration,
        )
        if duration is not None:
            if chat_id in radio_tasks:
                try:
                    radio_tasks[chat_id].cancel()
                except Exception:
                    pass
                radio_tasks.pop(chat_id, None)
            radio_tasks[chat_id] = asyncio.create_task(
                update_radio_timer(chat_id, state.get("msg_id"), state.get("station"), start_time, duration)
            )
        try:
            await bot.edit_message_reply_markup(chat_id, state.get("msg_id"), reply_markup=player_controls_markup(chat_id))
        except Exception:
            pass
        await message.reply_text(t(chat_id, "RADIO_RESUMED"))
        log_event_sync("radio_resumed_cmd", {"chat_id": chat_id, "by": message.from_user.id if message.from_user else None})
    except Exception as e:
        logging.debug(f"cmd_rresume failed: {e}")
        await message.reply_text(t(chat_id, "FAILED_RESUME"))

# ---------- BLOCK / UNBLOCK ----------
@bot.on_message(filters.group & filters.command(["bl", "block"]))
async def cmd_block_group(_, message: Message):
    chat_id = message.chat.id
    if not message.from_user or OWNER_ID is None or message.from_user.id != OWNER_ID:
        return await message.reply_text(t(chat_id, "ONLY_OWNER_BLOCK"))
    try:
        block_group_sync(chat_id, message.from_user.id, reason="blocked by owner via /bl")
        await message.reply_text(t(chat_id, "GROUP_BLOCKED_OK"))
        log_event_sync("group_blocked", {"chat_id": chat_id, "by": message.from_user.id})
    except Exception as e:
        logging.warning(f"Failed to block group {chat_id}: {e}")
        await message.reply_text(t(chat_id, "FAILED_BLOCK_GROUP"))

@bot.on_message(filters.group & filters.command(["unbl", "unblock"]))
async def cmd_unblock_group(_, message: Message):
    chat_id = message.chat.id
    if not message.from_user or OWNER_ID is None or message.from_user.id != OWNER_ID:
        return await message.reply_text(t(chat_id, "ONLY_OWNER_UNBLOCK"))
    try:
        unblock_group_sync(chat_id)
        await message.reply_text(t(chat_id, "GROUP_UNBLOCKED_OK"))
        log_event_sync("group_unblocked", {"chat_id": chat_id, "by": message.from_user.id})
    except Exception as e:
        logging.warning(f"Failed to unblock group {chat_id}: {e}")
        await message.reply_text(t(chat_id, "FAILED_UNBLOCK_GROUP"))

# ---------- OWNER PANEL ----------
@bot.on_message(filters.private & filters.command(["panel"]))
async def owner_panel(_, message: Message):
    chat_id = message.chat.id
    if not message.from_user or OWNER_ID is None or message.from_user.id != OWNER_ID:
        return await message.reply_text(t(chat_id, "ONLY_OWNER_PANEL"))
    if db is None:
        return await message.reply_text(t(chat_id, "DB_NOT_CONFIGURED"))
    try:
        blocked = list(db.blocked.find({}).sort("ts", -1).limit(100))
        if not blocked:
            return await message.reply_text(t(chat_id, "BLOCK_LIST_EMPTY"))
        text_lines = [t(chat_id, "BLOCK_LIST_HEADER")]
        for b in blocked:
            text_lines.append(
                f"- {b.get('chat_id')} (by {b.get('by')}, reason: {b.get('reason') or 'n/a'})"
            )
        await message.reply_text("\n".join(text_lines))
    except Exception as e:
        logging.warning(f"Failed to fetch blocked list: {e}")
        await message.reply_text(t(chat_id, "FAILED_FETCH_BLOCKS"))

# ---------- CALLBACKS ----------
@bot.on_callback_query(filters.regex("^music_skip$"))
async def cb_music_skip(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await query.answer(t(chat_id, "ONLY_ADMINS_SKIP"), show_alert=True)
    q = radio_queue.get(chat_id, [])
    if not q:
        await leave_voice_chat(chat_id)
        try:
            await query.message.edit_caption(
                caption=t(chat_id, "MUSIC_SKIP_BTN_NO_QUEUE"),
                reply_markup=None,
            )
        except Exception:
            pass
        await query.answer(t(chat_id, "MUSIC_SKIP_BTN_ALERT"), show_alert=True)
        log_event_sync(
            "music_skipped_stop",
            {"chat_id": chat_id, "by": query.from_user.id if query.from_user else None},
        )
        return
    next_entry = q.pop(0)
    radio_queue[chat_id] = q
    if chat_id in track_watchers:
        try:
            track_watchers[chat_id].cancel()
        except Exception:
            pass
        track_watchers.pop(chat_id, None)
    ok = await play_entry(chat_id, next_entry)
    if ok:
        try:
            await query.message.edit_caption(
                caption=t(chat_id, "NOW_PLAYING_QUEUE", title=next_entry["title"]),
                reply_markup=player_controls_markup(chat_id),
            )
        except Exception:
            pass
        await query.answer(t(chat_id, "MUSIC_SKIP_BTN_ALERT"), show_alert=False)
        log_event_sync(
            "music_skipped",
            {"chat_id": chat_id, "title": next_entry["title"], "by": query.from_user.id if query.from_user else None},
        )
    else:
        await query.answer(t(chat_id, "MUSIC_SKIP_BTN_FAIL"), show_alert=True)

@bot.on_callback_query(filters.regex("^radio_pause$"))
async def radio_pause_cb(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await query.answer(t(chat_id, "ONLY_ADMINS_RADIO_BUTTON"), show_alert=True)
    state = radio_state.get(chat_id)
    if not state:
        return await query.answer(t(chat_id, "RADIO_NOTHING_PLAYING"), show_alert=True)
    try:
        await _safe_call_py_method("pause_stream", chat_id)
        await _safe_call_py_method("pause", chat_id)
        start_time = state.get("start_time") or time.time()
        elapsed = time.time() - start_time if start_time else state.get("elapsed", 0.0)
        state["paused"] = True
        state["elapsed"] = elapsed
        state["start_time"] = None
        radio_paused.add(chat_id)
        store_play_state(
            chat_id,
            state.get("station"),
            state.get("url"),
            state.get("msg_id"),
            None,
            elapsed=elapsed,
            paused=True,
            duration=state.get("duration"),
        )
        try:
            await query.message.edit_reply_markup(reply_markup=player_controls_markup(chat_id))
        except Exception:
            pass
        await query.answer(t(chat_id, "RADIO_PAUSED"), show_alert=False)
        log_event_sync("radio_paused", {"chat_id": chat_id, "by": query.from_user.id if query.from_user else None})
    except Exception as e:
        logging.debug(f"Pause failed: {e}")
        await query.answer(t(chat_id, "RADIO_PAUSE_FAIL"), show_alert=True)

@bot.on_callback_query(filters.regex("^radio_resume$"))
async def radio_resume_cb(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await query.answer(t(chat_id, "ONLY_ADMINS_RADIO_BUTTON"), show_alert=True)
    state = radio_state.get(chat_id)
    if not state:
        return await query.answer(t(chat_id, "NOTHING_TO_RESUME_BTN"), show_alert=True)
    try:
        await _safe_call_py_method("resume_stream", chat_id)
        await _safe_call_py_method("resume", chat_id)
        elapsed = state.get("elapsed", 0.0) or 0.0
        start_time = time.time() - elapsed
        state["paused"] = False
        state["elapsed"] = 0.0
        state["start_time"] = start_time
        radio_paused.discard(chat_id)
        duration = state.get("duration")
        store_play_state(
            chat_id,
            state.get("station"),
            state.get("url"),
            state.get("msg_id"),
            start_time,
            elapsed=0.0,
            paused=False,
            duration=duration,
        )
        if duration is not None:
            if chat_id in radio_tasks:
                try:
                    radio_tasks[chat_id].cancel()
                except Exception:
                    pass
                radio_tasks.pop(chat_id, None)
            radio_tasks[chat_id] = asyncio.create_task(
                update_radio_timer(chat_id, state.get("msg_id"), state.get("station"), start_time, duration)
            )
        try:
            await query.message.edit_reply_markup(reply_markup=player_controls_markup(chat_id))
        except Exception:
            pass
        await query.answer(t(chat_id, "RADIO_RESUMED_BTN"), show_alert=False)
        log_event_sync("radio_resumed", {"chat_id": chat_id, "by": query.from_user.id if query.from_user else None})
    except Exception as e:
        logging.debug(f"Resume failed: {e}")
        await query.answer(t(chat_id, "RADIO_RESUME_FAIL_BTN"), show_alert=True)

@bot.on_callback_query(filters.regex("^radio_stop$"))
async def cb_radio_stop(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await query.answer(t(chat_id, "ONLY_ADMINS_RADIO_BUTTON"), show_alert=True)
    try:
        await leave_voice_chat(chat_id)
        try:
            await query.message.delete()
        except Exception:
            try:
                await query.message.edit_caption(
                    caption=t(chat_id, "RADIO_STOPPED_BTN"),
                    reply_markup=None,
                )
            except Exception:
                pass
        await query.answer(t(chat_id, "RADIO_STOPPED_BTN"), show_alert=False)
        log_event_sync("radio_stopped", {"chat_id": chat_id, "by": query.from_user.id if query.from_user else None})
    except Exception as e:
        logging.error(f"Stop failed via callback: {e}", exc_info=True)
        await query.answer(t(chat_id, "RADIO_STOP_FAIL_BTN"), show_alert=True)

# ---------- RADIO PLAY ----------
@bot.on_callback_query(filters.regex("^radio_play_"))
async def play_radio_station(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    station = query.data.replace("radio_play_", "")
    url = RADIO_STATION.get(station)
    user = query.from_user
    if is_group_blocked_sync(chat_id):
        await query.answer(t(chat_id, "ASSISTANT_BLOCKED_GROUP"), show_alert=True)
        return
    if not url:
        return await query.answer(t(chat_id, "STATION_URL_NOT_FOUND"), show_alert=True)
    try:
        assistant_ok = True
        if assistant is not None:
            assistant_ok = await ensure_assistant_in_chat(chat_id)
        if not assistant_ok and assistant is not None:
            try:
                invite = await bot.create_chat_invite_link(chat_id, member_limit=1, name="DLK BOT assistant")
                invite_link = invite.invite_link
                help_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Invite Link", url=invite_link)],
                    [InlineKeyboardButton("ℹ️ How to add assistant", callback_data="assistant_invite_help")],
                    [InlineKeyboardButton("❌ Dismiss", callback_data="radio_close")],
                ])
                await query.message.reply_text(
                    t(chat_id, "ASSISTANT_INVITE_TEXT"),
                    reply_markup=help_kb,
                )
            except Exception:
                await query.message.reply_text(t(chat_id, "ASSISTANT_INVITE_FAIL_TEXT"))
            return

        played = await _pytgcalls_play(chat_id, url)
        if not played:
            await leave_voice_chat(chat_id)
            await query.message.reply_text(t(chat_id, "RADIO_PLAY_FAILED_ASSIST", error="pyTgCalls failed"))
            await query.answer(t(chat_id, "RADIO_PLAY_FAILED_ASSIST", error="pyTgCalls failed"), show_alert=True)
            return

        try:
            msg = await query.message.edit_caption(
                caption=f"🎧 {station}\n🔴 LIVE Radio",
                reply_markup=player_controls_markup(chat_id),
            )
            msg_id = msg.id if msg else query.message.id
        except Exception:
            msg = await bot.send_message(chat_id, f"🎧 {station}\n🔴 LIVE Radio", reply_markup=player_controls_markup(chat_id))
            msg_id = msg.id

        start_time = time.time()
        store_play_state(chat_id, station, url, msg_id, start_time, elapsed=0.0, paused=False, duration=None)
        radio_paused.discard(chat_id)
        await query.answer(f"Now playing {station} via assistant!", show_alert=False)
        log_event_sync("radio_started", {"chat_id": chat_id, "station": station, "by": user.id if user else None})
    except FloodWait as e:
        await leave_voice_chat(chat_id)
        wait_time = getattr(e, "value", None) or getattr(e, "x", None) or "unknown"
        await query.message.reply_text(t(chat_id, "RATE_LIMIT", seconds=wait_time))
        await query.answer(f"Wait {wait_time}s", show_alert=True)
    except ntgcalls.TelegramServerError:
        await leave_voice_chat(chat_id)
        await query.message.reply_text(t(chat_id, "VOICECHAT_NOT_READY"))
        await query.answer("Voice chat not ready!", show_alert=True)
    except RPCError as e:
        await leave_voice_chat(chat_id)
        await query.message.reply_text(t(chat_id, "RADIO_PLAY_FAILED_ASSIST", error=str(e)))
    except Exception as e:
        await leave_voice_chat(chat_id)
        logging.error("General radio play error", exc_info=True)
        await query.message.reply_text(t(chat_id, "RADIO_START_FAIL", error=str(e)))

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
    await query.answer()
    try:
        await query.message.edit_text(text, reply_markup=kb)
    except Exception:
        await query.message.reply_text(text, reply_markup=kb)

@bot.on_callback_query(filters.regex("^assistant_invite_help$"))
async def assistant_invite_help(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    help_text = t(chat_id, "ASSISTANT_INVITE_HELP_TEXT")
    await query.answer()
    await query.message.reply_text(help_text)

@bot.on_callback_query(filters.regex("^help_info$"))
async def cb_help_info(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    help_text = t(chat_id, "HELP_TEXT")
    await query.answer()
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
        await query.answer(t(chat_id, "UNKNOWN_LANG"), show_alert=True)
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
    await query.answer()

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
    await query.answer()
    try:
        await query.message.edit_text(text, reply_markup=lang_keyboard(current))
    except Exception:
        await query.message.reply_text(text, reply_markup=lang_keyboard(current))

@bot.on_callback_query(filters.regex(r"^radio_page_(\d+)$"))
async def cb_radio_page(_, query: CallbackQuery):
    try:
        m = re.match(r"radio_page_(\d+)", query.data)
        if not m:
            return await query.answer()
        page = int(m.group(1))
        kb = radio_buttons(page)
        try:
            await query.message.edit_text("📻 Radio Stations - choose one:", reply_markup=kb)
        except Exception:
            try:
                await query.message.edit_reply_markup(reply_markup=kb)
            except Exception:
                pass
        await query.answer()
    except Exception as e:
        logging.debug(f"radio_page handler failed: {e}")
        try:
            await query.answer("Failed to load page.", show_alert=True)
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
        await query.answer()
    except Exception as e:
        logging.debug(f"radio_close handler failed: {e}")
        try:
            await query.answer("Failed to close menu.", show_alert=True)
        except Exception:
            pass

# ---------- STARTUP / MAIN ----------
def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

if __name__ == "__main__":
    _setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting DLK Bot...")

    init_db_sync()

    async def main():
        global BOT_USERNAME, ASSISTANT_USERNAME, ASSISTANT_ID, assistant, call_py

        # Start assistant session and PyTgCalls if configured
        if assistant is not None:
            try:
                await _call_maybe_await(getattr(assistant, "start", None))
                me = await _call_maybe_await(getattr(assistant, "get_me", None))
                if me:
                    ASSISTANT_USERNAME = getattr(me, "username", None)
                    ASSISTANT_ID = getattr(me, "id", None)
                    logger.info(f"Assistant started: @{ASSISTANT_USERNAME} ({ASSISTANT_ID})")
            except Exception as e:
                logger.warning(f"Assistant start failed: {e}")
            try:
                # Recreate call_py with running assistant if needed
                if call_py is None:
                    call_py = PyTgCalls(assistant)
                await _call_maybe_await(getattr(call_py, "start", None))
                logger.info("PyTgCalls started.")
            except Exception as e:
                logger.warning(f"PyTgCalls start failed: {e}")

        # Start main bot client
        try:
            await _call_maybe_await(getattr(bot, "start", None))
            me = await _call_maybe_await(getattr(bot, "get_me", None))
            if me:
                BOT_USERNAME = getattr(me, "username", None)
                logger.info(f"Bot started: @{BOT_USERNAME}")
        except Exception as e:
            logger.warning(f"Bot start failed: {e}")

        log_event_sync("bot_started", {"ts": time.time(), "owner": OWNER_ID})

        # Idle to keep the clients running
        from pyrogram import idle
        try:
            logger.info("Entering idle - bot is ready to receive commands.")
            await idle()
        finally:
            logger.info("Shutting down clients...")
            try:
                await _call_maybe_await(getattr(call_py, "stop", None))
            except Exception:
                pass
            try:
                await _call_maybe_await(getattr(assistant, "stop", None))
            except Exception:
                pass
            try:
                await _call_maybe_await(getattr(bot, "stop", None))
            except Exception:
                pass

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user.")
    except Exception as e:
        logger.error(f"Unexpected error in main: {e}", exc_info=True)
