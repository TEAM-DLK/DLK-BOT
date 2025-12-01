# dlk_radio_bot_fixed.py
# Fixed and consolidated version of the DLK radio bot.
# Make sure environment variables are set and system dependencies (ffmpeg, libopus, libsndfile, libsodium) installed.

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
except Exception:
    # compatibility fallback - create a compatible subclass if missing
    from pyrogram.errors.exceptions.forbidden_403 import Forbidden
    class GroupcallForbidden(Forbidden):
        pass
    import pyrogram.errors
    pyrogram.errors.GroupcallForbidden = GroupcallForbidden

from pyrogram.client import Client as _PyroClient
_original_handle_updates = _PyroClient.handle_updates

async def _safe_handle_updates(self, updates):
    try:
        return await _original_handle_updates(self, updates)
    except ValueError as e:
        # ignore pyrogram ValueError when it complains about "Peer id invalid: -100..."
        # this often happens when an incoming update references a peer id not present in local storage
        msg = str(e)
        if msg.startswith("Peer id invalid: -100") or "Peer id invalid:" in msg:
            logging.debug(f"Ignored invalid peer id in updates: {e}")
            return
        # otherwise re-raise
        raise

# pytgcalls for voice - note: must match installed version
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream

from dotenv import load_dotenv
load_dotenv()

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

# ----------------- Basic config / env parsing -----------------
def _get_int_env(name: str, default: int = 0) -> int:
    v = os.environ.get(name, "")
    try:
        return int(v)
    except Exception:
        return default

API_ID = _get_int_env("API_ID")
API_HASH = os.environ.get("API_HASH", "") or ""
BOT_TOKEN = os.environ.get("BOT_TOKEN", "") or ""
ASSISTANT_SESSION = os.environ.get("ASSISTANT_SESSION")
if ASSISTANT_SESSION:
    ASSISTANT_SESSION = ASSISTANT_SESSION.strip() or None
else:
    ASSISTANT_SESSION = None

OWNER_ID = _get_int_env("OWNER_ID")
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

DEFAULT_FALLBACK_DURATION = 240

# ----------------- Radio stations (keep your list) -----------------
RADIO_STATION = {
    "SirasaFM": "http://live.trusl.com:1170/;",
    "HelaNadaFM": "https://stream-176.zeno.fm/9ndoyrsujwpvv",
    # ... keep full station list from original ...
    "JAM FM": "http://stream.jam.fm/jamfm-nmr/mp3-192/",
}

# In-memory state
radio_tasks: Dict[int, asyncio.Task] = {}
radio_paused = set()
radio_state: Dict[int, Dict[str, Any]] = {}
radio_queue: Dict[int, List[Dict[str, Any]]] = {}
track_watchers: Dict[int, asyncio.Task] = {}
bot_start_time = time.time()

BOT_USERNAME = None
ASSISTANT_USERNAME = None
ASSISTANT_ID = None

# ---------- Clients ----------
# Bot (bot token) client
bot = Client("dlk_radio_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Assistant (user) client. Use session_string if provided; else we still create a named session so features gracefully degrade.
assistant_kwargs = {"api_id": API_ID, "api_hash": API_HASH}
if ASSISTANT_SESSION:
    # newer pyrogram supports session_string param; but to be defensive, pass as session_string only if exists
    assistant = Client("assistant_account", session_string=ASSISTANT_SESSION, **assistant_kwargs)
else:
    assistant = Client("assistant_account", **assistant_kwargs)

# PyTgCalls bound to assistant (only used when assistant is started)
call_py = PyTgCalls(assistant)

# ---------- DB / logging ----------
db_client = None
db = None

def init_db_sync():
    global db_client, db
    if not MONGO_URI or MongoClient is None:
        logging.info("DB disabled.")
        return
    db_client = MongoClient(MONGO_URI)
    db = db_client[MONGO_DBNAME]
    try:
        db.blocked.create_index("chat_id")
        db.logs.create_index("ts")
        db.langs.create_index("chat_id", unique=True)
    except Exception:
        pass
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
            await bot.send_message(target, f"🔔 <b>{event_type}</b>\n<pre>{data}</pre>", disable_web_page_preview=True)
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
    db.blocked.update_one({"chat_id": chat_id}, {"$set": {"chat_id": chat_id, "by": by_user, "reason": reason, "ts": time.time()}}, upsert=True)

def unblock_group_sync(chat_id: int):
    if db is None:
        return
    db.blocked.delete_one({"chat_id": chat_id})

# ---------- i18n (kept simple) ----------
TRANSLATIONS = {
    "en": {
        "GROUP_BLOCKED": "❌ This group is blocked from using DLK BOT.",
        "ONLY_ADMINS": "Only admins can use this.",
        "PLAY_USAGE": "Usage: /play <YouTube url or search terms> OR reply to an audio/voice file and use /play",
        "SEARCHING_STREAM": "🔎 Searching and preparing stream...",
        "YTDLP_FAIL": "❌ Could not extract audio stream. Ensure yt-dlp is installed and cookies.txt set if needed.",
        "NOW_PLAYING": "▶️ Now playing: {title}",
        "ADDED_QUEUE": "➕ Added to queue: {title}",
        "PREPARING_AUDIO_REPLY": "Preparing your audio reply...",
        "BOT_STOPPED": "DLK bot stopped & cleaned up.",
        "ASSISTANT_JOIN_INFO": "🤖 Assistant has joined the group. Please grant it permission to manage voice chats and speak.",
        "ASSISTANT_INVITE_TEXT": "Assistant not in group. I've created an invite link — add the assistant account manually and give it permission to speak.",
        "ASSISTANT_INVITE_FAIL_TEXT": "Assistant is not in this group and I couldn't create an invite automatically. Please add the assistant account to the group and try again.",
        "STATION_URL_NOT_FOUND": "Station URL not found!",
        "VOICECHAT_NOT_READY": "❌ Cannot connect to voice chat! Ensure voice chat is active and assistant has permissions.",
        "RADIO_START_FAIL": "❌ Failed to start radio! Error: {error}",
        "RATE_LIMIT": "⏳ Rate limit reached! Wait {seconds} seconds.",
    }
}
DEFAULT_LANG = "en"
def t(chat_id: int, key: str, **kwargs) -> str:
    text = TRANSLATIONS.get(DEFAULT_LANG, {}).get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
    return text

# ---------- Utilities ----------
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

# ---------- Thumbnail helpers (kept) ----------
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

# ---------- play helpers ----------
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

async def _force_leave_call(chat_id: int):
    """
    Attempt to leave via the available PyTgCalls methods.
    """
    try:
        # try standard leave
        await call_py.leave_group_call(chat_id)
        logging.debug(f"_force_leave_call: leave_group_call used for {chat_id}")
    except Exception as e:
        logging.debug(f"_force_leave_call leave_group_call failed {chat_id}: {e}")
        try:
            await _safe_call_py_method("leave_call", chat_id)
        except Exception as e2:
            logging.debug(f"_force_leave_call leave_call fallback failed {chat_id}: {e2}")

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

def store_play_state(chat_id: int, title: str, url: str, msg_id: int, start_time: Optional[float], elapsed: float = 0.0, paused: bool = False, duration: Optional[int] = None):
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

# ---------- prepare entry from reply ----------
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

# ---------- track watcher & timers ----------
async def update_radio_timer(chat_id: int, msg_id: int, title: str, start_time: float, track_duration: int):
    while True:
        try:
            elapsed = max(0, int(time.time() - start_time))
            remaining = max(0, track_duration - elapsed)
            m, s = divmod(remaining, 60)
            timer = f"{m:02d}:{s:02d}"
            caption = f"🎧 Now Playing: {title}\n⏳ Duration: {timer}"
            await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=caption, reply_markup=player_controls_markup(chat_id))
            if remaining <= 0:
                break
        except Exception as e:
            logging.debug(f"Timer update failed for {chat_id}/{msg_id}: {e}")
            break
        await asyncio.sleep(5)

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
            except Exception as e:
                logging.debug(f"track_watcher edit caption failed {chat_id}/{msg_id}: {e}")
            log_event_sync("music_track_autostop", {"chat_id": chat_id})
    except asyncio.CancelledError:
        return
    except Exception as e:
        logging.debug(f"track_watcher error {chat_id}: {e}")

# ---------- player controls UI ----------
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

# ---------- core play logic ----------
async def play_entry(chat_id: int, entry: dict, reply_message: Optional[Message] = None):
    try:
        if chat_id in radio_tasks:
            radio_tasks[chat_id].cancel()
            radio_tasks.pop(chat_id, None)
        stream_source = entry["stream_url"]
        # Use MediaStream wrapper - PyTgCalls expects a compatible object; this is the same pattern as before
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
        logging.exception("Play entry failed")
        try:
            await leave_voice_chat(chat_id)
        except Exception:
            pass
        return False

# ---------- helper: is admin or owner ----------
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

# ---------- Commands (/play, /skip, /queue, /stop, /radio, etc.) ----------
@bot.on_message(filters.group & filters.command(["play", "p"]))
async def cmd_play(_, message: Message):
    chat_id = message.chat.id
    user = message.from_user
    if is_group_blocked_sync(chat_id):
        return await message.reply_text(t(chat_id, "GROUP_BLOCKED"))
    # ensure assistant present (improved flow)
    assistant_present = False
    assistant_id = None
    try:
        if ASSISTANT_SESSION:
            assistant_user = await assistant.get_me()
            assistant_id = assistant_user.id
            ASSISTANT_USERNAME = assistant_user.username
            ASSISTANT_ID = assistant_user.id
    except Exception:
        assistant_id = None

    if assistant_id:
        try:
            await assistant.get_chat_member(chat_id, assistant_id)
            assistant_present = True
        except RPCError:
            assistant_present = False

    if not assistant_present:
        # create invite link and try to join assistant - but if assistant can't join, present the invite link to admins
        try:
            # create invite - requires bot to be admin with invite permission
            invite = await bot.create_chat_invite_link(chat_id, member_limit=1, name="DLK BOT assistant")
            invite_link = invite.invite_link
        except Exception as e:
            invite_link = None
            logging.debug(f"Failed to create invite link: {e}")

        if invite_link and ASSISTANT_SESSION:
            # try to join with assistant account
            try:
                # assistant.join_chat expects either username or invite link
                await assistant.join_chat(invite_link)
                assistant_present = True
                try:
                    await bot.send_message(chat_id, t(chat_id, "ASSISTANT_JOIN_INFO"), disable_web_page_preview=True)
                except Exception:
                    pass
            except Exception as e_join:
                logging.warning(f"Assistant failed to join via invite: {e_join}")
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("📋 Invite Link", url=invite_link)]])
                await message.reply_text(t(chat_id, "ASSISTANT_INVITE_TEXT"), reply_markup=kb)
                return
        else:
            return await message.reply_text(t(chat_id, "ASSISTANT_INVITE_FAIL_TEXT"))

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
        try:
            if info_msg:
                await info_msg.edit_text(t(chat_id, "YTDLP_FAIL"))
        except Exception:
            pass

@bot.on_message(filters.group & filters.command(["skip", "s"]))
async def cmd_skip(_, message: Message):
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text("Only admins can skip.")
    q = radio_queue.get(chat_id, [])
    if not q:
        await leave_voice_chat(chat_id)
        await message.reply_text("⛔ Skipped. No more tracks in queue.")
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
        await message.reply_text(f"⏭️ Now playing: {next_entry['title']}")
        log_event_sync("music_skipped", {"chat_id": chat_id, "title": next_entry["title"], "by": message.from_user.id})
    else:
        await message.reply_text("Failed to play next track.")

@bot.on_message(filters.group & filters.command(["queue", "q"]))
async def cmd_queue(_, message: Message):
    chat_id = message.chat.id
    q = radio_queue.get(chat_id, [])
    if not q:
        return await message.reply_text("Queue is empty.")
    text = "Upcoming queue:\n"
    for i, item in enumerate(q[:10], start=1):
        text += f"{i}. {item.get('title')}\n"
    await message.reply_text(text)

@bot.on_message(filters.group & filters.command(["stop", "end"]))
async def general_stop_handler(_, message: Message):
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text("Only admins can stop playback.")
    state = radio_state.get(chat_id)
    msg_id = state.get("msg_id") if state else None
    await leave_voice_chat(chat_id)
    if msg_id:
        try:
            await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=t(chat_id, "BOT_STOPPED"), reply_markup=None)
        except Exception:
            pass
    await message.reply_text(t(chat_id, "BOT_STOPPED"))
    log_event_sync("radio_stopped_text", {"chat_id": chat_id, "by": message.from_user.id})

@bot.on_message(filters.group & filters.command(["radio"]))
async def cmd_radio_menu(_, message: Message):
    chat_id = message.chat.id
    if is_group_blocked_sync(chat_id):
        return await message.reply_text(t(chat_id, "GROUP_BLOCKED"))
    kb = radio_buttons(0)
    await message.reply_text("📻 Radio Stations - choose one:", reply_markup=kb)

@bot.on_message(filters.group & filters.command(["rpush"]))
async def cmd_rpush(_, message: Message):
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text("Only admins can add radio stations.")
    args = None
    if len(message.command) > 1:
        args = message.text.split(None, 1)[1].strip()
    if not args:
        return await message.reply_text("Usage: /rpush <station_name or stream_url>")
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
    entry = {"title": title, "stream_url": stream_url, "webpage": None, "thumbnail": None, "duration": None, "is_local": False}
    if chat_id not in radio_queue:
        radio_queue[chat_id] = []
    radio_queue[chat_id].append(entry)
    await message.reply_text(f"➕ Added to radio queue: {title}")
    log_event_sync("radio_rpush", {"chat_id": chat_id, "title": title, "by": message.from_user.id})

# ---------- callbacks (play radio, pause/resume/stop/skip) ----------
@bot.on_callback_query(filters.regex("^radio_play_"))
async def play_radio_station(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    station = query.data.replace("radio_play_", "")
    url = RADIO_STATION.get(station)
    user = query.from_user
    if is_group_blocked_sync(chat_id):
        await query.answer("This group is blocked.", show_alert=True)
        return
    if not url:
        return await query.answer("Station URL not found.", show_alert=True)
    # ensure assistant presence (same logic as /play)
    assistant_present = False
    assistant_id = None
    try:
        if ASSISTANT_SESSION:
            assistant_user = await assistant.get_me()
            assistant_id = assistant_user.id
    except Exception:
        assistant_id = None
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
        except Exception as e:
            invite_link = None
            logging.debug(f"Failed to create invite link: {e}")
        if invite_link and ASSISTANT_SESSION:
            try:
                await assistant.join_chat(invite_link)
                assistant_present = True
                try:
                    await bot.send_message(chat_id, t(chat_id, "ASSISTANT_JOIN_INFO"), disable_web_page_preview=True)
                except Exception:
                    pass
            except Exception as e_join:
                logging.warning(f"Assistant failed to join via invite: {e_join}")
                help_kb = InlineKeyboardMarkup([[InlineKeyboardButton("📋 Invite Link", url=invite_link)]])
                await query.message.reply_text(t(chat_id, "ASSISTANT_INVITE_TEXT"), reply_markup=help_kb)
                return
        else:
            await query.message.reply_text(t(chat_id, "ASSISTANT_INVITE_FAIL_TEXT"))
            return
    try:
        await _safe_call_py_method("play", chat_id, MediaStream(url))
        msg = await query.message.edit_caption(caption=f"🎧 {station}\n🔴 LIVE Radio", reply_markup=player_controls_markup(chat_id))
        start_time = time.time()
        store_play_state(chat_id, station, url, msg.id, start_time, elapsed=0.0, paused=False, duration=None)
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
        await query.message.reply_text(t(chat_id, "RADIO_START_FAIL", error=str(e)))
    except Exception as e:
        await leave_voice_chat(chat_id)
        logging.exception("General radio play error")
        await query.message.reply_text(t(chat_id, "RADIO_START_FAIL", error=str(e)))

@bot.on_callback_query(filters.regex("^radio_pause$"))
async def radio_pause_cb(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await query.answer("Only admins can control radio.", show_alert=True)
    state = radio_state.get(chat_id)
    if not state:
        return await query.answer("Nothing is playing.", show_alert=True)
    try:
        await _safe_call_py_method("pause_stream", chat_id)
        await _safe_call_py_method("pause", chat_id)
        start_time = state.get("start_time") or time.time()
        elapsed = time.time() - start_time if start_time else state.get("elapsed", 0.0)
        state["paused"] = True
        state["elapsed"] = elapsed
        state["start_time"] = None
        radio_paused.add(chat_id)
        store_play_state(chat_id, state.get("station"), state.get("url"), state.get("msg_id"), None, elapsed=elapsed, paused=True, duration=state.get("duration"))
        try:
            await query.message.edit_reply_markup(reply_markup=player_controls_markup(chat_id))
        except Exception:
            pass
        await query.answer("Paused.", show_alert=False)
        log_event_sync("radio_paused", {"chat_id": chat_id, "by": query.from_user.id if query.from_user else None})
    except Exception as e:
        logging.debug(f"Pause failed: {e}")
        await query.answer("Failed to pause the stream.", show_alert=True)

@bot.on_callback_query(filters.regex("^radio_resume$"))
async def radio_resume_cb(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await query.answer("Only admins can control radio.", show_alert=True)
    state = radio_state.get(chat_id)
    if not state:
        return await query.answer("Nothing to resume.", show_alert=True)
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
        store_play_state(chat_id, state.get("station"), state.get("url"), state.get("msg_id"), start_time, elapsed=0.0, paused=False, duration=duration)
        if duration is not None:
            if chat_id in radio_tasks:
                try:
                    radio_tasks[chat_id].cancel()
                except Exception:
                    pass
                radio_tasks.pop(chat_id, None)
            radio_tasks[chat_id] = asyncio.create_task(update_radio_timer(chat_id, state.get("msg_id"), state.get("station"), start_time, duration))
        try:
            await query.message.edit_reply_markup(reply_markup=player_controls_markup(chat_id))
        except Exception:
            pass
        await query.answer("Resumed.", show_alert=False)
        log_event_sync("radio_resumed", {"chat_id": chat_id, "by": query.from_user.id if query.from_user else None})
    except Exception as e:
        logging.debug(f"Resume failed: {e}")
        await query.answer("Failed to resume the stream.", show_alert=True)

@bot.on_callback_query(filters.regex("^music_skip$"))
async def cb_music_skip(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await query.answer("Only admins can skip.", show_alert=True)
    q = radio_queue.get(chat_id, [])
    if not q:
        await leave_voice_chat(chat_id)
        try:
            await query.message.edit_caption(caption="⛔ Skipped. No more tracks in queue.", reply_markup=None)
        except Exception:
            pass
        await query.answer("Skipped. No queue.", show_alert=True)
        log_event_sync("music_skipped_stop", {"chat_id": chat_id, "by": query.from_user.id if query.from_user else None})
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
            await query.message.edit_caption(caption=f"⏭️ Now playing: {next_entry['title']}", reply_markup=player_controls_markup(chat_id))
        except Exception:
            pass
        await query.answer("Skipped to next.", show_alert=False)
        log_event_sync("music_skipped", {"chat_id": chat_id, "title": next_entry["title"], "by": query.from_user.id if query.from_user else None})
    else:
        await query.answer("Failed to skip to next track.", show_alert=True)

@bot.on_callback_query(filters.regex("^radio_stop$"))
async def cb_radio_stop(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await query.answer("Only admins can stop the radio.", show_alert=True)
    try:
        await leave_voice_chat(chat_id)
        try:
            await query.message.delete()
        except Exception:
            try:
                await query.message.edit_caption(caption="DLK BOT stopped!", reply_markup=None)
            except Exception:
                pass
        await query.answer("DLK BOT stopped!", show_alert=False)
        log_event_sync("radio_stopped", {"chat_id": chat_id, "by": query.from_user.id if query.from_user else None})
    except Exception as e:
        logging.exception("Stop failed via callback")
        await query.answer("Failed to stop bot.", show_alert=True)

# ---------- navigation / pages ----------
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

# ---------- /bl /unbl /panel ----------
@bot.on_message(filters.group & filters.command(["bl", "block"]))
async def cmd_block_group(_, message: Message):
    chat_id = message.chat.id
    if not message.from_user or message.from_user.id != OWNER_ID:
        return await message.reply_text("Only owner can block groups.")
    try:
        block_group_sync(chat_id, message.from_user.id, reason="blocked by owner via /bl")
        await message.reply_text("✅ This group has been blocked from using DLK BOT.")
        log_event_sync("group_blocked", {"chat_id": chat_id, "by": message.from_user.id})
    except Exception as e:
        logging.warning(f"Failed to block group {chat_id}: {e}")
        await message.reply_text("Failed to block the group.")

@bot.on_message(filters.group & filters.command(["unbl", "unblock"]))
async def cmd_unblock_group(_, message: Message):
    chat_id = message.chat.id
    if not message.from_user or message.from_user.id != OWNER_ID:
        return await message.reply_text("Only owner can unblock groups.")
    try:
        unblock_group_sync(chat_id)
        await message.reply_text("✅ This group has been unblocked.")
        log_event_sync("group_unblocked", {"chat_id": chat_id, "by": message.from_user.id})
    except Exception as e:
        logging.warning(f"Failed to unblock group {chat_id}: {e}")
        await message.reply_text("Failed to unblock the group.")

@bot.on_message(filters.private & filters.command(["panel"]))
async def owner_panel(_, message: Message):
    chat_id = message.chat.id
    if not message.from_user or message.from_user.id != OWNER_ID:
        return await message.reply_text("You are not authorized to view the panel.")
    if db is None:
        return await message.reply_text("Database is not configured. Block list not available.")
    try:
        blocked = list(db.blocked.find({}).sort("ts", -1).limit(100))
        if not blocked:
            return await message.reply_text("Blocked list is empty.")
        text_lines = ["Blocked groups:"]
        for b in blocked:
            text_lines.append(f"- {b.get('chat_id')} (by {b.get('by')}, reason: {b.get('reason') or 'n/a'})")
        await message.reply_text("\n".join(text_lines))
    except Exception as e:
        logging.warning(f"Failed to fetch blocked list: {e}")
        await message.reply_text("Failed to fetch blocked list.")

# ---------- START handlers ----------
@bot.on_message(filters.command(["start"]) & filters.private)
async def start_private(_, message: Message):
    text = (
        "👋 Welcome to DLK BOT!\n\n"
        "Commands (groups):\n"
        "- /radio : stations\n"
        "- /play <query|URL> or reply to an audio/voice file and use /play : play music\n"
        "- /pause /resume /stop /skip : playback controls (admins)\n\n"
        "Owner-only: /bl (block group), /unbl (unblock group)\n"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📻 Menu", callback_data="radio_page_0")],
        [InlineKeyboardButton("👨‍💻 Dev", url=DEV_LINK), InlineKeyboardButton("💬 Support", url=SUPPORT_LINK)],
    ])
    await message.reply_text(text, reply_markup=kb)

# ---------- STARTUP / MAIN ----------
def _safe_startup():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.info("Starting DLK Bot (fixed startup sequence)...")
    try:
        init_db_sync()
    except Exception as e:
        logger.warning(f"Database initialization failed: {e}")

    # Start the bot first (bot must be running to create invite links or send messages)
    try:
        bot.start()
        logger.info("Bot (token) client started.")
    except Exception as e:
        logger.exception(f"Failed to start bot client: {e}")
        raise

    assistant_started = False
    if ASSISTANT_SESSION:
        try:
            # Start assistant AFTER bot to make invite flows reliable
            assistant.start()
            assistant_started = True
            logger.info("Assistant (user) client started.")
        except Exception as e:
            logger.warning(f"Assistant start failed: {e}")
            assistant_started = False
    else:
        logger.warning("ASSISTANT_SESSION is not set - assistant (user) client will not be started. Voice features disabled.")

    # Start PyTgCalls only if assistant started
    if assistant_started:
        try:
            call_py.start()
            logger.info("PyTgCalls started.")
        except Exception as e:
            logger.warning(f"PyTgCalls start failed: {e}")
            # continue; voice may be unreliable

    # fill global helper metadata
    try:
        if assistant_started:
            me = assistant.get_me()
            global ASSISTANT_USERNAME, ASSISTANT_ID
            ASSISTANT_USERNAME = me.username
            ASSISTANT_ID = me.id
    except Exception:
        ASSISTANT_USERNAME = "assistant"
        ASSISTANT_ID = None

    try:
        bot_me = bot.get_me()
        global BOT_USERNAME
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

if __name__ == "__main__":
    _safe_startup()
