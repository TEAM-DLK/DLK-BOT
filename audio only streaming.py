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

from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream, AudioQuality
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, FileResponse
import uvicorn
import threading

# yt-dlp for extracting audio streams
try:
    import yt_dlp as youtube_dl
except Exception:
    youtube_dl = None

# thumbnail support
import aiohttp
import aiofiles
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

# Optional youtube-search-python
try:
    from youtubesearchpython.__future__ import VideosSearch
    VIDEOS_SEARCH_AVAILABLE = True
except Exception:
    VideosSearch = None
    VIDEOS_SEARCH_AVAILABLE = False

# Optional DB (pymongo)
try:
    from pymongo import MongoClient
except Exception:
    MongoClient = None

import ntgcalls

load_dotenv()

# ====================== CONFIG ======================
API_ID = int(os.environ.get("API_ID", ""))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ASSISTANT_SESSION = os.environ.get("ASSISTANT_SESSION", "")
OWNER_ID = int(os.getenv("OWNER_ID", ""))

MONGO_URI = os.environ.get("MONGO_URI")
MONGO_DBNAME = os.environ.get("MONGO_DBNAME", "dlk_radio")
LOG_CHANNEL_ID = os.environ.get("LOG_CHANNEL_ID", "").strip()

YT_DLP_COOKIES = os.environ.get("YT_DLP_COOKIES")  # optional cookies.txt path

DEV_LINK = "https://t.me/DLKDEVELOPERS"
SUPPORT_LINK = "https://t.me/DevDLK"

# REST API configuration
STREAM_API_KEY = os.environ.get("STREAM_API_KEY", "").strip()
STREAM_API_HOST = os.environ.get("STREAM_API_HOST", "0.0.0.0").strip()
STREAM_API_PORT = int(os.environ.get("STREAM_API_PORT", "8080"))
STREAM_API_RATE_LIMIT = int(os.environ.get("STREAM_API_RATE_LIMIT", "30"))
STREAM_API_RATE_WINDOW = int(os.environ.get("STREAM_API_RATE_WINDOW", "60"))

# Optional external YouTube download API (same architecture as the supplied sample).
# If configured, the bot downloads MP3 files from this API instead of playing
# short-lived YouTube media URLs. If unavailable, local yt-dlp is used as fallback.
YOUTUBE_API_URL = os.environ.get("SHRUTI_API_URL", "").strip().rstrip("/")
YOUTUBE_API_KEY = os.environ.get("SHRUTI_API_KEY", "").strip()
YOUTUBE_API_TIMEOUT = int(os.environ.get("SHRUTI_API_TIMEOUT", "300"))

THUMB_CACHE_DIR = "cache"
os.makedirs(THUMB_CACHE_DIR, exist_ok=True)
DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Example station list (kept minimal here; merge from your original file)
RADIO_STATION = {
    "🎧 sɪʀᴀsᴀ ғᴍ"              : "http://live.trusl.com:1170/;",
    "🎼 ʜᴇʟᴀ ɴᴀᴅᴀ ғᴍ"           : "https://stream-176.zeno.fm/9ndoyrsujwpvv",
    "🔥 ʀᴀᴅɪᴏ ᴘʟᴜs ʜɪᴛᴢ"        : "https://altair.streamerr.co/stream/8054",
    "🌍 ᴇɴɢʟɪsʜ ʜɪᴛs"           : "https://hls-01-regions.emgsound.ru/11_msk/playlist.m3u8",
    "📻 ʜɪʀᴜ ғᴍ"                : "https://radio.lotustechnologieslk.net:2020/stream/hirufmgarden",
    "❤️ ʀᴇᴅ ғᴍ"                : "https://shaincast.caster.fm:47830/listen.mp3",
    "💛 ʀᴀɴ ғᴍ"                : "https://207.148.74.192:7874/ran.mp3",
    "💙 ʏ ғᴍ"                  : "http://live.trusl.com:1180/;",
    "🔊 +212"                   : "http://stream.radio.co/sf55ced545/listen",
    "🎶 ᴅᴇᴇᴘ ʜᴏᴜsᴇ ᴍᴜsɪᴄ"       : "http://live.dancemusic.ro:7000/",
    "🇮🇹 ʀᴀᴅɪᴏ ɪᴛᴀʟɪᴀ"          : "https://energyitalia.radioca.st",
    "⭐ ᴛʜᴇ ʙᴇsᴛ ᴍᴜsɪᴄ"          : "http://s1.slotex.pl:7040/",
    "🎵 ʜɪᴛᴢ ғᴍ"                : "https://stream-173.zeno.fm/uyx7eqengijtv",
    "📡 ᴘʀɪᴍᴇ ʀᴀᴅɪᴏ ʜᴅ"         : "https://stream-153.zeno.fm/oksfm5djcfxvv",
    "🌌 1ᴍɪx ᴛʀᴀɴᴄᴇ"            : "https://fr3.1mix.co.uk:8000/128",
    "🎛 ᴍᴀɴɢʟᴇᴅ ᴍᴜsɪᴄ"          : "http://hearme.fm:9500/autodj?8194",
    "🎤 sʜʀᴇᴇ ғᴍ"               : "https://207.148.74.192:7874/stream2.mp3",
    "🎙 sʜᴀᴀ ғᴍ"               : "https://radio.lotustechnologieslk.net:2020/stream/shaafmgarden",
    "🎶 sɪᴛʜᴀ ғᴍ"               : "https://stream.streamgenial.stream/cdzzrkrv0p8uv",
    "🥁 ᴊᴏɪɴᴛ ʀᴀᴅɪᴏ ʙᴇᴀᴛ"       : "https://jointil.com/stream-beat",
    "📻 ᴇғᴍ"                    : "https://207.148.74.192:7874/stream",
    "🇻🇳 ʀғɪ ᴠɪᴇᴛɴᴀᴍ"            : "https://rfivietnamien96k.ice.infomaniak.ch/rfivietnamien-96k.mp3",
    "🎧 ᴘʜᴀᴛ"                   : "https://phat.stream.laut.fm/phat",
    "🇻🇳 ᴅᴀɪ ᴘʜᴀᴛ ᴛʜᴀɴʜ"        : "http://c13.radioboss.fm:8127/stream",
    "⚡ ᴘᴜʟsᴇ ᴇᴅᴍ"              : "https://naxos.cdnstream.com/1373_128",
    "🎼 ʙᴀsᴇ ᴍᴜsɪᴄ"             : "https://base-music.stream.laut.fm/base-music",
    "🎉 ᴜʟᴛʀᴀ ᴍᴜsɪᴄ ғᴇsᴛ"       : "http://prem4.di.fm/umfradio_hi",
    "🌄 ɴᴀ ᴅᴀʜᴀsᴀ ғᴍ"           : "https://stream-155.zeno.fm/z7q96fbw7rquv",
    "📼 ᴘᴀʀᴀɴɪ ɢᴇᴇ ʀᴀᴅɪᴏ"       : "http://cast2.citrus3.com:8288/",
    "☀️ sᴜɴ ғᴍ"                : "https://radio.lotustechnologieslk.net:2020/stream/sunfmgarden",
    "💥 ᴇᴅᴍ ᴍᴇɢᴀ sʜᴜғғʟᴇ"       : "https://maggie.torontocast.com:9030/stream",
    "🎸 ᴊᴀᴍ ғᴍ"                : "http://stream.jam.fm/jamfm-nmr/mp3-192/"
}


# ====================== GLOBALS ======================
radio_tasks: Dict[int, asyncio.Task] = {}
radio_paused = set()
radio_state: Dict[int, Dict[str, Any]] = {}
radio_queue: Dict[int, List[Dict[str, Any]]] = {}
track_watchers: Dict[int, asyncio.Task] = {}
bot_start_time = time.time()

BOT_USERNAME = None
ASSISTANT_USERNAME = None
ASSISTANT_ID = None

# ====================== CLIENTS ======================
bot = Client("dlk_radio_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
assistant = Client("assistant_account", session_string=ASSISTANT_SESSION)
call_py = PyTgCalls(assistant)

db_client = None
db = None

# ====================== UTIL: urls / youtube id / yt-dlp ======================
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

def _safe_video_id(value: str) -> Optional[str]:
    value = (value or "").strip()
    vid = get_youtube_id(value)
    if vid:
        return vid
    if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", value):
        return value
    return None


def _youtube_info_local(query: str) -> Optional[Dict[str, Any]]:
    """Resolve metadata with yt-dlp without downloading media."""
    if youtube_dl is None:
        return None
    query = (query or "").strip()
    if not query:
        return None
    target = query if looks_like_url(query) else f"ytsearch1:{query}"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "cachedir": False,
        "socket_timeout": 20,
        "retries": 2,
        "extractor_args": {"youtube": {"player_client": ["android", "web_safari"]}},
    }
    if YT_DLP_COOKIES and os.path.isfile(YT_DLP_COOKIES):
        opts["cookiefile"] = YT_DLP_COOKIES
    try:
        with youtube_dl.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False)
        if info and info.get("entries"):
            info = next((x for x in info["entries"] if x), None)
        if not info:
            return None
        return {
            "id": info.get("id"),
            "title": info.get("title") or "Unknown",
            "webpage_url": info.get("webpage_url") or (f"https://www.youtube.com/watch?v={info.get('id')}" if info.get("id") else query),
            "thumbnail": info.get("thumbnail"),
            "duration": int(info.get("duration")) if info.get("duration") else None,
        }
    except Exception as e:
        logging.warning("yt-dlp metadata lookup failed for %r: %s", query, e)
        return None


async def _youtube_search_info(query: str) -> Optional[Dict[str, Any]]:
    """Resolve search text to a YouTube video ID.

    First use youtube-search-python for normal searches. If that service is
    blocked/broken, fall back to yt-dlp's *flat* search. Flat search is
    important here because it can discover the video ID without requesting
    YouTube's playable media formats.
    """
    query = (query or "").strip()
    if not query:
        return None

    # 1) Normal youtube-search-python path.
    if VIDEOS_SEARCH_AVAILABLE and VideosSearch is not None:
        try:
            results = VideosSearch(query, limit=1)
            data = await results.next()
            items = (data or {}).get("result") or []
            if items:
                item = items[0]
                duration = item.get("duration")
                duration_sec = None
                if duration:
                    try:
                        duration_sec = sum(
                            int(x) * 60 ** i
                            for i, x in enumerate(reversed(str(duration).split(":")))
                        )
                    except Exception:
                        pass
                if item.get("id"):
                    return {
                        "id": item.get("id"),
                        "title": item.get("title") or "Unknown",
                        "webpage_url": item.get("link") or (
                            f"https://www.youtube.com/watch?v={item.get('id')}"
                        ),
                        "thumbnail": (
                            (item.get("thumbnails") or [{}])[0]
                            .get("url", "")
                            .split("?")[0]
                        ),
                        "duration": duration_sec,
                    }
        except Exception as e:
            logging.warning("youtube-search-python failed for %r: %s", query, e)

    # 2) yt-dlp flat-search fallback. This does NOT request audio formats.
    if youtube_dl is not None:
        try:
            opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": True,
                "noplaylist": True,
                "socket_timeout": 20,
                "retries": 2,
            }
            with youtube_dl.YoutubeDL(opts) as ydl:
                data = ydl.extract_info(f"ytsearch1:{query}", download=False)

            entries = [x for x in (data.get("entries") or []) if x]
            if entries:
                item = entries[0]
                vid = item.get("id")
                if vid:
                    return {
                        "id": vid,
                        "title": item.get("title") or "YouTube Audio",
                        "webpage_url": item.get("webpage_url") or (
                            f"https://www.youtube.com/watch?v={vid}"
                        ),
                        "thumbnail": item.get("thumbnail"),
                        "duration": int(item.get("duration")) if item.get("duration") else None,
                    }
        except Exception as e:
            logging.warning("yt-dlp flat YouTube search failed for %r: %s", query, e)

    return None


async def resolve_track(query: str) -> Optional[Dict[str, Any]]:
    """Resolve a song query to stable metadata (video id + title)."""
    query = (query or "").strip()
    if not query:
        return None
    if _safe_video_id(query):
        vid = _safe_video_id(query)
        info = await asyncio.to_thread(_youtube_info_local, query)
        if info:
            return info
        return {"id": vid, "title": "YouTube Audio", "webpage_url": f"https://www.youtube.com/watch?v={vid}", "thumbnail": None, "duration": None}
    if looks_like_url(query):
        info = await asyncio.to_thread(_youtube_info_local, query)
        return info
    info = await _youtube_search_info(query)
    if info and info.get("id"):
        return info

    # Last resort: normal yt-dlp metadata lookup.
    return await asyncio.to_thread(_youtube_info_local, query)


async def download_song(link: str) -> Optional[str]:
    """Download a YouTube audio track to a local MP3 cache.

    This follows the supplied sample's API-download architecture: the bot
    requests an MP3 from an external download API when configured, stores it
    under downloads/<video_id>.mp3, and then plays the local file.
    """
    video_id = _safe_video_id(link)
    if not video_id:
        info = await resolve_track(link)
        video_id = info.get("id") if info else None
    if not video_id:
        return None

    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    file_path = os.path.join(DOWNLOADS_DIR, f"{video_id}.mp3")
    if os.path.isfile(file_path) and os.path.getsize(file_path) > 0:
        return file_path

    # Preferred path: external API, matching the supplied sample.
    if YOUTUBE_API_URL and YOUTUBE_API_KEY:
        try:
            timeout = aiohttp.ClientTimeout(total=YOUTUBE_API_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{YOUTUBE_API_URL}/download",
                    params={"url": video_id, "type": "audio", "api_key": YOUTUBE_API_KEY},
                ) as resp:
                    if resp.status == 200:
                        tmp_path = file_path + ".part"
                        with open(tmp_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(131072):
                                if chunk:
                                    f.write(chunk)
                        if os.path.isfile(tmp_path) and os.path.getsize(tmp_path) > 0:
                            os.replace(tmp_path, file_path)
                            logging.info("Audio downloaded through external API: %s", video_id)
                            return file_path
                    else:
                        body = await resp.text()
                        logging.warning("External YouTube API returned %s: %s", resp.status, body[:300])
        except Exception as e:
            logging.warning("External YouTube API download failed: %s", e)
            try:
                if os.path.exists(file_path + ".part"):
                    os.remove(file_path + ".part")
            except Exception:
                pass

    # Fallback: local yt-dlp download. This is also file-based, not a direct stream URL.
    if youtube_dl is None:
        return None
    opts = {
        "format": "bestaudio[acodec!=none]/bestaudio",
        "outtmpl": os.path.join(DOWNLOADS_DIR, f"{video_id}.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "cachedir": False,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        "extractor_args": {"youtube": {"player_client": ["android", "web_safari"]}},
    }
    if YT_DLP_COOKIES and os.path.isfile(YT_DLP_COOKIES):
        opts["cookiefile"] = YT_DLP_COOKIES
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        await asyncio.to_thread(_yt_download_sync, url, opts)
        if os.path.isfile(file_path) and os.path.getsize(file_path) > 0:
            return file_path
        # Some ffmpeg/postprocessor combinations may produce a different extension.
        for name in os.listdir(DOWNLOADS_DIR):
            if name.startswith(video_id + ".") and name.endswith((".mp3", ".m4a", ".webm", ".opus")):
                candidate = os.path.join(DOWNLOADS_DIR, name)
                if os.path.getsize(candidate) > 0:
                    return candidate
    except Exception as e:
        logging.warning("Local yt-dlp audio download failed for %s: %s", video_id, e)
    return None


def _yt_download_sync(url: str, opts: dict):
    with youtube_dl.YoutubeDL(opts) as ydl:
        ydl.download([url])


async def get_downloaded_track(query: str) -> Optional[Dict[str, Any]]:
    info = await resolve_track(query)
    if not info or not info.get("id"):
        return None
    path = await download_song(info["id"])
    if not path:
        return None
    info["file_path"] = path
    info["is_local"] = True
    return info


def extract_audio_url(query: str) -> Optional[Dict[str, Any]]:
    """Compatibility resolver for the old /api/stream endpoint.

    New playback uses local downloaded files via get_downloaded_track().
    """
    info = _youtube_info_local(query)
    if not info or not info.get("id"):
        return None
    return info


# ====================== AUDIO STREAM REST API ======================
_api_app = FastAPI(title="DLK Audio Stream API", version="1.0.0")
_api_rate = {}
_api_rate_lock = asyncio.Lock()

async def _api_check_rate(client_id: str):
    now = time.monotonic()
    async with _api_rate_lock:
        values = _api_rate.setdefault(client_id, [])
        cutoff = now - STREAM_API_RATE_WINDOW
        values[:] = [t for t in values if t > cutoff]
        if len(values) >= STREAM_API_RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
        values.append(now)

def _api_auth(x_api_key: Optional[str]):
    if not STREAM_API_KEY:
        raise HTTPException(status_code=503, detail="STREAM_API_KEY is not configured.")
    if x_api_key != STREAM_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")

@_api_app.get("/")
async def _api_root():
    return {
        "success": True,
        "service": "DLK Audio Download API",
        "version": "2.0.0",
        "endpoints": ["/health", "/api/download?q=<song-or-youtube-url>", "/api/stream?q=<song-or-youtube-url>"],
    }


@_api_app.get("/health")
async def _api_health():
    return {"success": True, "status": "online"}


@_api_app.get("/api/download")
async def _api_download(
    q: str = Query(..., min_length=1, max_length=200),
    x_api_key: Optional[str] = Header(default=None),
    key: Optional[str] = Query(default=None),
):
    # Header is preferred; query key is kept for easy browser testing.
    _api_auth(x_api_key or key)
    await _api_check_rate(x_api_key or key or "unknown")
    result = await get_downloaded_track(q)
    if not result or not result.get("file_path"):
        raise HTTPException(status_code=404, detail="Could not download audio.")
    return FileResponse(
        result["file_path"],
        media_type="audio/mpeg",
        filename=os.path.basename(result["file_path"]),
        headers={"X-Track-Title": result.get("title", "Unknown")},
    )


@_api_app.get("/api/stream")
async def _api_stream(
    q: str = Query(..., min_length=1, max_length=200),
    x_api_key: Optional[str] = Header(default=None),
    key: Optional[str] = Query(default=None),
):
    # Backwards-compatible endpoint. It now returns a stable local API URL
    # instead of an expiring YouTube media URL.
    _api_auth(x_api_key or key)
    await _api_check_rate(x_api_key or key or "unknown")
    result = await resolve_track(q)
    if not result or not result.get("id"):
        raise HTTPException(status_code=404, detail="Could not resolve the requested audio.")
    return JSONResponse({
        "success": True,
        "title": result.get("title") or "Unknown",
        "video_id": result.get("id"),
        "duration": result.get("duration"),
        "thumbnail": result.get("thumbnail"),
        "webpage_url": result.get("webpage_url"),
        "download_url": f"/api/download?q={result.get('id')}",
    })

def _run_audio_api():
    config = uvicorn.Config(_api_app, host=STREAM_API_HOST, port=STREAM_API_PORT,
                            log_level="info", access_log=False)
    uvicorn.Server(config).run()

def start_audio_api():
    if not STREAM_API_KEY:
        logging.warning("Audio API disabled: STREAM_API_KEY is not configured.")
        return None
    thread = threading.Thread(target=_run_audio_api, name="dlk-audio-api", daemon=True)
    thread.start()
    logging.info("Audio Stream API started on %s:%s", STREAM_API_HOST, STREAM_API_PORT)
    return thread

# ====================== THUMBNAIL PROCESSING ======================
def changeImageSize(maxWidth, maxHeight, image):
    widthRatio = maxWidth / image.size[0]
    heightRatio = maxHeight / image.size[1]
    newWidth = int(widthRatio * image.size[0])
    newHeight = int(heightRatio * image.size[1])
    newImage = image.resize((newWidth, newHeight))
    return newImage

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
    """
    Given a PIL Image, produce a circular artwork Image with a white border and subtle shadow.
    Returns an RGBA Image of size (diameter + 2*border, diameter + 2*border).
    """
    # Crop to square centered and resize to diameter
    try:
        square = ImageOps.fit(image, (diameter, diameter), centering=(0.5, 0.5))
    except Exception:
        square = image.resize((diameter, diameter), Image.LANCZOS)

    # Create mask for circle
    mask = Image.new('L', (diameter, diameter), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, diameter, diameter), fill=255)

    # Prepare circular image
    circ = Image.new('RGBA', (diameter, diameter), (0, 0, 0, 0))
    circ.paste(square.convert('RGBA'), (0, 0), mask=mask)

    # Create output with border & shadow
    out_size = diameter + border * 2
    out = Image.new('RGBA', (out_size, out_size), (0, 0, 0, 0))

    # shadow
    shadow = Image.new('RGBA', (out_size, out_size), (0, 0, 0, 0))
    shadow_mask = Image.new('L', (out_size, out_size), 0)
    draw_sm = ImageDraw.Draw(shadow_mask)
    draw_sm.ellipse((border//2, border//2, out_size - border//2, out_size - border//2), fill=200)
    shadow.putalpha(shadow_mask)
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=6))
    out = Image.alpha_composite(out, shadow)

    # white border (slightly smaller than outer)
    border_layer = Image.new('RGBA', (out_size, out_size), (255, 255, 255, 0))
    draw_bl = ImageDraw.Draw(border_layer)
    draw_bl.ellipse((border, border, out_size - border, out_size - border), fill=(255, 255, 255, 255))
    # cut inner area to be transparent for the artwork
    inner_margin = border + 4
    draw_bl.ellipse((inner_margin, inner_margin, out_size - inner_margin, out_size - inner_margin), fill=(0, 0, 0, 0))
    out = Image.alpha_composite(out, border_layer)

    # paste circular artwork centered
    paste_pos = (border, border)
    out.paste(circ, paste_pos, circ)

    return out

async def _process_image_and_overlay(src_path: str, out_key: str, title: str) -> Optional[str]:
    """
    Create Apple-Music-like image (1280x720) using src_path and overlay text.
    The center artwork is circular and placed on top of a blurred background.
    Returns path to processed PNG.
    """
    try:
        image = Image.open(src_path).convert("RGBA")
        # Create blurred background fitted to 1280x720 (crop & scale)
        try:
            background = ImageOps.fit(image, (1280, 720), centering=(0.5, 0.5)).convert("RGBA")
        except Exception:
            background = image.resize((1280, 720), Image.LANCZOS).convert("RGBA")
        background = background.filter(ImageFilter.BoxBlur(6))
        enhancer = ImageEnhance.Brightness(background)
        background = enhancer.enhance(0.85)

        # Create circular artwork
        art = _create_circular_artwork(image, diameter=520, border=10)

        # Position artwork on the left with some padding
        art_x = 60
        art_y = (720 - art.size[1]) // 2
        background.paste(art, (art_x, art_y), art)

        # Draw text (title and small label)
        draw = ImageDraw.Draw(background)
        try:
            title_font = ImageFont.truetype("arial.ttf", 48)
            small_font = ImageFont.truetype("arial.ttf", 18)
        except Exception:
            title_font = ImageFont.load_default()
            small_font = ImageFont.load_default()

        # small top-left label
        draw.text((20, 20), "DLK DEVELOPER", fill="white", font=small_font)

        # Title to the right of artwork
        title_x = art_x + art.size[0] + 30
        title_y = art_y + 30
        # Draw shadow for title for readability
        shadow_color = (0, 0, 0, 200)
        for dx, dy in ((1, 1), (2, 2)):
            draw.text((title_x + dx, title_y + dy), clear_title(title), fill=shadow_color, font=title_font)
        draw.text((title_x, title_y), clear_title(title), fill="white", font=title_font)

        out_path = os.path.join(THUMB_CACHE_DIR, f"{out_key}.png")
        background.save(out_path)
        return out_path
    except Exception as e:
        logging.debug(f"_process_image_and_overlay failed: {e}")
        return None

async def get_thumb_from_url_or_webpage(thumbnail_url: Optional[str], webpage: Optional[str], title: str) -> Optional[str]:
    """
    Determine a cache key and produce a processed thumbnail file path.
    If thumbnail_url is a URL string, download+process it. If not available, try webpage (youtube id).
    """
    # prefer thumbnail_url
    if thumbnail_url:
        # if it's already a local path
        if os.path.isfile(thumbnail_url):
            # assume already processed or raw image; try to process into final style
            key = re.sub(r"[^0-9A-Za-z_-]", "_", os.path.basename(thumbnail_url))[:40]
            return await _process_image_and_overlay(thumbnail_url, key, title)
        # if it's a URL
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
    # fallback: try webpage youtube id -> fetch via yt-dlp or VideosSearch
    if webpage:
        vid_id = get_youtube_id(webpage) or re.sub(r"[^0-9A-Za-z_-]", "_", webpage)[:40]
        # first try to use youtubesearchpython
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
        # fallback to yt-dlp
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

# ====================== DB / LOGGING / PRIVILEGES ======================
def init_db_sync():
    global db_client, db
    if not MONGO_URI:
        logging.info("MONGO_URI not set - DB disabled.")
        return
    if MongoClient is None:
        raise RuntimeError("pymongo required. Install with: pip install pymongo")
    db_client = MongoClient(MONGO_URI)
    db = db_client[MONGO_DBNAME]
    db.blocked.create_index("chat_id")
    db.logs.create_index("ts")
    db.playing.create_index("chat_id", unique=True)
    logging.info(f"Connected to MongoDB: {MONGO_DBNAME}")

def save_play_state_db(chat_id: int, state: dict):
    if db is None:
        return
    try:
        db.playing.update_one({"chat_id": chat_id}, {"$set": state}, upsert=True)
    except Exception as e:
        logging.warning(f"Failed to save play state to DB: {e}")

def remove_play_state_db(chat_id: int):
    if db is None:
        return
    try:
        db.playing.delete_one({"chat_id": chat_id})
    except Exception as e:
        logging.warning(f"Failed to remove play state from DB: {e}")

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

    if not LOG_CHANNEL_ID:
        return

    if not _valid_log_target(LOG_CHANNEL_ID):
        logging.warning(f"LOG_CHANNEL_ID format invalid: {LOG_CHANNEL_ID}")
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
        logging.info("DB not configured; block state will not persist.")
        return
    db.blocked.update_one({"chat_id": chat_id}, {"$set": {"chat_id": chat_id, "by": by_user, "reason": reason, "ts": time.time()}}, upsert=True)

def unblock_group_sync(chat_id: int):
    if db is None:
        return
    db.blocked.delete_one({"chat_id": chat_id})

async def dlk_privilege_validator(subject: Union[Message, CallbackQuery]) -> bool:
    try:
        # Handles both Message and CallbackQuery. Checks owner, chat admins, and anonymous sender_chat.
        if isinstance(subject, CallbackQuery):
            user = subject.from_user
            chat = subject.message.chat
            sender_chat = getattr(subject.message, "sender_chat", None)
        else:
            user = subject.from_user
            chat = subject.chat
            sender_chat = getattr(subject, "sender_chat", None)

        # owner always allowed
        if user and user.id == OWNER_ID:
            return True

        # private chats: deny (admin controls are for groups)
        if chat.type == "private":
            return False

        # check explicit user (normal admin messages)
        if user:
            try:
                member = await bot.get_chat_member(chat.id, user.id)
                status = getattr(member, "status", "").lower()
                if status in ("administrator", "creator"):
                    return True
            except Exception:
                pass

        # check anonymous sender_chat (channel-like admin)
        if sender_chat:
            try:
                # sender_chat is a channel/chat object; check its status
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

# ====================== UI HELPERS ======================
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
    bottom = [InlineKeyboardButton("👨‍💻 Dev", url=DEV_LINK), InlineKeyboardButton("💬 Support", url=SUPPORT_LINK)]
    return InlineKeyboardMarkup([controls, bottom])

# ====================== TIMER / VOICE HELPERS ======================
async def update_radio_timer(chat_id: int, msg_id: int, title: str, start_time: float):
    while True:
        try:
            elapsed = int(time.time() - start_time)
            m, s = divmod(elapsed, 60)
            h, m = divmod(m, 60)
            timer = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
            caption = f"🎧 Now Playing: {title}\n⏳ Duration: {timer}"
            await _edit_player_message(
                chat_id,
                msg_id,
                caption,
                reply_markup=player_controls_markup(chat_id),
            )
        except Exception as e:
            logging.debug(f"Timer update failed for {chat_id}/{msg_id}: {e}")
            break
        await asyncio.sleep(8)

async def leave_voice_chat(chat_id: int):
    try:
        if chat_id in radio_tasks:
            radio_tasks[chat_id].cancel()
            radio_tasks.pop(chat_id, None)
        if chat_id in track_watchers:
            try:
                track_watchers[chat_id].cancel()
            except Exception:
                pass
            track_watchers.pop(chat_id, None)
        if chat_id in radio_paused:
            radio_paused.discard(chat_id)
        radio_state.pop(chat_id, None)
        remove_play_state_db(chat_id)
        try:
            await _safe_call_py_method("leave_call", chat_id)
            # try alternate names if any
            await _safe_call_py_method("stop", chat_id)
        except Exception as e:
            logging.debug(f"leave_call failed for {chat_id}: {e}")
    except Exception as e:
        logging.warning(f"Failed to leave VC/cancel task for {chat_id}: {e}")

def store_play_state(chat_id: int, title: str, url: str, msg_id: int, start_time: Optional[float], elapsed: float = 0.0, paused: bool = False):
    state = {"chat_id": chat_id, "station": title, "url": url, "msg_id": msg_id, "start_time": start_time, "elapsed": elapsed, "paused": paused, "ts": time.time()}
    radio_state[chat_id] = state
    save_play_state_db(chat_id, state)

# ====================== HELPER: safe-call for call_py methods ======================
async def _safe_call_py_method(method_name: str, *args, **kwargs):
    """
    Call a method on call_py (PyTgCalls) in a compatible way:
    - If the attribute exists and when called returns an awaitable, await it.
    - If it's a synchronous callable, call it and return.
    - If not present, try alternative method names if relevant.
    """
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

async def _call_py_method_strict(method_name: str, *args, **kwargs):
    """Call a PyTgCalls method and propagate failures."""
    if not hasattr(call_py, method_name):
        raise AttributeError(f"PyTgCalls has no method: {method_name}")
    method = getattr(call_py, method_name)
    if not callable(method):
        raise TypeError(f"PyTgCalls attribute is not callable: {method_name}")
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _edit_player_message(chat_id: int, message_id: int, text: str, reply_markup=None):
    """
    Edit either a text message or a photo caption.

    The old code always called edit_message_caption(), which breaks the
    /radio button flow because the radio menu is a text message.
    """
    try:
        msg = await bot.get_messages(chat_id, message_id)
    except Exception:
        msg = None

    try:
        if msg and getattr(msg, "photo", None):
            return await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=text,
                reply_markup=reply_markup,
            )
        return await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
    except Exception:
        # Fallback based on message type in case Telegram's cached object
        # is unavailable.
        try:
            return await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=text,
                reply_markup=reply_markup,
            )
        except Exception:
            return await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
            )


async def ensure_assistant_in_chat(chat_id: int, message: Optional[Message] = None) -> bool:
    """
    Make sure the assistant is actually visible to Telegram as a member.

    Joining an invite can return before Telegram's membership cache is
    updated, so verify it several times instead of assuming join_chat()
    succeeded.
    """
    try:
        me = await assistant.get_me()
        assistant_id = me.id
    except Exception as e:
        logging.error("Cannot resolve assistant account: %s", e)
        return False

    async def is_member() -> bool:
        try:
            member = await assistant.get_chat_member(chat_id, assistant_id)
            status = str(getattr(member, "status", "")).lower()
            return status not in {"", "left", "kicked"}
        except Exception:
            return False

    if await is_member():
        return True

    try:
        invite = await bot.create_chat_invite_link(
            chat_id,
            member_limit=1,
            name="DLK BOT assistant",
        )
        invite_link = invite.invite_link

        try:
            await assistant.join_chat(invite_link)
        except Exception as e:
            logging.warning("Assistant join_chat failed: %s", e)

        # Telegram membership propagation can take a moment.
        for delay in (0.5, 1.0, 2.0, 3.0):
            await asyncio.sleep(delay)
            if await is_member():
                return True

        if message:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Invite Link", url=invite_link)],
                [InlineKeyboardButton(
                    "ℹ️ How to add assistant",
                    callback_data="assistant_invite_help"
                )],
            ])
            try:
                await message.reply_text(
                    "❌ Telegram has not confirmed the assistant membership yet.\n\n"
                    "Add the assistant account to this group as a member/admin, "
                    "give it permission to speak in voice chats, then run /play again.",
                    reply_markup=kb,
                )
            except Exception:
                pass
        return False

    except Exception as e:
        logging.error("Cannot create assistant invite for %s: %s", chat_id, e)
        if message:
            try:
                await message.reply_text(
                    "❌ Assistant is not in this group.\n"
                    "Please add the assistant account to the group and give it "
                    "permission to speak in voice chats, then try /play again."
                )
            except Exception:
                pass
        return False

# ====================== PREPARE ENTRY FROM REPLIED AUDIO (same approach) ======================
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
                ext = ".bin"

        base_name = f"audio_{int(time.time())}_{random.randint(1000,9999)}"
        download_path = os.path.join(DOWNLOADS_DIR, base_name + ext)
        local_path = await bot.download_media(reply_msg, file_name=download_path)

        title = getattr(media_field, "title", None) or getattr(media_field, "file_name", None) or reply_msg.caption or "Telegram Audio"
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

# ====================== CORE play_entry (thumbnail fix & robust calls) ======================
def _player_caption(title: str, entry: dict, user=None) -> str:
    """Compact single-message player caption with title + requester mention."""
    title = (title or "Unknown").strip()
    if len(title) > 90:
        title = title[:87] + "..."
    mention = ""
    if user:
        try:
            if getattr(user, "username", None):
                mention = f"@{user.username}"
            else:
                name = (getattr(user, "first_name", None) or "User").strip()
                if getattr(user, "last_name", None):
                    name += f" {user.last_name}"
                mention = f'<a href="tg://user?id={user.id}">{name}</a>'
        except Exception:
            mention = ""
    requester = f"\n👤 Requested by: {mention}" if mention else ""
    return f"🎵 <b>{title}</b>{requester}"

async def _delete_old_player_message(chat_id: int):
    old = radio_state.get(chat_id) or {}
    old_id = old.get("msg_id")
    if old_id:
        try:
            await bot.delete_messages(chat_id, old_id)
        except Exception:
            pass

async def play_entry(chat_id: int, entry: dict, reply_message: Optional[Message] = None):
    """
    Play one AUDIO-ONLY entry in the Telegram voice chat.

    YouTube media URLs are short-lived, so if playback fails we resolve the
    saved YouTube page again and retry once with a fresh audio URL.
    """
    try:
        if chat_id in radio_tasks:
            radio_tasks[chat_id].cancel()
            radio_tasks.pop(chat_id, None)

        title = entry.get("title") or "Unknown"
        user = entry.get("requester")
        is_local = bool(entry.get("is_local"))
        stream_source = entry.get("file_path") or entry.get("stream_url")
        headers = entry.get("http_headers") or {}

        # YouTube tracks are downloaded to a local MP3 before playback.
        # This avoids expired direct YouTube media URLs.
        if not is_local:
            query = entry.get("video_id") or entry.get("webpage") or entry.get("title")
            refreshed = await get_downloaded_track(query)
            if not refreshed or not refreshed.get("file_path"):
                raise RuntimeError("Audio download failed. Configure SHRUTI_API_URL/SHRUTI_API_KEY or valid yt-dlp cookies.")
            entry.update(refreshed)
            title = entry.get("title") or title
            stream_source = entry.get("file_path")
            is_local = True

        if not stream_source or not os.path.isfile(stream_source):
            raise RuntimeError("No downloaded audio file was available.")

        async def start_audio(source: str, source_headers: dict):
            # IMPORTANT: explicitly IGNORE the video track. PyTgCalls'
            # MediaStream otherwise auto-detects video for media URLs.
            kwargs = {}
            if source_headers:
                kwargs["headers"] = source_headers
            return await _call_py_method_strict(
                "play",
                chat_id,
                MediaStream(
                    source,
                    AudioQuality.HIGH,
                    video_flags=MediaStream.Flags.IGNORE,
                    **kwargs,
                ),
            )

        try:
            await start_audio(stream_source, headers)
        except Exception:
            # If a cached file is broken, remove it and download once again.
            if is_local and entry.get("video_id"):
                try:
                    if os.path.isfile(stream_source):
                        os.remove(stream_source)
                except Exception:
                    pass
                refreshed = await get_downloaded_track(entry["video_id"])
                if not refreshed or not refreshed.get("file_path"):
                    raise
                entry.update(refreshed)
                stream_source = entry["file_path"]
                title = entry.get("title") or title
                await start_audio(stream_source, {})
            else:
                raise

        await _delete_old_player_message(chat_id)

        # Prepare thumbnail only for the Telegram "Now Playing" message.
        # It has no effect on the voice-chat stream and does not enable video.
        thumb_path = None
        thumb_val = entry.get("thumbnail")

        if thumb_val and isinstance(thumb_val, str) and os.path.isfile(thumb_val):
            thumb_path = thumb_val
        elif thumb_val and isinstance(thumb_val, str) and thumb_val.startswith("http"):
            thumb_path = await get_thumb_from_url_or_webpage(
                thumb_val, entry.get("webpage"), title
            )
        else:
            thumb_path = await get_thumb_from_url_or_webpage(
                None, entry.get("webpage"), title
            )

        if thumb_path and os.path.isfile(thumb_path):
            try:
                msg = await bot.send_photo(
                    chat_id,
                    photo=thumb_path,
                    caption=_player_caption(title, entry, user),
                    reply_markup=player_controls_markup(chat_id),
                )
            except Exception:
                msg = await bot.send_message(
                    chat_id,
                    _player_caption(title, entry, user),
                    reply_markup=player_controls_markup(chat_id),
                )
        else:
            msg = await bot.send_message(
                chat_id,
                _player_caption(title, entry, user),
                reply_markup=player_controls_markup(chat_id),
            )

        start_time = time.time()
        store_play_state(
            chat_id,
            title,
            entry.get("stream_url"),
            msg.id,
            start_time,
            elapsed=0.0,
            paused=False,
        )
        radio_tasks[chat_id] = asyncio.create_task(
            update_radio_timer(chat_id, msg.id, title, start_time)
        )
        radio_paused.discard(chat_id)

        log_event_sync("music_started", {
            "chat_id": chat_id,
            "title": title,
            "audio_only": True,
        })

        duration = entry.get("duration")
        if duration:
            if chat_id in track_watchers:
                track_watchers[chat_id].cancel()
            track_watchers[chat_id] = asyncio.create_task(
                track_watcher(chat_id, duration, msg.id)
            )

        return True

    except asyncio.CancelledError:
        raise
    except Exception:
        logging.error("Audio play entry failed", exc_info=True)
        await leave_voice_chat(chat_id)
        return False

async def track_watcher(chat_id: int, duration: int, msg_id: int):
    try:
        await asyncio.sleep(max(1, duration) + 0.5)
        q = radio_queue.get(chat_id, [])
        if q:
            next_entry = q.pop(0)
            radio_queue[chat_id] = q
            await play_entry(chat_id, next_entry)
            log_event_sync("music_auto_skipped", {"chat_id": chat_id, "title": next_entry.get("title")})
        else:
            try:
                await leave_voice_chat(chat_id)
            except Exception:
                pass
            try:
                await _edit_player_message(
                    chat_id,
                    msg_id,
                    "▶️ Playback finished.",
                    reply_markup=None,
                )
            except Exception:
                pass
            log_event_sync("music_track_ended", {"chat_id": chat_id})
    except asyncio.CancelledError:
        return
    except Exception as e:
        logging.debug(f"track_watcher error for {chat_id}: {e}")

# ====================== COMMANDS: /play supporting youtube and reply-to-audio ======================
@bot.on_message(filters.group & filters.command(["play", "p"]))
async def cmd_play(_, message: Message):
    chat_id = message.chat.id
    user = message.from_user
    if is_group_blocked_sync(chat_id):
        return await message.reply_text("❌ This group is blocked from using DLK BOT.")

    # Ensure the assistant is genuinely confirmed as a Telegram member.
    if not await ensure_assistant_in_chat(chat_id, message):
        return

    entry = None
    info_msg = None

    # If reply to an audio/voice/document -> handle local file play
    if message.reply_to_message:
        entry = await prepare_entry_from_reply(message.reply_to_message)
        if entry:
            info_msg = await message.reply_text("Preparing your audio reply...")
    # If not a replied audio, parse query for YouTube/search
    if not entry:
        query = None
        if len(message.command) > 1:
            query = message.text.split(None, 1)[1]
        elif message.reply_to_message and message.reply_to_message.text:
            query = message.reply_to_message.text
        if not query:
            return await message.reply_text("Usage: /play <YouTube url or search terms> OR reply to an audio/voice file and use /play")
        info_msg = await message.reply_text("🔎 Searching and downloading audio...")
        info = await resolve_track(query)
        if info is None or not info.get("id"):
            await info_msg.edit_text("❌ Could not find that YouTube track.")
            return
        entry = {
            "title": info.get("title"),
            "video_id": info.get("id"),
            "file_path": None,
            "webpage": info.get("webpage_url"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "is_local": False,
        }

    # Ensure queue structure
    if chat_id not in radio_queue:
        radio_queue[chat_id] = []
    current_state = radio_state.get(chat_id)

    # If something currently playing and not paused, queue the new entry
    if current_state and not current_state.get("paused"):
        radio_queue[chat_id].append(entry)
        try:
            if info_msg:
                await info_msg.edit_text(f"➕ Added to queue: {entry['title']}")
        except Exception:
            pass
        log_event_sync("music_queued", {"chat_id": chat_id, "title": entry["title"], "by": user.id})
        return

    ok = await play_entry(chat_id, entry, reply_message=message)
    if ok:
        try:
            if info_msg:
                await info_msg.edit_text(f"▶️ Now playing: {entry['title']}")
        except Exception:
            pass
    else:
        try:
            if info_msg:
                await info_msg.edit_text("❌ Failed to play the requested track.")
        except Exception:
            pass

# ====================== SKIP / QUEUE / STOP ======================
@bot.on_message(filters.group & filters.command(["skip", "s"]))
async def cmd_skip(_, message: Message):
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text("Only admins can skip tracks.")
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
        await message.reply_text(f"Failed to play next track: {next_entry.get('title')}")

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
        return await message.reply_text("Only admins can stop the playback!")
    await leave_voice_chat(chat_id)
    await message.reply_text("DLK bot stopped & cleaned up.")
    log_event_sync("radio_stopped_text", {"chat_id": chat_id, "by": message.from_user.id})

# ====================== RADIO-SPECIFIC COMMANDS ADDED: /radio, /rend, /rskip, /rpush, /rresume ======================
@bot.on_message(filters.group & filters.command(["radio"]))
async def cmd_radio_menu(_, message: Message):
    """
    Sends the radio stations menu - same button-based UI as existing callbacks.
    """
    chat_id = message.chat.id
    if is_group_blocked_sync(chat_id):
        return await message.reply_text("❌ This group is blocked from using DLK BOT.")
    kb = radio_buttons(0)
    try:
        await message.reply_text("📻 Radio Stations - choose one:", reply_markup=kb)
    except Exception:
        await message.reply_text("Failed to show radio menu.")

@bot.on_message(filters.group & filters.command(["rend"]))
async def cmd_rend(_, message: Message):
    """
    /rend - stop the radio and leave voice chat (radio-specific end)
    """
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text("Only admins can end the radio.")
    try:
        await leave_voice_chat(chat_id)
        await message.reply_text("✅ Radio ended and assistant left the voice chat.")
        log_event_sync("radio_rend", {"chat_id": chat_id, "by": message.from_user.id})
    except Exception as e:
        logging.warning(f"cmd_rend failed: {e}")
        await message.reply_text("Failed to end the radio.")

@bot.on_message(filters.group & filters.command(["rskip"]))
async def cmd_rskip(_, message: Message):
    """
    /rskip - skip to next queued entry for this chat. If no queue -> stop radio.
    """
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text("Only admins can skip radio tracks.")
    q = radio_queue.get(chat_id, [])
    if not q:
        await leave_voice_chat(chat_id)
        await message.reply_text("⛔ Skipped. No more items in queue.")
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
        await message.reply_text(f"⏭️ Now playing: {next_entry['title']}")
        log_event_sync("radio_rskip", {"chat_id": chat_id, "title": next_entry["title"], "by": message.from_user.id})
    else:
        await message.reply_text(f"Failed to play next: {next_entry.get('title')}")

@bot.on_message(filters.group & filters.command(["rpush"]))
async def cmd_rpush(_, message: Message):
    """
    /rpush <station_name or url> - push a radio stream to the queue.
    If station_name is known in RADIO_STATION, use it. Else if URL is provided, use as stream_url.
    """
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text("Only admins can add to the radio queue.")
    args = None
    if len(message.command) > 1:
        args = message.text.split(None, 1)[1].strip()
    if not args:
        return await message.reply_text("Usage: /rpush <station_name or stream_url>\nExample: /rpush SirasaFM OR /rpush https://stream.example.com/live")
    # determine stream
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
        # try fuzzy match (case-insensitive) for station names
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

@bot.on_message(filters.group & filters.command(["rresume", "rremuse"]))
async def cmd_rresume(_, message: Message):
    """
    /rresume (alias /rremuse) - resume radio if paused.
    This mirrors the inline callback resume behavior but via text command.
    """
    chat_id = message.chat.id
    if not await dlk_privilege_validator(message):
        return await message.reply_text("Only admins can resume the radio.")
    state = radio_state.get(chat_id)
    if not state:
        return await message.reply_text("Nothing to resume.")
    try:
        await _safe_call_py_method("resume_stream", chat_id)
        await _safe_call_py_method("resume", chat_id)
        elapsed = state.get("elapsed", 0.0) or 0.0
        start_time = time.time() - elapsed
        state["paused"] = False
        state["elapsed"] = 0.0
        state["start_time"] = start_time
        radio_paused.discard(chat_id)
        store_play_state(chat_id, state.get("station"), state.get("url"), state.get("msg_id"), start_time, elapsed=0.0, paused=False)
        # restart timer
        if chat_id in radio_tasks:
            try:
                radio_tasks[chat_id].cancel()
            except Exception:
                pass
            radio_tasks.pop(chat_id, None)
        radio_tasks[chat_id] = asyncio.create_task(update_radio_timer(chat_id, state.get("msg_id"), state.get("station"), start_time))
        try:
            # try to update message controls
            await bot.edit_message_reply_markup(chat_id, state.get("msg_id"), reply_markup=player_controls_markup(chat_id))
        except Exception:
            pass
        await message.reply_text("▶️ Radio resumed.")
        log_event_sync("radio_resumed_cmd", {"chat_id": chat_id, "by": message.from_user.id})
    except Exception as e:
        logging.debug(f"cmd_rresume failed: {e}")
        await message.reply_text("Failed to resume the radio.")

# ====================== BLOCK / UNBLOCK (owner only) ======================
@bot.on_message(filters.group & filters.command(["bl", "block"]))
async def cmd_block_group(_, message: Message):
    # Only owner can block a group (per request)
    if not message.from_user or message.from_user.id != OWNER_ID:
        return await message.reply_text("Only the bot owner can block this group.")
    chat_id = message.chat.id
    try:
        block_group_sync(chat_id, message.from_user.id, reason="blocked by owner via /bl")
        await message.reply_text("✅ This group has been blocked from using DLK BOT.")
        log_event_sync("group_blocked", {"chat_id": chat_id, "by": message.from_user.id})
    except Exception as e:
        logging.warning(f"Failed to block group {chat_id}: {e}")
        await message.reply_text("Failed to block the group.")

@bot.on_message(filters.group & filters.command(["unbl", "unblock"]))
async def cmd_unblock_group(_, message: Message):
    # Only owner can unblock a group
    if not message.from_user or message.from_user.id != OWNER_ID:
        return await message.reply_text("Only the bot owner can unblock this group.")
    chat_id = message.chat.id
    try:
        unblock_group_sync(chat_id)
        await message.reply_text("✅ This group has been unblocked.")
        log_event_sync("group_unblocked", {"chat_id": chat_id, "by": message.from_user.id})
    except Exception as e:
        logging.warning(f"Failed to unblock group {chat_id}: {e}")
        await message.reply_text("Failed to unblock the group.")

# ====================== OWNER PANEL (private) ======================
@bot.on_message(filters.private & filters.command(["panel"]))
async def owner_panel(_, message: Message):
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

# ====================== CALLBACK HANDLERS (pause/resume/skip/stop) ======================
@bot.on_callback_query(filters.regex("^music_skip$"))
async def cb_music_skip(_, query: CallbackQuery):
    if not await dlk_privilege_validator(query):
        return await query.answer("Only admins can skip tracks.", show_alert=True)
    chat_id = query.message.chat.id
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
        await query.answer(f"⏭️ Now: {next_entry['title']}", show_alert=False)
        log_event_sync("music_skipped", {"chat_id": chat_id, "title": next_entry["title"], "by": query.from_user.id if query.from_user else None})
    else:
        await query.answer("Failed to skip to next track.", show_alert=True)

@bot.on_callback_query(filters.regex("^radio_pause$"))
async def radio_pause_cb(_, query: CallbackQuery):
    if not await dlk_privilege_validator(query):
        return await query.answer("Only admins can pause the radio!", show_alert=True)
    chat_id = query.message.chat.id
    state = radio_state.get(chat_id)
    if not state:
        return await query.answer("Nothing is playing.", show_alert=True)
    try:
        # call PyTgCalls pause in a robust way
        await _safe_call_py_method("pause_stream", chat_id)
        await _safe_call_py_method("pause", chat_id)  # try alternate name
        # record elapsed
        start_time = state.get("start_time") or time.time()
        elapsed = time.time() - start_time if start_time else state.get("elapsed", 0.0)
        state["paused"] = True
        state["elapsed"] = elapsed
        state["start_time"] = None
        radio_paused.add(chat_id)
        store_play_state(chat_id, state.get("station"), state.get("url"), state.get("msg_id"), None, elapsed=elapsed, paused=True)
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
    if not await dlk_privilege_validator(query):
        return await query.answer("Only admins can resume the bot!", show_alert=True)
    chat_id = query.message.chat.id
    state = radio_state.get(chat_id)
    if not state:
        return await query.answer("Nothing to resume.", show_alert=True)
    try:
        # call resume via PyTgCalls robustly
        await _safe_call_py_method("resume_stream", chat_id)
        await _safe_call_py_method("resume", chat_id)
        # restore start_time based on saved elapsed
        elapsed = state.get("elapsed", 0.0) or 0.0
        start_time = time.time() - elapsed
        state["paused"] = False
        state["elapsed"] = 0.0
        state["start_time"] = start_time
        radio_paused.discard(chat_id)
        store_play_state(chat_id, state.get("station"), state.get("url"), state.get("msg_id"), start_time, elapsed=0.0, paused=False)
        # restart timer
        if chat_id in radio_tasks:
            try:
                radio_tasks[chat_id].cancel()
            except Exception:
                pass
            radio_tasks.pop(chat_id, None)
        radio_tasks[chat_id] = asyncio.create_task(update_radio_timer(chat_id, state.get("msg_id"), state.get("station"), start_time))
        try:
            await query.message.edit_reply_markup(reply_markup=player_controls_markup(chat_id))
        except Exception:
            pass
        await query.answer("Resumed.", show_alert=False)
        log_event_sync("radio_resumed", {"chat_id": chat_id, "by": query.from_user.id if query.from_user else None})
    except Exception as e:
        logging.debug(f"Resume failed: {e}")
        await query.answer("Failed to resume the stream.", show_alert=True)

@bot.on_callback_query(filters.regex("^radio_stop$"))
async def cb_radio_stop(_, query: CallbackQuery):
    if not await dlk_privilege_validator(query):
        return await query.answer("Only admins can stop the radio!", show_alert=True)
    chat_id = query.message.chat.id
    try:
        await leave_voice_chat(chat_id)
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.answer("DLK BOT stopped!", show_alert=False)
        log_event_sync("radio_stopped", {"chat_id": chat_id, "by": query.from_user.id if query.from_user else None})
    except Exception as e:
        logging.error(f"Stop failed via callback: {e}", exc_info=True)
        await query.answer("Failed to stop bot.", show_alert=True)

# ====================== RADIO STATION PLAY (button flow) ======================
@bot.on_callback_query(filters.regex("^radio_play_"))
async def play_radio_station(_, query: CallbackQuery):
    station = query.data.replace("radio_play_", "")
    url = RADIO_STATION.get(station)
    chat_id = query.message.chat.id
    user = query.from_user
    if is_group_blocked_sync(chat_id):
        await query.answer("This group is blocked from using DLK BOT.", show_alert=True)
        return
    if not url:
        return await query.answer("Station URL not found!", show_alert=True)
    try:
        if not await ensure_assistant_in_chat(chat_id):
            await query.answer(
                "Assistant is not confirmed in this group yet.",
                show_alert=True,
            )
            return

        # cleanup previous playback
        if chat_id in radio_tasks:
            radio_tasks[chat_id].cancel()
            radio_tasks.pop(chat_id, None)

        if chat_id in track_watchers:
            track_watchers[chat_id].cancel()
            track_watchers.pop(chat_id, None)

        # AUDIO ONLY: explicitly disable the video track.
        await _call_py_method_strict(
            "play",
            chat_id,
            MediaStream(
                            url,
                            AudioQuality.HIGH,
                            video_flags=MediaStream.Flags.IGNORE,
                        ),
        )

        # The radio menu is a TEXT message, not a photo caption.
        msg = query.message
        await _edit_player_message(
            chat_id,
            msg.id,
            f"🎧 Now Playing: {station}\n⏳ Duration: 00:00",
            reply_markup=player_controls_markup(chat_id),
        )

        start_time = time.time()
        store_play_state(
            chat_id,
            station,
            url,
            msg.id,
            start_time,
            elapsed=0.0,
            paused=False,
        )
        radio_tasks[chat_id] = asyncio.create_task(
            update_radio_timer(chat_id, msg.id, station, start_time)
        )
        radio_paused.discard(chat_id)

        await query.answer(
            f"Now playing {station} (audio only).",
            show_alert=False,
        )
        log_event_sync(
            "radio_started",
            {"chat_id": chat_id, "station": station, "by": user.id if user else None},
        )

    except FloodWait as e:
        await leave_voice_chat(chat_id)
        wait_time = getattr(e, "value", None) or getattr(e, "x", None) or "unknown"
        await query.message.reply_text(f"⏳ Rate limit reached! Wait {wait_time} seconds.")
        await query.answer(f"Wait {wait_time}s", show_alert=True)
    except ntgcalls.TelegramServerError:
        await leave_voice_chat(chat_id)
        await query.message.reply_text("❌ Cannot connect to voice chat! Ensure voice chat is active and assistant has permissions.")
        await query.answer("Voice chat not ready!", show_alert=True)
    except RPCError as e:
        await leave_voice_chat(chat_id)
        await query.message.reply_text(f"Failed to play radio! Assistant error: {e}")
    except Exception as e:
        await leave_voice_chat(chat_id)
        logging.error("General radio play error", exc_info=True)
        await query.message.reply_text(f"❌ Failed to start radio! Error: {e}")

# ====================== START / HELP / PANEL (minimal) ======================
@bot.on_message(filters.command(["start"]) & filters.private)
async def start_private(_, message: Message):
    text = (
        "👋 Welcome to DLK BOT!\n\n"
        "Commands (groups):\n"
        "- /radio : stations\n"
        "- /play <query|URL> or reply to audio with /play : play music\n"
        "- /pause /resume /stop /skip : playback controls (admins)\n\n"
        "Owner-only: /bl (block group), /unbl (unblock group)\n"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Home", callback_data="home"), InlineKeyboardButton("❓ Help", callback_data="help_info")],
        [InlineKeyboardButton("📻 Menu", callback_data="radio_page_0")],
        [InlineKeyboardButton("👨‍💻 Dev", url=DEV_LINK), InlineKeyboardButton("💬 Support", url=SUPPORT_LINK)],
    ])
    try:
        await message.reply_text(text, reply_markup=kb)
    except Exception:
        pass

@bot.on_callback_query(filters.regex("^home$"))
async def cb_home(_, query: CallbackQuery):
    try:
        await query.answer()
        text = (
            "👋 DLK BOT Home\n\n"
            "Use the buttons to navigate: Menu shows radio stations. Help explains commands."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📻 Menu", callback_data="radio_page_0"), InlineKeyboardButton("❓ Help", callback_data="help_info")],
            [InlineKeyboardButton("👨‍💻 Dev", url=DEV_LINK), InlineKeyboardButton("💬 Support", url=SUPPORT_LINK)],
        ])
        try:
            await query.message.reply_text(text, reply_markup=kb)
        except Exception:
            # fallback: edit if possible
            try:
                await query.message.edit_text(text, reply_markup=kb)
            except Exception:
                pass
    except Exception:
        pass

@bot.on_callback_query(filters.regex("^assistant_invite_help$"))
async def assistant_invite_help(_, query: CallbackQuery):
    try:
        help_text = (
            "How to add the assistant account:\n\n"
            "1. Open group info -> Administrators -> Add Administrator\n"
            "2. Search for the assistant account username (the bot created a session string).\n"
            "3. Add it and give permission to manage voice chats and speak.\n\n"
            "If you used an invite link, use it to add the assistant and then re-run the command."
        )
        await query.answer()  # just close the loader
        await query.message.reply_text(help_text)
    except Exception:
        pass

@bot.on_callback_query(filters.regex("^help_info$"))
async def cb_help_info(_, query: CallbackQuery):
    try:
        help_text = (
            "DLK BOT help:\n"
            "- Use /play to play YouTube links or search terms.\n"
            "- Reply to an audio/file and use /play to play local audio.\n"
            "- Use /radio to open the radio stations menu.\n"
            "- Use /rpush to add a station or url to the queue.\n"
            "- Use /rskip to skip to next queued station, /rend to end radio, /rresume to resume (admins only).\n"
            "- Admins can use pause/resume/skip/stop via the inline buttons.\n"
            "- Owner-only commands: /bl and /unbl in a group to block/unblock the group.\n"
        )
        await query.answer()
        await query.message.reply_text(help_text)
    except Exception:
        pass

# ====================== NEW: RADIO MENU NAVIGATION HANDLERS ======================
@bot.on_callback_query(filters.regex(r"^radio_page_(\d+)$"))
async def cb_radio_page(_, query: CallbackQuery):
    """
    Handles pagination for the radio stations menu.
    Edits the same message to show the requested page of stations.
    """
    try:
        m = re.match(r"radio_page_(\d+)", query.data)
        if not m:
            return await query.answer()
        page = int(m.group(1))
        kb = radio_buttons(page)
        # Try to edit the message text and markup (original menu uses reply_text with text)
        try:
            await query.message.edit_text("📻 Radio Stations - choose one:", reply_markup=kb)
        except Exception:
            # If editing text not allowed (e.g. message is a photo/caption), try editing the reply markup only
            try:
                await query.message.edit_reply_markup(reply_markup=kb)
            except Exception:
                logging.debug("Could not edit radio menu message for pagination.")
        await query.answer()
    except Exception as e:
        logging.debug(f"radio_page handler failed: {e}")
        try:
            await query.answer("Failed to load page.", show_alert=True)
        except Exception:
            pass

@bot.on_callback_query(filters.regex(r"^radio_close$"))
async def cb_radio_close(_, query: CallbackQuery):
    """
    Closes the radio menu (deletes the message if possible).
    """
    try:
        try:
            await query.message.delete()
        except Exception:
            # fallback: remove inline keyboard
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

# (You may keep admin panel, block/unblock handlers from your original file — omitted here for brevity)

# ====================== RESTORE ON STARTUP & LAUNCH ======================
async def restore_playing_on_start():
    if db is None:
        return
    try:
        for state in db.playing.find({"paused": {"$ne": True}}):
            try:
                chat_id = int(state.get("chat_id"))
                url = state.get("url")
                station = state.get("station") or "Unknown"
                msg_id = state.get("msg_id")
                if chat_id in radio_tasks:
                    continue
                try:
                    await _call_py_method_strict(
                        "play",
                        chat_id,
                        MediaStream(
                            url,
                            AudioQuality.HIGH,
                            video_flags=MediaStream.Flags.IGNORE,
                        ),
                    )
                    start_time = time.time()
                    store_play_state(chat_id, station, url, msg_id or 0, start_time, elapsed=0.0, paused=False)
                    if msg_id:
                        radio_tasks[chat_id] = asyncio.create_task(update_radio_timer(chat_id, msg_id, station, start_time))
                        logging.info(f"Restored playing for chat {chat_id} station {station}")
                except Exception as e:
                    logging.warning(f"Could not auto-restore playing for chat {chat_id}: {e}")
            except Exception:
                continue
    except Exception as e:
        logging.warning(f"Restore playing failed: {e}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.info("Starting DLK Bot...")

    try:
        init_db_sync()
    except Exception as e:
        logger.warning(f"Database initialization failed: {e}")

    assistant.start()
    call_py.start()
    bot.start()

    try:
        me = assistant.get_me()
        ASSISTANT_USERNAME = me.username
        ASSISTANT_ID = me.id
    except Exception:
        ASSISTANT_USERNAME = "assistant"
        ASSISTANT_ID = None

    try:
        bot_me = bot.get_me()
        BOT_USERNAME = bot_me.username
    except Exception:
        BOT_USERNAME = None

    try:
        asyncio.get_event_loop().create_task(restore_playing_on_start())
    except Exception:
        pass

    log_event_sync("bot_started", {"ts": time.time(), "owner": OWNER_ID})

    from pyrogram import idle
    try:
        idle()
    finally:
        try:
            call_py.stop()
            assistant.stop()
            bot.stop()
        except Exception:
            pass   
  
