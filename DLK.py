# DLK_radio_trimmed.py - Modified by assistant
# Changes made:
# - Force yt-dlp to pick audio-only formats and avoid playing video streams
# - /radio always plays in the group where the command/button was used (ignores linked channel)
# - /cradio and /cplay keep linked-channel behaviour
# - Player UI simplified to a single text message (no thumbnails by default)
# - Help/Home callbacks edit the same message instead of sending extra messages
# - Admin-only checks preserved for control commands
# - Removed heavy thumbnail replacement in play_entry to keep UI simple

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

load_dotenv()

API_ID = int(os.environ.get("API_ID", "") or "")
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

ASSISTANT_SESSION = os.environ.get("ASSISTANT_SESSION")
if ASSISTANT_SESSION:
    ASSISTANT_SESSION = ASSISTANT_SESSION.strip() or None
else:
    ASSISTANT_SESSION = None

OWNER_ID = int(os.getenv("OWNER_ID", "") or "0")

MONGO_URI = os.environ.get("MONGO_URI")
MONGO_DBNAME = os.environ.get("MONGO_DBNAME", "dlk_radio")
LOG_CHANNEL_ID = os.environ.get("LOG_CHANNEL_ID", "").strip()

YT_DLP_COOKIES = os.environ.get("YT_DLP_COOKIES")

DEV_LINK = "https://t.me/DLKDEVELOPERS"
SUPPORT_LINK = "https://t.me/DevDLK"

DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

DEFAULT_FALLBACK_DURATION = 240

RADIO_STATION = {
    "SirasaFM": "http://live.trusl.com:1170/;",
    "Radio Plus Hitz": "https://altair.streamerr.co/stream/8054",
    "HiruFM": "https://radio.lotustechnologieslk.net:2020/stream/hirufmgarden?1707015384",
}

radio_tasks: Dict[int, asyncio.Task] = {}
radio_paused = set()
radio_state: Dict[int, Dict[str, Any]] = {}
radio_queue: Dict[int, List[Dict[str, Any]]] = {}
track_watchers: Dict[int, asyncio.Task] = {}

bot = Client("dlk_radio_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
assistant = Client("assistant_account", session_string=ASSISTANT_SESSION, api_id=API_ID, api_hash=API_HASH)
call_py = PyTgCalls(assistant)

# minimal DB placeholders (keep API but optional)
db_client = None
db = None

TRANSLATIONS = {
    "en": {
        "ONLY_ADMINS": "Only admins can use this.",
        "ONLY_ADMINS_SKIP": "Only admins can skip tracks.",
        "ONLY_ADMINS_STOP": "Only admins can stop the playback!",
        "PLAY_USAGE": "/play <YouTube url or search terms> OR reply to an audio/voice file and use /play",
        "SEARCHING_STREAM": "🔎 Searching...",
        "YTDLP_FAIL": "❌ Could not extract audio stream. Ensure yt-dlp is installed and cookies.txt set if needed.",
        "ADDED_QUEUE": "➕ Added to queue: {title}",
        "NOW_PLAYING": "▶️ Now playing: {title}",
        "SKIPPED_NO_QUEUE": "⛔ Skipped. No more tracks in queue.",
        "BOT_STOPPED": "DLK bot stopped & cleaned up.",
        "RADIO_ENDED": "✅ Radio ended and assistant left the voice chat.",
        "RADIO_PLAY_FAILED_ASSIST": "Failed to play radio! Assistant error: {error}",
        "ASSISTANT_JOIN_INFO": "🤖 Assistant has joined the group. Please grant it permission to manage voice chats and speak.",
        "ASSISTANT_NOT_IN_GROUP": "Assistant is not in this group. Please add the assistant account and try again.",
        "START_TEXT": "👋 Welcome to DLK BOT!\n\nCommands (groups):\n- /radio : stations (plays in the group)\n- /play <query|URL> or reply to audio : play music (audio-only)\n- /cplay : play into linked channel\n- /cradio : radio into linked channel\n- pause resume stop skip : playback controls (admins)",
        "HOME_TEXT": "👋 DLK BOT Home\n\nUse the buttons to navigate: Menu shows radio stations. Help explains commands.",
        "HELP_TEXT": "DLK BOT help:\n- /play to play YouTube or reply audio.\n- /radio to open radio stations (plays in this group).\n- /cplay /cradio to use linked channel.\n- Admins only: pause/resume/skip/stop.",
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

# ---------- UTIL ----------

def looks_like_url(text: str) -> bool:
    try:
        p = urlparse(text)
        return bool(p.scheme and p.netloc)
    except Exception:
        return False

def is_ffmpeg_available() -> bool:
    try:
        res = subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        return res.returncode == 0
    except Exception:
        return False

# Force yt-dlp to return audio-only direct URL
def extract_audio_url(query: str) -> Optional[Dict[str, Any]]:
    if youtube_dl is None:
        logging.warning("yt_dlp not installed.")
        return None
    target = query if looks_like_url(query) else f"ytsearch1:{query}"
    ydl_opts = {
        "format": "bestaudio[ext!=webm]/bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
        "prefer_ffmpeg": True,
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
            # choose best audio format from formats list
            formats = info.get("formats", []) or []
            audio_formats = []
            for f in formats:
                ac = (f.get("acodec") or "").lower()
                vc = (f.get("vcodec") or "").lower()
                if ac and ac != "none" and (vc in ("none", "") or vc == "none") and f.get("url"):
                    audio_formats.append(f)
            if not audio_formats:
                # fallback: accept any format with audio
                for f in formats:
                    ac = (f.get("acodec") or "").lower()
                    if ac and ac != "none" and f.get("url"):
                        audio_formats.append(f)
            chosen = None
            if audio_formats:
                # prefer mp3 > m4a > other by bitrate
                def score(ff):
                    ext = (ff.get("ext") or "").lower()
                    abr = ff.get("abr") or ff.get("tbr") or 0
                    s = int(abr) or 0
                    if ext == "mp3":
                        s += 10000
                    if ext in ("m4a", "aac"): 
                        s += 5000
                    return s
                audio_formats_sorted = sorted(audio_formats, key=score, reverse=True)
                chosen = audio_formats_sorted[0]
            stream_url = chosen.get("url") if chosen else info.get("url")
            duration = info.get("duration")
            try:
                if duration is not None:
                    duration = int(duration)
            except Exception:
                duration = None
            return {
                "title": info.get("title") or "Unknown",
                "webpage_url": info.get("webpage_url") or info.get("id"),
                "stream_url": stream_url,
                "thumbnail": info.get("thumbnail"),
                "duration": duration,
                "format": chosen.get("ext") if chosen else None,
            }
    except Exception as e:
        logging.warning(f"yt_dlp failed: {e}")
        return None

# ---------- DB / LOG (lightweight placeholders) ----------

def init_db_sync():
    global db_client, db
    if not MONGO_URI:
        logging.info("DB disabled.")
        return
    try:
        from pymongo import MongoClient as _MongoClient
        db_client = _MongoClient(MONGO_URI)
        db = db_client[MONGO_DBNAME]
    except Exception as e:
        logging.warning(f"DB init failed: {e}")

def log_event_sync(event_type: str, data: dict):
    try:
        if db is not None:
            db.logs.insert_one({"ts": time.time(), "type": event_type, "data": data})
    except Exception:
        pass

# ---------- PRIVILEGE ----------
async def dlk_privilege_validator(subject: Union[Message, CallbackQuery]) -> bool:
    try:
        if isinstance(subject, CallbackQuery):
            user = subject.from_user
            chat = subject.message.chat
        else:
            user = subject.from_user
            chat = subject.chat
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
        return False
    except Exception:
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

def player_controls_markup(ui_chat_id: int):
    if ui_chat_id in radio_paused:
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
    bottom = [InlineKeyboardButton("👨‍💻", url=DEV_LINK), InlineKeyboardButton("💬", url=SUPPORT_LINK)]
    return InlineKeyboardMarkup([controls, bottom])

async def safe_query_answer(query: CallbackQuery, text: Optional[str] = None, show_alert: bool = False):
    try:
        if text is None:
            await query.answer()
        else:
            await query.answer(text, show_alert=show_alert)
    except RPCError:
        pass
    except Exception:
        pass

# ---------- call helpers ----------
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
    except Exception:
        return None

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
        # fallback: check attributes
        for name in ("active_calls", "group_calls", "calls", "_active_calls"):
            ac = getattr(call_py, name, None)
            if not ac:
                continue
            try:
                if isinstance(ac, dict) and chat_id in ac:
                    return True
            except Exception:
                pass
        return False
    except Exception:
        return False

async def _force_leave_call(chat_id: int):
    try:
        if hasattr(call_py, "leave_group_call"):
            res = call_py.leave_group_call(chat_id)
            if inspect.isawaitable(res):
                await res
            return
    except Exception:
        pass
    try:
        await _safe_call_py_method("leave_call", chat_id)
    except Exception:
        pass

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
        except Exception:
            pass
    except Exception:
        pass

def store_play_state(voice_chat_id: int, ui_chat_id: int, title: str, url: str, msg_id: int, start_time: Optional[float], elapsed: float = 0.0, paused: bool = False, duration: Optional[int] = None):
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

# ---------- start stream robustly (kept from original to maximize compatibility) ----------
async def _start_stream_in_call(chat_id: int, stream_source: str) -> bool:
    if not stream_source:
        return False
    ffmpeg_ok = is_ffmpeg_available()
    async def _try_and_verify(call_coro_callable):
        try:
            result = call_coro_callable()
            if inspect.isawaitable(result):
                await result
        except Exception:
            pass
        for _ in range(8):
            await asyncio.sleep(0.5)
            try:
                if await _is_call_active(chat_id):
                    return True
            except Exception:
                pass
        return False

    if AudioPiped is not None and ffmpeg_ok:
        try:
            audio_stream = None
            try:
                audio_stream = AudioPiped(stream_source)
            except Exception:
                audio_stream = None
            methods_and_args = []
            for method_name in ("join_group_call", "join_call", "play", "play_stream", "start_playout", "start_stream", "start"):
                if audio_stream is not None:
                    methods_and_args.append((method_name, (chat_id, audio_stream), {}))
                    methods_and_args.append((method_name, (chat_id,), {"input_stream": audio_stream}))
                methods_and_args.append((method_name, (chat_id, stream_source), {}))
                methods_and_args.append((method_name, (chat_id,), {"input_stream": stream_source}))
            for method_name, args, kwargs in methods_and_args:
                if not hasattr(call_py, method_name):
                    continue
                def make_call(method=method_name, a=args, kw=kwargs):
                    return getattr(call_py, method)(*a, **kw)
                ok = await _try_and_verify(make_call)
                if ok:
                    return True
        except Exception:
            pass

    # try media stream if available
    if MediaStream is not None:
        try:
            ms = None
            try:
                ms = MediaStream(stream_source)
            except Exception:
                ms = None
            methods_and_args = []
            for method_name in ("join_group_call", "join_call", "play", "play_stream", "start_playout", "start_stream", "start"):
                if ms is not None:
                    methods_and_args.append((method_name, (chat_id, ms), {}))
                methods_and_args.append((method_name, (chat_id, stream_source), {}))
            for method_name, args, kwargs in methods_and_args:
                if not hasattr(call_py, method_name):
                    continue
                def make_call(method=method_name, a=args, kw=kwargs):
                    return getattr(call_py, method)(*a, **kw)
                ok = await _try_and_verify(make_call)
                if ok:
                    return True
        except Exception:
            pass

    # raw calls
    candidates = [
        ("join_group_call", (chat_id, stream_source), {}),
        ("join_call", (chat_id, stream_source), {}),
        ("play", (chat_id, stream_source), {}),
        ("play_stream", (chat_id, stream_source), {}),
    ]
    for name, args, kwargs in candidates:
        if not hasattr(call_py, name):
            continue
        try:
            res = await _safe_call_py_method(name, *args, **kwargs)
            for _ in range(8):
                await asyncio.sleep(0.5)
                if await _is_call_active(chat_id):
                    return True
        except Exception:
            continue

    # fallback: try leave+log
    try:
        await _force_leave_call(chat_id)
    except Exception:
        pass
    return False

# ---------- prepare_entry_from_reply (local audio)
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
        title = (getattr(media_field, "title", None) or getattr(media_field, "file_name", None) or reply_msg.caption or "Telegram Audio")
        duration = getattr(media_field, "duration", None) or None
        entry = {
            "title": title,
            "stream_url": local_path,
            "webpage": None,
            "thumbnail": None,
            "duration": duration,
            "is_local": True,
        }
        return entry
    except Exception:
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
                    await bot.edit_message_text(chat_id=ui_chat_id, message_id=msg_id, text=t(ui_chat_id, "BOT_STOPPED"))
                except Exception:
                    pass
            except Exception:
                pass
            log_event_sync("music_track_autostop", {"chat_id": voice_chat_id})
    except asyncio.CancelledError:
        return
    except Exception:
        return

# ---------- play_entry (simplified UI - text only) ----------
async def play_entry(voice_chat_id: int, entry: dict, reply_message: Optional[Message] = None, ui_chat_id: Optional[int] = None, info_msg: Optional[Message] = None):
    try:
        if voice_chat_id in radio_tasks:
            radio_tasks[voice_chat_id].cancel()
            radio_tasks.pop(voice_chat_id, None)

        stream_source = entry["stream_url"]
        started = await _start_stream_in_call(voice_chat_id, stream_source)
        if not started:
            logging.error("Failed to start streaming in call for %s", voice_chat_id)
            return False

        title = entry.get("title") or "Unknown"
        requested_by = entry.get("requested_by")
        ui_chat = ui_chat_id or voice_chat_id
        caption = f"🎧 {t(ui_chat, 'NOW_PLAYING', title=title)}"
        if requested_by:
            caption = f"{caption}\n👤 Added by: {requested_by}"
        try:
            msg = await bot.send_message(ui_chat, caption, reply_markup=player_controls_markup(ui_chat))
        except Exception:
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
        msg_id = msg.id if msg else 0

        store_play_state(voice_chat_id, ui_chat, title, entry.get("stream_url"), msg_id, start_time, elapsed=0.0, paused=False, duration=duration)
        radio_paused.discard(voice_chat_id)
        if msg_id:
            radio_tasks[voice_chat_id] = asyncio.create_task(update_radio_timer(voice_chat_id, ui_chat, msg_id, title, start_time, duration))
        if voice_chat_id in track_watchers:
            try:
                track_watchers[voice_chat_id].cancel()
            except Exception:
                pass
        track_watchers[voice_chat_id] = asyncio.create_task(track_watcher(voice_chat_id, duration, msg_id))
        log_event_sync("music_started", {"voice_chat_id": voice_chat_id, "ui_chat": ui_chat, "title": title, "requested_by": requested_by})
        return True
    except Exception:
        try:
            await leave_voice_chat(voice_chat_id)
        except Exception:
            pass
        return False

# ---------- update timer (small edits only) ----------
async def update_radio_timer(voice_chat_id: int, ui_chat_id: int, msg_id: int, title: str, start_time: float, track_duration: int):
    while True:
        try:
            elapsed = max(0, int(time.time() - start_time))
            remaining = max(0, track_duration - elapsed)
            m, s = divmod(remaining, 60)
            timer = f"{m:02d}:{s:02d}"
            caption = f"🎧 Now Playing: {title}\n⏳ Remaining: {timer}"
            await bot.edit_message_text(chat_id=ui_chat_id, message_id=msg_id, text=caption, reply_markup=player_controls_markup(ui_chat_id))
            if remaining <= 0:
                break
        except Exception:
            break
        await asyncio.sleep(5)

# ---------- /play ----------
@bot.on_message(filters.group & filters.command(["play", "p"]))
async def cmd_play(_, message: Message):
    chat_id = message.chat.id
    user = message.from_user
    # assistant presence check
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
            return await message.reply_text("Assistant session is not configured (ASSISTANT_SESSION). Set it and restart the app.")
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
                await message.reply_text(t(chat_id, "ASSISTANT_NOT_IN_GROUP"), reply_markup=kb)
                return
        except Exception:
            return await message.reply_text(t(chat_id, "ASSISTANT_NOT_IN_GROUP"))

    entry = None
    info_msg = None
    if message.reply_to_message:
        entry = await prepare_entry_from_reply(message.reply_to_message)
        if entry:
            entry["requested_by"] = (user.first_name or user.username or str(user.id)) if user else None
            info_msg = await message.reply_text("Preparing audio...")
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
            "thumbnail": None,
            "duration": info.get("duration"),
            "is_local": False,
            "format": info.get("format"),
            "requested_by": (user.first_name or user.username or str(user.id)) if user else None,
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
    ok = await play_entry(chat_id, entry, reply_message=message, ui_chat_id=chat_id, info_msg=info_msg)
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

# ---------- /skip and /end (group commands) ----------
@bot.on_message(filters.group & filters.command(["skip", "rskip", "next"]))
async def cmd_skip(_, message: Message):
    ui_chat = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text(t(ui_chat, "ONLY_ADMINS_SKIP"))
    voice_chat = ui_chat
    q = radio_queue.get(voice_chat, [])
    if not q:
        await leave_voice_chat(voice_chat)
        state = radio_state.get(voice_chat)
        ui_chat_id = state.get("ui_chat_id") if state else ui_chat
        msg_id = state.get("msg_id") if state else None
        if msg_id:
            try:
                await bot.edit_message_text(chat_id=ui_chat_id, message_id=msg_id, text=t(ui_chat_id, "BOT_STOPPED"))
            except Exception:
                pass
        await message.reply_text(t(ui_chat, "SKIPPED_NO_QUEUE"))
        log_event_sync("music_skipped_stop", {"chat_id": voice_chat, "by": message.from_user.id if message.from_user else None})
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
            await message.reply_text(t(ui_chat, "NOW_PLAYING", title=next_entry["title"]))
        except Exception:
            pass
        log_event_sync("music_skipped", {"chat_id": voice_chat, "title": next_entry["title"], "by": message.from_user.id if message.from_user else None})
    else:
        await message.reply_text("Failed to play next track.")

@bot.on_message(filters.group & filters.command(["end", "stop"]))
async def cmd_end(_, message: Message):
    ui_chat = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text(t(ui_chat, "ONLY_ADMINS_STOP"))
    voice_chat = ui_chat
    state = radio_state.get(voice_chat)
    ui_chat_id = state.get("ui_chat_id") if state else ui_chat
    msg_id = state.get("msg_id") if state else None
    await leave_voice_chat(voice_chat)
    if msg_id:
        try:
            await bot.edit_message_text(chat_id=ui_chat_id, message_id=msg_id, text=t(ui_chat_id, "BOT_STOPPED"))
        except Exception:
            pass
    await message.reply_text(t(ui_chat, "RADIO_ENDED"))
    log_event_sync("music_end_cmd", {"chat_id": voice_chat, "by": message.from_user.id if message.from_user else None})

# ---------- /radio (open radio menu in group) ----------
@bot.on_message(filters.group & filters.command(["radio"]))
async def cmd_radio(_, message: Message):
    chat_id = message.chat.id
    kb = radio_buttons(0)
    await message.reply_text("📻 Radio Stations - choose one (will play in this group):", reply_markup=kb)

# ---------- /conet and /cplay (link group -> channel; play into linked channel) ----------
linked_channels_local: Dict[int, Union[str, int]] = {}

def get_linked_channel(group_id: int) -> Optional[Union[int, str]]:
    try:
        if db is not None:
            row = db.linked_channels.find_one({"group_id": group_id})
            if row:
                return row.get("channel")
        return linked_channels_local.get(group_id)
    except Exception:
        return linked_channels_local.get(group_id)

def set_linked_channel(group_id: int, channel_identifier: Optional[Union[int, str]]):
    try:
        if db is not None:
            if channel_identifier is None:
                db.linked_channels.delete_one({"group_id": group_id})
            else:
                db.linked_channels.update_one({"group_id": group_id}, {"$set": {"group_id": group_id, "channel": channel_identifier, "ts": time.time()}}, upsert=True)
            return
    except Exception:
        pass
    if channel_identifier is None:
        linked_channels_local.pop(group_id, None)
    else:
        linked_channels_local[group_id] = channel_identifier

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
            return await message.reply_text(f"This group is linked to channel: {cur}\n/conet unlink to remove.")
        return await message.reply_text("/conet <@channelusername or -100...id> OR /conet unlink")
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

@bot.on_message(filters.group & filters.command(["cplay", "cp"]))
async def cmd_cplay(_, message: Message):
    group_id = message.chat.id
    channel_ident = get_linked_channel(group_id)
    if not channel_ident:
        return await message.reply_text("No channel linked. /conet <@channelusername or -100id> to link a channel.")
    try:
        chat_obj = await bot.get_chat(channel_ident)
        voice_chat_id = chat_obj.id
    except Exception as e:
        return await message.reply_text("Failed to resolve linked channel. Ensure the channel exists and the bot has access.")

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
                await message.reply_text("Assistant not present in linked channel. I created an invite link – add the assistant.", reply_markup=kb)
            else:
                await message.reply_text("Assistant not present in linked channel. Please add the assistant account to the channel and give it permission to speak.")
            return

    entry = None
    info_msg = None
    user = message.from_user
    if message.reply_to_message:
        entry = await prepare_entry_from_reply(message.reply_to_message)
        if entry:
            entry["requested_by"] = (user.first_name or user.username or str(user.id)) if user else None
            info_msg = await message.reply_text("Preparing audio for channel...")
    if not entry:
        query = None
        if len(message.command) > 1:
            query = message.text.split(None, 1)[1]
        elif message.reply_to_message and message.reply_to_message.text:
            query = message.reply_to_message.text
        if not query:
            return await message.reply_text("/cplay <YouTube url or search terms> OR reply to audio in this group and use /cplay")
        info_msg = await message.reply_text("Searching audio...")
        info = extract_audio_url(query)
        if info is None or not info.get("stream_url"):
            try:
                await info_msg.edit_text(t(group_id, "YTDLP_FAIL"))
            except Exception:
                pass
            return
        entry = {
            "title": info.get("title"),
            "stream_url": info.get("stream_url"),
            "webpage": info.get("webpage_url"),
            "thumbnail": None,
            "duration": info.get("duration"),
            "is_local": False,
            "format": info.get("format"),
            "requested_by": (user.first_name or user.username or str(user.id)) if user else None,
        }

    if voice_chat_id not in radio_queue:
        radio_queue[voice_chat_id] = []
    current_state = radio_state.get(voice_chat_id)
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
    ok = await play_entry(voice_chat_id, entry, reply_message=message, ui_chat_id=ui_chat_for_ui, info_msg=info_msg)
    if ok:
        try:
            if info_msg:
                await info_msg.edit_text(f"Now playing in channel (via assistant): {entry['title']}")
        except Exception:
            pass
        log_event_sync("cplay_started", {"group_id": group_id, "channel": voice_chat_id, "title": entry["title"], "by": message.from_user.id})
    else:
        try:
            if info_msg:
                await info_msg.edit_text(t(group_id, "YTDLP_FAIL"))
        except Exception:
            pass
        await message.reply_text("Failed to start playback in linked channel.")

# ---------- /cradio (open radio menu to play into linked channel) ----------
@bot.on_message(filters.group & filters.command(["cradio"]))
async def cmd_cradio(_, message: Message):
    group_id = message.chat.id
    channel_ident = get_linked_channel(group_id)
    if not channel_ident:
        return await message.reply_text("No channel linked. /conet <@channelusername or -100id> to link a channel.")
    kb = radio_buttons(0)
    await message.reply_text(f"📻 Radio Stations - choose one to play in linked channel {channel_ident} (UI will be shown here):", reply_markup=kb)

# ---------- /cplend and /crend - end playback in linked channel (admins only) ----------
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
    except Exception:
        return await message.reply_text("Failed to resolve linked channel.")
    state = radio_state.get(voice_chat_id)
    ui_chat = state.get("ui_chat_id") if state else group_id
    msg_id = state.get("msg_id") if state else None
    await leave_voice_chat(voice_chat_id)
    if msg_id:
        try:
            await bot.edit_message_text(chat_id=ui_chat, message_id=msg_id, text=t(ui_chat, "BOT_STOPPED"))
        except Exception:
            pass
    await message.reply_text("Stopped playback in linked channel.")
    log_event_sync("cplend", {"group_id": group_id, "channel": voice_chat_id, "by": message.from_user.id})

# ---------- CALLBACKS: skip/pause/resume/stop (admin checks) ----------
@bot.on_callback_query(filters.regex("^music_skip$"))
async def cb_music_skip(_, query: CallbackQuery):
    ui_chat = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await safe_query_answer(query, t(ui_chat, "ONLY_ADMINS_SKIP"), show_alert=True)
    voice_chat = ui_chat
    q = radio_queue.get(voice_chat, [])
    if not q:
        await leave_voice_chat(voice_chat)
        try:
            await query.message.edit_text(t(ui_chat, "BOT_STOPPED"), reply_markup=None)
        except Exception:
            pass
        await safe_query_answer(query, t(ui_chat, "SKIPPED_NO_QUEUE"), show_alert=True)
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
            await query.message.edit_text(t(ui_chat, "NOW_PLAYING", title=next_entry["title"]), reply_markup=player_controls_markup(ui_chat))
        except Exception:
            pass
        await safe_query_answer(query, "Skipped to next", show_alert=False)
        log_event_sync("music_skipped", {"chat_id": voice_chat, "title": next_entry["title"], "by": query.from_user.id if query.from_user else None})
    else:
        await safe_query_answer(query, "Failed to skip to next", show_alert=True)

@bot.on_callback_query(filters.regex("^radio_pause$"))
async def radio_pause_cb(_, query: CallbackQuery):
    ui_chat = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await safe_query_answer(query, t(ui_chat, "ONLY_ADMINS"), show_alert=True)
    state = radio_state.get(ui_chat)
    if not state:
        return await safe_query_answer(query, "Nothing playing.", show_alert=True)
    try:
        await _safe_call_py_method("pause_stream", ui_chat)
        await _safe_call_py_method("pause", ui_chat)
        start_time = state.get("start_time") or time.time()
        elapsed = time.time() - start_time if start_time else state.get("elapsed", 0.0)
        state["paused"] = True
        state["elapsed"] = elapsed
        state["start_time"] = None
        radio_paused.add(ui_chat)
        store_play_state(ui_chat, state.get("ui_chat_id") or ui_chat, state.get("station"), state.get("url"), state.get("msg_id"), None, elapsed=elapsed, paused=True, duration=state.get("duration"))
        try:
            await query.message.edit_reply_markup(reply_markup=player_controls_markup(ui_chat))
        except Exception:
            pass
        await safe_query_answer(query, "Paused.")
        log_event_sync("radio_paused", {"chat_id": ui_chat, "by": query.from_user.id if query.from_user else None})
    except Exception:
        await safe_query_answer(query, "Failed to pause.", show_alert=True)

@bot.on_callback_query(filters.regex("^radio_resume$"))
async def radio_resume_cb(_, query: CallbackQuery):
    ui_chat = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await safe_query_answer(query, t(ui_chat, "ONLY_ADMINS"), show_alert=True)
    state = radio_state.get(ui_chat)
    if not state:
        return await safe_query_answer(query, "Nothing to resume.", show_alert=True)
    try:
        await _safe_call_py_method("resume_stream", ui_chat)
        await _safe_call_py_method("resume", ui_chat)
        elapsed = state.get("elapsed", 0.0) or 0.0
        start_time = time.time() - elapsed
        state["paused"] = False
        state["elapsed"] = 0.0
        state["start_time"] = start_time
        radio_paused.discard(ui_chat)
        duration = state.get("duration")
        store_play_state(ui_chat, state.get("ui_chat_id") or ui_chat, state.get("station"), state.get("url"), state.get("msg_id"), start_time, elapsed=0.0, paused=False, duration=duration)
        if duration is not None:
            if ui_chat in radio_tasks:
                try:
                    radio_tasks[ui_chat].cancel()
                except Exception:
                    pass
                radio_tasks.pop(ui_chat, None)
            radio_tasks[ui_chat] = asyncio.create_task(update_radio_timer(ui_chat, state.get("ui_chat_id") or ui_chat, state.get("msg_id"), state.get("station"), start_time, duration))
        try:
            await query.message.edit_reply_markup(reply_markup=player_controls_markup(ui_chat))
        except Exception:
            pass
        await safe_query_answer(query, "Resumed.")
        log_event_sync("radio_resumed", {"chat_id": ui_chat, "by": query.from_user.id if query.from_user else None})
    except Exception:
        await safe_query_answer(query, "Failed to resume.", show_alert=True)

@bot.on_callback_query(filters.regex("^radio_stop$"))
async def cb_radio_stop(_, query: CallbackQuery):
    ui_chat = query.message.chat.id
    if not await dlk_privilege_validator(query):
        return await safe_query_answer(query, t(ui_chat, "ONLY_ADMINS"), show_alert=True)
    try:
        await leave_voice_chat(ui_chat)
        try:
            await query.message.edit_text(t(ui_chat, "BOT_STOPPED"), reply_markup=None)
        except Exception:
            pass
        await safe_query_answer(query, "Stopped.")
        log_event_sync("radio_stopped", {"chat_id": ui_chat, "by": query.from_user.id if query.from_user else None})
    except Exception:
        await safe_query_answer(query, "Failed to stop.", show_alert=True)

# ---------- radio play callback (changed: always play in the group where button was used) ----------
@bot.on_callback_query(filters.regex("^radio_play_"))
async def play_radio_station(_, query: CallbackQuery):
    ui_chat = query.message.chat.id
    station = query.data.replace("radio_play_", "")
    url = RADIO_STATION.get(station)
    user = query.from_user
    voice_chat = ui_chat  # ALWAYS play in the same group (per user request)

    if not url:
        return await safe_query_answer(query, "Station URL not found", show_alert=True)
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
                        await bot.send_message(ui_chat, t(ui_chat, "ASSISTANT_JOIN_INFO"), disable_web_page_preview=True)
                    except Exception:
                        pass
                except Exception:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📋 Invite Link", url=invite_link)]])
                    await query.message.reply_text(t(ui_chat, "ASSISTANT_NOT_IN_GROUP"), reply_markup=kb)
                    await safe_query_answer(query)
                    return
            except Exception:
                await query.message.reply_text(t(ui_chat, "ASSISTANT_NOT_IN_GROUP"))
                await safe_query_answer(query)
                return

        started = await _start_stream_in_call(voice_chat, url)
        if not started:
            await leave_voice_chat(voice_chat)
            await query.message.reply_text(t(ui_chat, "RADIO_PLAY_FAILED_ASSIST", error="assistant failed to start stream"))
            await safe_query_answer(query, "Failed to start radio", show_alert=True)
            return

        msg = None
        try:
            try:
                await query.message.edit_text(f"🎧 {station}\n🔴 LIVE Radio", reply_markup=player_controls_markup(ui_chat))
                msg = await bot.get_messages(ui_chat, query.message.id)
            except Exception:
                msg = await bot.send_message(ui_chat, f"🎧 {station}\n🔴 LIVE Radio", reply_markup=player_controls_markup(ui_chat))
        except Exception:
            msg = None

        start_time = time.time()
        msg_id = msg.id if msg else 0
        store_play_state(voice_chat, ui_chat, station, url, msg_id, start_time, elapsed=0.0, paused=False, duration=None)
        radio_paused.discard(voice_chat)
        await safe_query_answer(query, f"Now playing {station} via assistant!", show_alert=False)
        log_event_sync("radio_started", {"voice_chat": voice_chat, "station": station, "by": user.id if user else None})
    except FloodWait as e:
        await leave_voice_chat(voice_chat)
        wait_time = getattr(e, "value", None) or getattr(e, "x", None) or "unknown"
        await query.message.reply_text(f"Rate limit: wait {wait_time}s")
        await safe_query_answer(query, f"Wait {wait_time}s", show_alert=True)
    except Exception as e:
        await leave_voice_chat(voice_chat)
        await query.message.reply_text(t(ui_chat, "RADIO_PLAY_FAILED_ASSIST", error=str(e)))
        await safe_query_answer(query)

# ---------- START / HELP / LANG (help edits same message) ----------
@bot.on_message(filters.command(["start"]) & filters.private)
async def start_private(_, message: Message):
    chat_id = message.chat.id
    text = t(chat_id, "START_TEXT")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Home", callback_data="home"), InlineKeyboardButton("❓ Help", callback_data="help_info")],
        [InlineKeyboardButton("📻 Menu", callback_data="radio_page_0")],
        [InlineKeyboardButton("👨‍💻", url=DEV_LINK), InlineKeyboardButton("💬", url=SUPPORT_LINK)],
    ])
    await message.reply_text(text, reply_markup=kb)

@bot.on_callback_query(filters.regex("^home$"))
async def cb_home(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    text = t(chat_id, "HOME_TEXT")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📻 Menu", callback_data="radio_page_0"), InlineKeyboardButton("❓ Help", callback_data="help_info")]])
    await safe_query_answer(query)
    try:
        await query.message.edit_text(text, reply_markup=kb)
    except Exception:
        await query.message.reply_text(text, reply_markup=kb)

@bot.on_callback_query(filters.regex("^help_info$"))
async def cb_help_info(_, query: CallbackQuery):
    chat_id = query.message.chat.id
    help_text = t(chat_id, "HELP_TEXT")
    await safe_query_answer(query)
    try:
        await query.message.edit_text(help_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back", callback_data="home")]]))
    except Exception:
        await query.message.reply_text(help_text)

# ---------- radio menu pagination/close ----------
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
    except Exception:
        await safe_query_answer(query, "Failed to load page.", show_alert=True)

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
    except Exception:
        await safe_query_answer(query, "Failed to close menu.", show_alert=True)

# ---------- OWNER debug (kept minimal) ----------
@bot.on_message(filters.private & filters.command(["debug_call_status"]))
async def cmd_debug_call_status(_, message: Message):
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
    logger.info("Starting DLK Radio (trimmed)...")
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
    except Exception:
        pass
    try:
        bot_me = bot.get_me()
    except Exception:
        pass
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
