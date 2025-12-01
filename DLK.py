# main.py
import os
import re
import time
import asyncio
import logging
import random
import inspect
from typing import Union, Optional, Dict, Any, List
from urllib.parse import urlparse, parse_qs

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

try:
    from pyrogram.errors import SessionRevoked, Unauthorized
except Exception:
    SessionRevoked = None
    Unauthorized = None

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

# load .env if exists (useful for local dev)
load_dotenv()

# ---- Basic logging ----
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ---- ENV / CONFIG ----
def env_int(key: str, default: Optional[int] = None) -> Optional[int]:
    v = os.environ.get(key)
    if v is None or v.strip() == "":
        return default
    try:
        return int(v)
    except Exception:
        return default

API_ID = env_int("API_ID")
API_HASH = os.environ.get("API_HASH", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

ASSISTANT_SESSION = os.environ.get("ASSISTANT_SESSION")
if ASSISTANT_SESSION:
    ASSISTANT_SESSION = ASSISTANT_SESSION.strip() or None
else:
    ASSISTANT_SESSION = None

OWNER_ID = env_int("OWNER_ID")

MONGO_URI = os.environ.get("MONGO_URI")
MONGO_DBNAME = os.environ.get("MONGO_DBNAME", "dlk_radio")
LOG_CHANNEL_ID = os.environ.get("LOG_CHANNEL_ID", "").strip() or None

YT_DLP_COOKIES = os.environ.get("YT_DLP_COOKIES", "").strip() or None

DEV_LINK = os.environ.get("DEV_LINK", "https://t.me/DLKDEVELOPERS")
SUPPORT_LINK = os.environ.get("SUPPORT_LINK", "https://t.me/DevDLK")

THUMB_CACHE_DIR = "cache"
os.makedirs(THUMB_CACHE_DIR, exist_ok=True)
DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

DEFAULT_FALLBACK_DURATION = 240  # seconds fallback

# Minimal RADIO_STATION map (keep yours, shortened here for brevity)
RADIO_STATION = {
    "SirasaFM": "http://live.trusl.com:1170/;",
    "HelaNadaFM": "https://stream-176.zeno.fm/9ndoyrsujwpvv",
    "Radio Plus Hitz": "https://altair.streamerr.co/stream/8054",
}

# runtime state
radio_tasks: Dict[int, asyncio.Task] = {}
radio_paused = set()
radio_state: Dict[int, Dict[str, Any]] = {}
radio_queue: Dict[int, List[Dict[str, Any]]] = {}
track_watchers: Dict[int, asyncio.Task] = {}
bot_start_time = time.time()

BOT_USERNAME = None
ASSISTANT_USERNAME = None
ASSISTANT_ID = None
ASSISTANT_STARTED = False

# ---- Clients ----
if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.error("Missing API_ID, API_HASH or BOT_TOKEN in environment. Exiting.")
    raise SystemExit(1)

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

# ---- small helpers & i18n (short) ----
TRANSLATIONS = {
    "en": {
        "GROUP_BLOCKED": "❌ This group is blocked from using DLK BOT.",
        "ONLY_ADMINS": "Only admins can use this.",
        "PLAY_USAGE": "Usage: /play <YouTube url or search terms> OR reply to an audio/voice file and use /play",
        "SEARCHING_STREAM": "🔎 Searching and preparing stream...",
        "YTDLP_FAIL": "❌ Could not extract audio stream. Ensure yt-dlp is installed and cookies.txt set if needed.",
        "PREPARING_AUDIO_REPLY": "Preparing your audio reply...",
        "NOW_PLAYING": "▶️ Now playing: {title}",
        "ADDED_QUEUE": "➕ Added to queue: {title}",
        "BOT_STOPPED": "DLK bot stopped & cleaned up.",
        "ASSISTANT_NOT_IN_GROUP": "Assistant is not in this group. Please add the assistant account and try again.",
        "ASSISTANT_INVITE_TEXT": "Assistant not in group. I've created an invite link — add the assistant account manually and give it permission to speak.",
        "ASSISTANT_JOIN_INFO": "🤖 Assistant has joined the group. Please grant it permission to manage voice chats and speak.",
        "RADIO_ENDED": "✅ Radio ended and assistant left the voice chat.",
        "RADIO_START_FAIL": "❌ Failed to start radio! Error: {error}",
    }
}
DEFAULT_LANG = "en"
def t(chat_id: int, key: str, **kwargs):
    text = TRANSLATIONS.get(DEFAULT_LANG, {}).get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
    return text

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

# ---- yt-dlp extraction ----
def extract_audio_url(query: str) -> Optional[Dict[str, Any]]:
    if youtube_dl is None:
        logger.warning("yt_dlp not installed.")
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
            if "entries" in info and isinstance(info["entries"], list):
                if info["entries"]:
                    info = info["entries"][0]
                else:
                    return None
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
                logger.warning("yt_dlp: no stream_url")
                return None
            duration = info.get("duration")
            try:
                duration = int(duration) if duration is not None else None
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
        logger.warning(f"yt_dlp failed: {e}")
        return None

# ---- thumbnails + image helpers (kept minimal) ----
def clear_title(text: str) -> str:
    parts = (text or "").split(" ")
    title = ""
    for i in parts:
        if len(title) + len(i) < 60:
            title += " " + i
    return title.strip()

# basic download helper for thumbnail
async def _download_file(url: str, dest: str) -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=20) as resp:
                if resp.status != 200:
                    return None
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                content = await resp.read()
                async with aiofiles.open(dest, mode="wb") as f:
                    await f.write(content)
                return dest
    except Exception as e:
        logger.debug(f"_download_file failed: {e}")
        try:
            if os.path.exists(dest):
                os.remove(dest)
        except Exception:
            pass
        return None

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
        out_path = os.path.join(THUMB_CACHE_DIR, f"{out_key}.png")
        background.save(out_path)
        return out_path
    except Exception as e:
        logger.debug(f"_process_image failed: {e}")
        return None

async def get_thumb_from_url_or_webpage(thumbnail_url: Optional[str], webpage: Optional[str], title: str) -> Optional[str]:
    if thumbnail_url and thumbnail_url.startswith("http"):
        key = re.sub(r"[^0-9A-Za-z_-]", "_", thumbnail_url)[:40]
        tmp = os.path.join(THUMB_CACHE_DIR, f"tmp_{key}.jpg")
        dl = await _download_file(thumbnail_url, tmp)
        if dl:
            proc = await _process_image_and_overlay(dl, key, title)
            try:
                os.remove(dl)
            except Exception:
                pass
            return proc
    if webpage:
        vid_id = get_youtube_id(webpage)
        if vid_id and youtube_dl is not None:
            try:
                ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
                with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid_id}", download=False)
                    thumb = info.get("thumbnail")
                    if thumb:
                        return await get_thumb_from_url_or_webpage(thumb, None, title)
            except Exception:
                pass
    return None

# ---- DB / logging ----
def init_db_sync():
    global db_client, db
    if not MONGO_URI or MongoClient is None:
        logger.info("DB disabled or pymongo not installed.")
        return
    db_client = MongoClient(MONGO_URI)
    db = db_client[MONGO_DBNAME]
    try:
        db.blocked.create_index("chat_id")
        db.logs.create_index("ts")
        db.langs.create_index("chat_id", unique=True)
    except Exception:
        pass
    logger.info("Connected to MongoDB.")

def log_event_sync(event_type: str, data: dict):
    try:
        if db is not None:
            db.logs.insert_one({"ts": time.time(), "type": event_type, "data": data})
    except Exception as e:
        logger.warning(f"Failed to write log to DB: {e}")
    if LOG_CHANNEL_ID:
        async def _send():
            try:
                target = LOG_CHANNEL_ID
                if not target.startswith("@"):
                    try:
                        target = int(target)
                    except Exception:
                        pass
                await bot.send_message(target, f"🔔 <b>{event_type}</b>\n<pre>{data}</pre>", disable_web_page_preview=True)
            except Exception as e:
                logger.debug(f"Failed to send log: {e}")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_send())
        except RuntimeError:
            try:
                asyncio.get_event_loop().create_task(_send())
            except Exception:
                pass

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
    db.blocked.update_one({"chat_id": chat_id}, {"$set": {"chat_id": chat_id, "by": by_user, "reason": reason, "ts": time.time()}}, upsert=True)

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
        if user and OWNER_ID and user.id == OWNER_ID:
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
        logger.warning(f"Privilege check failed: {e}")
        return False

# UI helpers (radio list, controls)
def radio_buttons(page: int = 0, per_page: int = 6):
    stations = sorted(RADIO_STATION.keys())
    total_pages = (len(stations) - 1) // per_page + 1
    start = page * per_page
    current = stations[start:start+per_page]
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

# Safe updates handler fix (from your original)
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

# Timer / watcher / play logic (kept similar to yours, but robustified)
async def update_radio_timer(chat_id: int, msg_id: int, title: str, start_time: float, track_duration: int):
    while True:
        try:
            elapsed = max(0, int(time.time() - start_time))
            remaining = max(0, track_duration - elapsed)
            m, s = divmod(remaining, 60)
            timer = f"{m:02d}:{s:02d}"
            caption = f"🎧 Now Playing: {title}\n⏳ Duration: {timer}"
            try:
                await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=caption, reply_markup=player_controls_markup(chat_id))
            except Exception:
                pass
            if remaining <= 0:
                break
        except Exception as e:
            logger.debug(f"Timer update failed for {chat_id}/{msg_id}: {e}")
            break
        await asyncio.sleep(5)

async def _safe_call_py_method(method_name: str, *args, **kwargs):
    try:
        if not ASSISTANT_STARTED:
            logger.debug(f"assistant not started, skipping {method_name}")
            return None
        if not hasattr(call_py, method_name):
            return None
        attr = getattr(call_py, method_name)
        result = attr(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
    except Exception as e:
        logger.debug(f"_safe_call_py_method {method_name} failed: {e}")
        return None

async def _force_leave_call(chat_id: int):
    try:
        await call_py.leave_group_call(chat_id)
    except Exception as e:
        logger.debug(f"_force_leave_call leave_group_call failed {chat_id}: {e}")
        try:
            await _safe_call_py_method("leave_call", chat_id)
        except Exception as e2:
            logger.debug(f"_force_leave_call leave_call fallback failed {chat_id}: {e2}")

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
            logger.debug(f"force leave vc failed {chat_id}: {e}")
    except Exception as e:
        logger.warning(f"leave_voice_chat failed {chat_id}: {e}")

def store_play_state(chat_id: int, title: str, url: str, msg_id: int, start_time: Optional[float], elapsed: float = 0.0, paused: bool = False, duration: Optional[int] = None):
    state = {"chat_id": chat_id, "station": title, "url": url, "msg_id": msg_id, "start_time": start_time, "elapsed": elapsed, "paused": paused, "duration": duration, "ts": time.time()}
    radio_state[chat_id] = state

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
                await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=t(chat_id, "BOT_STOPPED"), reply_markup=None)
            except Exception:
                pass
            log_event_sync("music_track_autostop", {"chat_id": chat_id})
    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.debug(f"track_watcher error {chat_id}: {e}")

async def play_entry(chat_id: int, entry: dict, reply_message: Optional[Message] = None):
    try:
        if chat_id in radio_tasks:
            radio_tasks[chat_id].cancel()
            radio_tasks.pop(chat_id, None)
        stream_source = entry["stream_url"]
        await _safe_call_py_method("play", chat_id, MediaStream(stream_source))
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
                msg = await bot.send_photo(chat_id, photo=thumb_path, caption=caption, reply_markup=player_controls_markup(chat_id))
            else:
                msg = await bot.send_photo(chat_id, photo="https://files.catbox.moe/3o9qj5.jpg", caption=caption, reply_markup=player_controls_markup(chat_id))
        except Exception:
            msg = await bot.send_photo(chat_id, photo="https://files.catbox.moe/3o9qj5.jpg", caption=caption, reply_markup=player_controls_markup(chat_id))
        duration = entry.get("duration")
        try:
            if duration is not None:
                duration = int(duration)
        except Exception:
            duration = None
        if not duration or duration <= 0:
            duration = DEFAULT_FALLBACK_DURATION
        start_time = time.time()
        store_play_state(chat_id, title, entry.get("stream_url"), msg.id, start_time, elapsed=0.0, paused=False, duration=duration)
        radio_paused.discard(chat_id)
        radio_tasks[chat_id] = asyncio.create_task(update_radio_timer(chat_id, msg.id, title, start_time, duration))
        if chat_id in track_watchers:
            try:
                track_watchers[chat_id].cancel()
            except Exception:
                pass
        track_watchers[chat_id] = asyncio.create_task(track_watcher(chat_id, duration, msg.id))
        log_event_sync("music_started", {"chat_id": chat_id, "title": title})
        return True
    except Exception:
        logger.exception("Play entry failed")
        try:
            await leave_voice_chat(chat_id)
        except Exception:
            pass
        return False

# ---------- Handlers (play / radio menu / skip / stop) ----------
@bot.on_message(filters.group & filters.command(["play", "p"]))
async def cmd_play(_, message: Message):
    chat_id = message.chat.id
    user = message.from_user
    if is_group_blocked_sync(chat_id):
        return await message.reply_text(t(chat_id, "GROUP_BLOCKED"))

    # If assistant wasn't started, assistant actions can't be used.
    assistant_id = None
    if ASSISTANT_STARTED:
        try:
            assistant_user = await assistant.get_me()
            assistant_id = assistant_user.id
        except Exception as e:
            logger.debug(f"assistant.get_me failed in cmd_play: {e}")
            assistant_id = None

    assistant_present = False
    if assistant_id and ASSISTANT_STARTED:
        try:
            await assistant.get_chat_member(chat_id, assistant_id)
            assistant_present = True
        except RPCError:
            assistant_present = False

    if not assistant_present:
        if not ASSISTANT_STARTED:
            return await message.reply_text(t(chat_id, "ASSISTANT_NOT_IN_GROUP"))
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
        # prepare local audio (simple version)
        try:
            reply_msg = message.reply_to_message
            media_field = None
            if reply_msg.voice:
                media_field = reply_msg.voice
            elif reply_msg.audio:
                media_field = reply_msg.audio
            elif reply_msg.document:
                media_field = reply_msg.document
            if media_field:
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
                title = getattr(media_field, "title", None) or getattr(media_field, "file_name", None) or reply_msg.caption or "Telegram Audio"
                entry = {"title": title, "stream_url": local_path, "webpage": None, "thumbnail": None, "duration": getattr(media_field, "duration", None), "is_local": True}
                info_msg = await message.reply_text(t(chat_id, "PREPARING_AUDIO_REPLY"))
        except Exception as e:
            logger.debug(f"prepare_entry_from_reply failed: {e}")
            entry = None

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
        entry = {"title": info.get("title"), "stream_url": info.get("stream_url"), "webpage": info.get("webpage_url"), "thumbnail": info.get("thumbnail"), "duration": info.get("duration"), "is_local": False}

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
        try:
            if info_msg:
                await info_msg.edit_text(t(chat_id, "YTDLP_FAIL"))
        except Exception:
            pass

@bot.on_message(filters.group & filters.command(["radio"]))
async def cmd_radio_menu(_, message: Message):
    chat_id = message.chat.id
    if is_group_blocked_sync(chat_id):
        return await message.reply_text(t(chat_id, "GROUP_BLOCKED"))
    kb = radio_buttons(0)
    await message.reply_text("📻 Radio Stations - choose one:", reply_markup=kb)

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
        logger.debug(f"radio_page handler failed: {e}")
        try:
            await query.answer("Failed to load page.", show_alert=True)
        except Exception:
            pass

@bot.on_callback_query(filters.regex("^radio_play_"))
async def play_radio_station(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    station = query.data.replace("radio_play_", "")
    url = RADIO_STATION.get(station)
    user = query.from_user
    if is_group_blocked_sync(chat_id):
        await query.answer(t(chat_id, "GROUP_BLOCKED"), show_alert=True)
        return
    if not url:
        return await query.answer("Station URL not found!", show_alert=True)

    if not ASSISTANT_STARTED:
        await query.answer(t(chat_id, "ASSISTANT_NOT_IN_GROUP"), show_alert=True)
        return

    try:
        try:
            assistant_user = await assistant.get_me()
            assistant_id = assistant_user.id
        except Exception as e:
            logger.debug(f"assistant.get_me failed in play_radio_station: {e}")
            assistant_id = None
        assistant_present = False
        if assistant_id:
            try:
                await assistant.get_chat_member(chat_id, assistant_id)
                assistant_present = True
            except RPCError:
                assistant_present = False
        if not assistant_present:
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
                except Exception as e_join:
                    logger.warning(f"Assistant failed to join via invite: {e_join}")
                    help_kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("📋 Invite Link", url=invite_link)],
                        [InlineKeyboardButton("❌ Dismiss", callback_data="radio_close")],
                    ])
                    await query.message.reply_text(t(chat_id, "ASSISTANT_INVITE_TEXT"), reply_markup=help_kb)
                    return
            except Exception as e_inv:
                logger.warning(f"Cannot create invite/join assistant: {e_inv}")
                await query.message.reply_text(t(chat_id, "ASSISTANT_JOIN_INFO"))
                return

        await _safe_call_py_method("play", chat_id, MediaStream(url))
        try:
            await query.message.edit_caption(caption=f"🎧 {station}\n🔴 LIVE Radio", reply_markup=player_controls_markup(chat_id))
        except Exception:
            pass
        start_time = time.time()
        store_play_state(chat_id, station, url, query.message.id, start_time, elapsed=0.0, paused=False, duration=None)
        radio_paused.discard(chat_id)
        await query.answer(f"Now playing {station} via assistant!", show_alert=False)
        log_event_sync("radio_started", {"chat_id": chat_id, "station": station, "by": user.id if user else None})
    except FloodWait as e:
        await leave_voice_chat(chat_id)
        wait_time = getattr(e, "value", None) or getattr(e, "x", None) or "unknown"
        await query.message.reply_text(f"Rate limit: wait {wait_time}s")
        await query.answer(f"Wait {wait_time}s", show_alert=True)
    except ntgcalls.TelegramServerError:
        await leave_voice_chat(chat_id)
        await query.message.reply_text("Voice chat not ready!")
        await query.answer("Voice chat not ready!", show_alert=True)
    except RPCError as e:
        await leave_voice_chat(chat_id)
        await query.message.reply_text(t(chat_id, "RADIO_START_FAIL", error=str(e)))
    except Exception as e:
        await leave_voice_chat(chat_id)
        logger.exception("General radio play error")
        await query.message.reply_text(t(chat_id, "RADIO_START_FAIL", error=str(e)))

# Start / main boot
if __name__ == "__main__":
    logger.info("Starting DLK Bot...")
    try:
        init_db_sync()
    except Exception as e:
        logger.warning(f"Database initialization failed: {e}")

    # Start assistant if session provided
    ASSISTANT_STARTED = False
    if ASSISTANT_SESSION:
        try:
            assistant.start()
            ASSISTANT_STARTED = True
            logger.info("Assistant (user) client started.")
        except Exception as e:
            logger.warning(f"Assistant start failed: {e}")
            ASSISTANT_STARTED = False
    else:
        logger.info("ASSISTANT_SESSION not set - assistant disabled. Voice features limited.")

    if ASSISTANT_STARTED:
        try:
            call_py.start()
            logger.info("PyTgCalls started.")
        except Exception as e:
            logger.warning(f"PyTgCalls start failed: {e}")

    # Start bot
    try:
        bot.start()
    except Exception as e:
        logger.error(f"Bot start failed: {e}")
        raise

    try:
        if ASSISTANT_STARTED:
            try:
                me = assistant.get_me()
                ASSISTANT_USERNAME = me.username
                ASSISTANT_ID = me.id
            except Exception:
                ASSISTANT_USERNAME = "assistant"
                ASSISTANT_ID = None
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

    log_event_sync("bot_started", {"ts": time.time(), "owner": OWNER_ID, "assistant_started": ASSISTANT_STARTED})

    from pyrogram import idle
    try:
        idle()
    finally:
        try:
            if ASSISTANT_STARTED:
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
