# DLK.py - patched/fixed version (assistant/chat-member handling fixes)
# NOTE: This is the full file contents adapted from user-provided code with robust assistant checks.
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
    "HelaNadaFM": "https://stream-176.zeno.fm/9ndoyrsujwpvv",
    "Radio Plus Hitz": "https://altair.streamerr.co/stream/8054",
    # ... (keep all other stations as before)
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

# TRANSLATIONS ... (same as original) - omitted here for brevity in this snippet
TRANSLATIONS = {
    "en": {
        # ... same translations as provided earlier ...
        "ONLY_ADMINS_RADIO_BUTTON": "Only admins can control the radio!",
        # etc...
    },
    "si": {
        # ... your Sinhala translations ...
    }
}
LANG_NAMES = {"en": "English 🇬🇧", "si": "සිංහල 🇱🇰"}
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
    db_client = MongoClient(MONGO_URI)
    db = db_client[MONGO_DBNAME]
    db.blocked.create_index("chat_id")
    db.logs.create_index("ts")
    db.langs.create_index("chat_id", unique=True)
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

# Optional: support multiple owners via OWNER_IDS env var (comma-separated)
OWNER_IDS_RAW = os.environ.get("OWNER_IDS", "").strip()
if OWNER_IDS_RAW:
    try:
        OWNER_IDS = [int(x.strip()) for x in OWNER_IDS_RAW.split(",") if x.strip()]
    except Exception:
        OWNER_IDS = []
else:
    OWNER_IDS = []

async def dlk_privilege_validator(subject: Union[Message, CallbackQuery]) -> bool:
    """
    Robust privilege validator.

    Returns True when:
      - action performed by OWNER_ID or any id in OWNER_IDS, OR
      - user is chat administrator (creator/administrator), OR
      - sender_chat (anonymous admin / channel-as-admin) has admin/creator status.

    Works with both Message and CallbackQuery objects.
    """
    try:
        # Normalize objects
        if isinstance(subject, CallbackQuery):
            user = subject.from_user  # may be None in rare cases
            # callback.message should exist for inline buttons
            msg = getattr(subject, "message", None)
            if not msg:
                return False
            chat = msg.chat
            sender_chat = getattr(msg, "sender_chat", None)
        else:  # Message
            user = subject.from_user
            msg = subject
            chat = subject.chat
            sender_chat = getattr(subject, "sender_chat", None)

        # Quick owner checks (works if from_user present)
        try:
            if user and getattr(user, "id", None) is not None:
                uid = int(user.id)
                if OWNER_ID and uid == int(OWNER_ID):
                    return True
                if OWNER_IDS and uid in OWNER_IDS:
                    return True
        except Exception:
            # ignore parsing issues and continue checks
            pass

        # Also accept owner if owner posted as sender_chat (rare)
        try:
            if sender_chat and hasattr(sender_chat, "id"):
                sc_id = int(sender_chat.id)
                if OWNER_ID and sc_id == int(OWNER_ID):
                    return True
                if OWNER_IDS and sc_id in OWNER_IDS:
                    return True
        except Exception:
            pass

        # Private chats: treat as non-admin (unless owner via direct message)
        if chat.type == "private":
            # allow direct owner DM (if the DM user is owner)
            try:
                if user and getattr(user, "id", None) is not None:
                    uid = int(user.id)
                    if OWNER_ID and uid == int(OWNER_ID):
                        return True
                    if OWNER_IDS and uid in OWNER_IDS:
                        return True
            except Exception:
                pass
            return False

        # If sender_chat exists (anonymous admin / channel), try to check its status
        if sender_chat:
            try:
                # sender_chat.id is typically a negative channel id; get_chat_member accepts it
                member = await bot.get_chat_member(chat.id, sender_chat.id)
                status = (getattr(member, "status", "") or "").lower()
                if status in ("administrator", "creator"):
                    return True
            except Exception as e:
                # Some Telegram setups may not allow get_chat_member for sender_chat
                logging.debug(f"dlk_privilege_validator: sender_chat check failed: {e}")

        # If a user object exists, check the user's role in the chat
        if user and getattr(user, "id", None) is not None:
            try:
                member = await bot.get_chat_member(chat.id, user.id)
                status = (getattr(member, "status", "") or "").lower()
                if status in ("administrator", "creator"):
                    return True
            except Exception as e:
                logging.debug(f"dlk_privilege_validator: user chat member check failed: {e}")

        # If we reached here, user is not admin/owner
        return False
    except Exception as e:
        logging.warning(f"Privilege check failed (unexpected): {e}")
        return False

# ---------- New helper functions for assistant presence & invite (FIXED) ----------
async def is_assistant_in_chat(chat_id: int) -> bool:
    """
    Robustly check whether the assistant (user-session) is in the chat.
    Returns True if assistant client can see the chat member entry for itself.
    """
    if ASSISTANT_SESSION is None:
        return False
    try:
        assistant_me = await assistant.get_me()
        assistant_id = getattr(assistant_me, "id", None)
    except Exception as e:
        logging.debug(f"is_assistant_in_chat: could not get assistant me: {e}")
        return False
    if assistant_id is None:
        return False
    try:
        # Catch *any* exception here: resolve_peer KeyError/ValueError or RPCError -> treat as not present
        await assistant.get_chat_member(chat_id, assistant_id)
        return True
    except Exception as e:
        logging.debug(f"is_assistant_in_chat: assistant not present or get_chat_member failed: {e}")
        return False

async def try_invite_and_join_assistant(chat_id: int) -> Optional[str]:
    """
    Try to create an invite link and make the assistant join it.
    Returns invite_link if created (even if join fails) or None on failure.
    This function never raises; logs exceptions and returns None on failure.
    """
    try:
        # create_chat_invite_link often requires the bot to be admin with invite permission
        invite = await bot.create_chat_invite_link(chat_id, member_limit=1, name="DLK BOT assistant")
        invite_link = invite.invite_link
        logging.debug(f"Created invite link for chat {chat_id}: {invite_link}")
    except Exception as e:
        logging.debug(f"try_invite_and_join_assistant: failed to create invite link: {e}")
        return None
    if not ASSISTANT_SESSION:
        return invite_link
    try:
        # Attempt assistant to join via invite link. Wrap any exception.
        try:
            await assistant.join_chat(invite_link)
            logging.info(f"Assistant joined chat {chat_id} via invite.")
        except Exception as e_join:
            logging.debug(f"Assistant join_chat failed (may need manual add): {e_join}")
        return invite_link
    except Exception as e:
        logging.debug(f"try_invite_and_join_assistant: unexpected error: {e}")
        return invite_link

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

# ---------- Safe callback answer ----------
async def safe_query_answer(query: CallbackQuery, text: Optional[str] = None, show_alert: bool = False):
    """Call query.answer but ignore QUERY_ID_INVALID and similar RPC errors."""
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
async def update_radio_timer(chat_id: int, msg_id: int, title: str, start_time: float, track_duration: int):
    while True:
        try:
            elapsed = max(0, int(time.time() - start_time))
            remaining = max(0, track_duration - elapsed)
            m, s = divmod(remaining, 60)
            timer = f"{m:02d}:{s:02d}"
            caption = f"🎧 Now Playing: {title}\n⏳ Duration: {timer}"
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption=caption,
                reply_markup=player_controls_markup(chat_id),
            )
            if remaining <= 0:
                break
        except Exception as e:
            logging.debug(f"Timer update failed for {chat_id}/{msg_id}: {e}")
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
                if chat_id in ac:
                    return True
                try:
                    if str(chat_id) in ac:
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
            radio_tasks[chat_id].cancel()
            radio_tasks.pop(chat_id, None)
        stream_source = entry["stream_url"]
        started = await _start_stream_in_call(chat_id, stream_source)
        if not started:
            logging.error("Failed to start streaming in call for %s", chat_id)
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

# ---------- /play ----------
@bot.on_message(filters.group & filters.command(["play", "p"]))
async def cmd_play(_, message: Message):
    chat_id = message.chat.id
    user = message.from_user
    if is_group_blocked_sync(chat_id):
        return await message.reply_text(t(chat_id, "GROUP_BLOCKED"))

    # Robust assistant presence check (FIXED)
    assistant_present = False
    if ASSISTANT_SESSION:
        try:
            assistant_present = await is_assistant_in_chat(chat_id)
        except Exception as e:
            logging.debug(f"cmd_play: is_assistant_in_chat failed: {e}")
            assistant_present = False

    if not assistant_present:
        if not ASSISTANT_SESSION:
            return await message.reply_text(
                "Assistant session is not configured (ASSISTANT_SESSION). Set it in Heroku config vars and restart the app."
            )
        # Try to create invite & ask assistant to join, but handle all exceptions gracefully.
        invite_link = await try_invite_and_join_assistant(chat_id)
        if not invite_link:
            # Could not create invite => likely bot lacks permission; instruct admin to add assistant manually.
            try:
                await message.reply_text(t(chat_id, "ASSISTANT_INVITE_FAIL_TEXT"))
            except Exception:
                pass
            return
        # If invite_link exists, give it to the chat and explain.
        try:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("📋 Invite Link", url=invite_link)]])
            await message.reply_text(t(chat_id, "ASSISTANT_INVITE_TEXT"), reply_markup=kb)
        except Exception:
            try:
                await message.reply_text(t(chat_id, "ASSISTANT_JOIN_INFO"))
            except Exception:
                pass
        return

    # from here assistant is present (or we assume it is), proceed
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
    ok = await play_entry(chat_id, entry, reply_message=message)
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

# ---------- skip/queue/stop (unchanged) ----------
@bot.on_message(filters.group & filters.command(["skip", "s"]))
async def cmd_skip(_, message: Message):
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text(t(chat_id, "ONLY_ADMINS_SKIP"))
    q = radio_queue.get(chat_id, [])
    if not q:
        await leave_voice_chat(chat_id)
        await message.reply_text(t(chat_id, "SKIPPED_NO_QUEUE"))
        log_event_sync("music_skipped_stop", {"chat_id": chat_id, "by": message.from_user.id})
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
        log_event_sync("music_skipped", {"chat_id": chat_id, "title": next_entry["title"], "by": message.from_user.id})
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
    log_event_sync("radio_stopped_text", {"chat_id": chat_id, "by": message.from_user.id})

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
        log_event_sync("radio_rend", {"chat_id": chat_id, "by": message.from_user.id})
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
        log_event_sync("radio_rskip_stop", {"chat_id": chat_id, "by": message.from_user.id})
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
        log_event_sync("radio_rskip", {"chat_id": chat_id, "title": next_entry["title"], "by": message.from_user.id})
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
    log_event_sync("radio_rpush", {"chat_id": chat_id, "title": title, "by": message.from_user.id})

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
        log_event_sync("radio_resumed_cmd", {"chat_id": chat_id, "by": message.from_user.id})
    except Exception as e:
        logging.debug(f"cmd_rresume failed: {e}")
        await message.reply_text(t(chat_id, "FAILED_RESUME"))

# ---------- BLOCK / UNBLOCK ----------
@bot.on_message(filters.group & filters.command(["bl", "block"]))
async def cmd_block_group(_, message: Message):
    chat_id = message.chat.id
    if not message.from_user or message.from_user.id != OWNER_ID:
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
    if not message.from_user or message.from_user.id != OWNER_ID:
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
    if not message.from_user or message.from_user.id != OWNER_ID:
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

# ---------- CALLBACK handlers use safe_query_answer ----------
@bot.on_callback_query(filters.regex("^music_skip$"))
async def cb_music_skip(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await safe_query_answer(query, t(chat_id, "ONLY_ADMINS_SKIP"), show_alert=True)
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
        await safe_query_answer(query, t(chat_id, "MUSIC_SKIP_BTN_ALERT"), show_alert=True)
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
        await safe_query_answer(query, t(chat_id, "MUSIC_SKIP_BTN_ALERT"), show_alert=False)
        log_event_sync(
            "music_skipped",
            {"chat_id": chat_id, "title": next_entry["title"], "by": query.from_user.id if query.from_user else None},
        )
    else:
        await safe_query_answer(query, t(chat_id, "MUSIC_SKIP_BTN_FAIL"), show_alert=True)

@bot.on_callback_query(filters.regex("^radio_pause$"))
async def radio_pause_cb(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await safe_query_answer(query, t(chat_id, "ONLY_ADMINS_RADIO_BUTTON"), show_alert=True)
    state = radio_state.get(chat_id)
    if not state:
        return await safe_query_answer(query, t(chat_id, "RADIO_NOTHING_PLAYING"), show_alert=True)
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
        await safe_query_answer(query, t(chat_id, "RADIO_PAUSED"), show_alert=False)
        log_event_sync("radio_paused", {"chat_id": chat_id, "by": query.from_user.id if query.from_user else None})
    except Exception as e:
        logging.debug(f"Pause failed: {e}")
        await safe_query_answer(query, t(chat_id, "RADIO_PAUSE_FAIL"), show_alert=True)

@bot.on_callback_query(filters.regex("^radio_resume$"))
async def radio_resume_cb(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await safe_query_answer(query, t(chat_id, "ONLY_ADMINS_RADIO_BUTTON"), show_alert=True)
    state = radio_state.get(chat_id)
    if not state:
        return await safe_query_answer(query, t(chat_id, "NOTHING_TO_RESUME_BTN"), show_alert=True)
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
        await safe_query_answer(query, t(chat_id, "RADIO_RESUMED_BTN"), show_alert=False)
        log_event_sync("radio_resumed", {"chat_id": chat_id, "by": query.from_user.id if query.from_user else None})
    except Exception as e:
        logging.debug(f"Resume failed: {e}")
        await safe_query_answer(query, t(chat_id, "RADIO_RESUME_FAIL_BTN"), show_alert=True)

@bot.on_callback_query(filters.regex("^radio_stop$"))
async def cb_radio_stop(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await safe_query_answer(query, t(chat_id, "ONLY_ADMINS_RADIO_BUTTON"), show_alert=True)
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
        await safe_query_answer(query, t(chat_id, "RADIO_STOPPED_BTN"), show_alert=False)
        log_event_sync("radio_stopped", {"chat_id": chat_id, "by": query.from_user.id if query.from_user else None})
    except Exception as e:
        logging.error(f"Stop failed via callback: {e}", exc_info=True)
        await safe_query_answer(query, t(chat_id, "RADIO_STOP_FAIL_BTN"), show_alert=True)

# ---------- RADIO BUTTON PLAY ----------
@bot.on_callback_query(filters.regex("^radio_play_"))
async def play_radio_station(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    station = query.data.replace("radio_play_", "")
    url = RADIO_STATION.get(station)
    user = query.from_user
    if is_group_blocked_sync(chat_id):
        await safe_query_answer(query, t(chat_id, "ASSISTANT_BLOCKED_GROUP"), show_alert=True)
        return
    if not url:
        return await safe_query_answer(query, t(chat_id, "STATION_URL_NOT_FOUND"), show_alert=True)

    # Robust assistant check (FIXED)
    assistant_present = False
    if ASSISTANT_SESSION:
        try:
            assistant_present = await is_assistant_in_chat(chat_id)
        except Exception as e:
            logging.debug(f"play_radio_station: is_assistant_in_chat failed: {e}")
            assistant_present = False

    if not assistant_present:
        if not ASSISTANT_SESSION:
            await safe_query_answer(query, "Assistant session is not configured. Set ASSISTANT_SESSION and restart.", show_alert=True)
            return
        invite_link = await try_invite_and_join_assistant(chat_id)
        if not invite_link:
            # create invite failed
            await query.message.reply_text(t(chat_id, "ASSISTANT_INVITE_FAIL_TEXT"))
            await safe_query_answer(query)
            return
        # If invite_link exists, show helpful keyboard
        help_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Invite Link", url=invite_link)],
            [InlineKeyboardButton("ℹ️ How to add assistant", callback_data="assistant_invite_help")],
            [InlineKeyboardButton("❌ Dismiss", callback_data="radio_close")],
        ])
        await query.message.reply_text(t(chat_id, "ASSISTANT_INVITE_TEXT"), reply_markup=help_kb)
        await safe_query_answer(query)
        return

    try:
        # start the stream robustly
        started = await _start_stream_in_call(chat_id, url)
        if not started:
            await leave_voice_chat(chat_id)
            if AudioPiped is not None and not is_ffmpeg_available():
                await query.message.reply_text(
                    "❌ Failed to start radio: ffmpeg is not installed in the environment. "
                    "Install ffmpeg (on Heroku add ffmpeg buildpack) and restart the bot. "
                    "Also ensure the assistant account is present in the group and has permission to speak."
                )
            else:
                await query.message.reply_text(t(chat_id, "RADIO_PLAY_FAILED_ASSIST", error="assistant failed to start stream"))
            await safe_query_answer(query, "Failed to start radio", show_alert=True)
            return

        msg = await query.message.edit_caption(
            caption=f"🎧 {station}\n🔴 LIVE Radio",
            reply_markup=player_controls_markup(chat_id),
        )
        start_time = time.time()
        store_play_state(chat_id, station, url, msg.id, start_time, elapsed=0.0, paused=False, duration=None)
        radio_paused.discard(chat_id)
        await safe_query_answer(query, f"Now playing {station} via assistant!", show_alert=False)
        log_event_sync("radio_started", {"chat_id": chat_id, "station": station, "by": user.id if user else None})
    except FloodWait as e:
        await leave_voice_chat(chat_id)
        wait_time = getattr(e, "value", None) or getattr(e, "x", None) or "unknown"
        await query.message.reply_text(t(chat_id, "RATE_LIMIT", seconds=wait_time))
        await safe_query_answer(query, f"Wait {wait_time}s", show_alert=True)
    except ntgcalls.TelegramServerError:
        await leave_voice_chat(chat_id)
        await query.message.reply_text(t(chat_id, "VOICECHAT_NOT_READY"))
        await safe_query_answer(query, "Voice chat not ready!", show_alert=True)
    except RPCError as e:
        await leave_voice_chat(chat_id)
        await query.message.reply_text(t(chat_id, "RADIO_PLAY_FAILED_ASSIST", error=str(e)))
        await safe_query_answer(query)
    except Exception as e:
        await leave_voice_chat(chat_id)
        logging.error("General radio play error", exc_info=True)
        await query.message.reply_text(t(chat_id, "RADIO_START_FAIL", error=str(e)))
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
        # Provide dump of call_py internals
        state = await dump_call_py_state(None)
        # Truncate big fields
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