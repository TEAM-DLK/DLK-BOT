"""
DLK/core.py

Re-export shim for bot.py plus a startup_tasks list that plugins can append
startup routines to. Plugins import from DLK.core and append async callables
(or functions that return coroutines) to `startup_tasks`. main.py will run
those tasks after starting the clients.
"""
# Import the bot module (the main core file)
import bot as _bot

# re-export frequently used stdlib / modules
os = _bot.os
re = _bot.re
time = _bot.time
asyncio = _bot.asyncio
logging = _bot.logging
random = _bot.random
inspect = _bot.inspect

# typing helpers (bot.py imported them into its namespace)
Union = _bot.Union
Optional = _bot.Optional
Dict = _bot.Dict
Any = _bot.Any
List = _bot.List

# url helpers
urlparse = _bot.urlparse
parse_qs = _bot.parse_qs

# pyrogram / types / errors
Client = _bot.Client
filters = _bot.filters
Message = _bot.Message
CallbackQuery = _bot.CallbackQuery
InlineKeyboardButton = _bot.InlineKeyboardButton
InlineKeyboardMarkup = _bot.InlineKeyboardMarkup
RPCError = _bot.RPCError
FloodWait = _bot.FloodWait

# GroupcallForbidden shim (may be replaced by bot.py)
try:
    GroupcallForbidden = _bot.GroupcallForbidden
except Exception:
    GroupcallForbidden = None

# pytgcalls
PyTgCalls = _bot.PyTgCalls
MediaStream = _bot.MediaStream

# dotenv (already loaded in bot.py)
load_dotenv = _bot.load_dotenv

# optional external libs (yt-dlp / youtube search / mongo / ntgcalls / aio.. / PIL)
youtube_dl = getattr(_bot, "youtube_dl", None)
aiohttp = getattr(_bot, "aiohttp", None)
aiofiles = getattr(_bot, "aiofiles", None)
Image = getattr(_bot, "Image", None)
ImageDraw = getattr(_bot, "ImageDraw", None)
ImageEnhance = getattr(_bot, "ImageEnhance", None)
ImageFilter = getattr(_bot, "ImageFilter", None)
ImageFont = getattr(_bot, "ImageFont", None)
ImageOps = getattr(_bot, "ImageOps", None)

VIDEOS_SEARCH_AVAILABLE = getattr(_bot, "VIDEOS_SEARCH_AVAILABLE", False)
VideosSearch = getattr(_bot, "VideosSearch", None)

MongoClient = getattr(_bot, "MongoClient", None)
ntgcalls = getattr(_bot, "ntgcalls", None)

# ====================== CONFIG / CONSTANTS (exported) ======================
API_ID = getattr(_bot, "API_ID", None)
API_HASH = getattr(_bot, "API_HASH", None)
BOT_TOKEN = getattr(_bot, "BOT_TOKEN", None)
ASSISTANT_SESSION = getattr(_bot, "ASSISTANT_SESSION", None)
OWNER_ID = getattr(_bot, "OWNER_ID", None)

MONGO_URI = getattr(_bot, "MONGO_URI", None)
MONGO_DBNAME = getattr(_bot, "MONGO_DBNAME", None)
LOG_CHANNEL_ID = getattr(_bot, "LOG_CHANNEL_ID", None)

YT_DLP_COOKIES = getattr(_bot, "YT_DLP_COOKIES", None)

DEV_LINK = getattr(_bot, "DEV_LINK", None)
SUPPORT_LINK = getattr(_bot, "SUPPORT_LINK", None)

THUMB_CACHE_DIR = getattr(_bot, "THUMB_CACHE_DIR", None)
DOWNLOADS_DIR = getattr(_bot, "DOWNLOADS_DIR", None)

# Radio station list
RADIO_STATION = getattr(_bot, "RADIO_STATION", {})

# ====================== GLOBALS (shared state) ======================
radio_tasks = getattr(_bot, "radio_tasks", {})
radio_paused = getattr(_bot, "radio_paused", set())
radio_state = getattr(_bot, "radio_state", {})
radio_queue = getattr(_bot, "radio_queue", {})
track_watchers = getattr(_bot, "track_watchers", {})
bot_start_time = getattr(_bot, "bot_start_time", None)

BOT_USERNAME = getattr(_bot, "BOT_USERNAME", None)
ASSISTANT_USERNAME = getattr(_bot, "ASSISTANT_USERNAME", None)
ASSISTANT_ID = getattr(_bot, "ASSISTANT_ID", None)

# ====================== CLIENTS ======================
# These are the pyrogram clients and the PyTgCalls instance from bot.py
bot = getattr(_bot, "bot", None)
assistant = getattr(_bot, "assistant", None)
call_py = getattr(_bot, "call_py", None)

db_client = getattr(_bot, "db_client", None)
db = getattr(_bot, "db", None)

# ====================== UTILS / HELPERS (re-export) ======================
looks_like_url = getattr(_bot, "looks_like_url", None)
get_youtube_id = getattr(_bot, "get_youtube_id", None)
extract_audio_url = getattr(_bot, "extract_audio_url", None)

changeImageSize = getattr(_bot, "changeImageSize", None)
clear_title = getattr(_bot, "clear_title", None)
_download_file = getattr(_bot, "_download_file", None)
_create_circular_artwork = getattr(_bot, "_create_circular_artwork", None)
_process_image_and_overlay = getattr(_bot, "_process_image_and_overlay", None)
get_thumb_from_url_or_webpage = getattr(_bot, "get_thumb_from_url_or_webpage", None)

init_db_sync = getattr(_bot, "init_db_sync", None)
save_play_state_db = getattr(_bot, "save_play_state_db", None)
remove_play_state_db = getattr(_bot, "remove_play_state_db", None)

_valid_log_target = getattr(_bot, "_valid_log_target", None)
log_event_sync = getattr(_bot, "log_event_sync", None)

is_group_blocked_sync = getattr(_bot, "is_group_blocked_sync", None)
block_group_sync = getattr(_bot, "block_group_sync", None)
unblock_group_sync = getattr(_bot, "unblock_group_sync", None)

dlk_privilege_validator = getattr(_bot, "dlk_privilege_validator", None)

radio_buttons = getattr(_bot, "radio_buttons", None)
player_controls_markup = getattr(_bot, "player_controls_markup", None)

update_radio_timer = getattr(_bot, "update_radio_timer", None)
leave_voice_chat = getattr(_bot, "leave_voice_chat", None)
store_play_state = getattr(_bot, "store_play_state", None)

_safe_call_py_method = getattr(_bot, "_safe_call_py_method", None)

prepare_entry_from_reply = getattr(_bot, "prepare_entry_from_reply", None)
play_entry = getattr(_bot, "play_entry", None)
track_watcher = getattr(_bot, "track_watcher", None)

restore_playing_on_start = getattr(_bot, "restore_playing_on_start", None)

# ====================== STARTUP TASKS ======================
# Plugins can append async callables (or callables returning coroutines)
# to this list. main.py will invoke them after clients are started.
startup_tasks: List = []

# __all__ makes it convenient to import *
__all__ = [
    # stdlib
    "os", "re", "time", "asyncio", "logging", "random", "inspect",
    "Union", "Optional", "Dict", "Any", "List", "urlparse", "parse_qs",
    # pyrogram
    "Client", "filters", "Message", "CallbackQuery", "InlineKeyboardButton", "InlineKeyboardMarkup",
    "RPCError", "FloodWait", "GroupcallForbidden",
    # pytgcalls
    "PyTgCalls", "MediaStream",
    # libs
    "youtube_dl", "aiohttp", "aiofiles", "Image", "ImageDraw", "ImageEnhance", "ImageFilter", "ImageFont", "ImageOps",
    "VIDEOS_SEARCH_AVAILABLE", "VideosSearch", "MongoClient", "ntgcalls",
    # config
    "API_ID", "API_HASH", "BOT_TOKEN", "ASSISTANT_SESSION", "OWNER_ID",
    "MONGO_URI", "MONGO_DBNAME", "LOG_CHANNEL_ID", "YT_DLP_COOKIES",
    "DEV_LINK", "SUPPORT_LINK", "THUMB_CACHE_DIR", "DOWNLOADS_DIR",
    "RADIO_STATION",
    # shared state & clients
    "radio_tasks", "radio_paused", "radio_state", "radio_queue", "track_watchers", "bot_start_time",
    "BOT_USERNAME", "ASSISTANT_USERNAME", "ASSISTANT_ID",
    "bot", "assistant", "call_py", "db_client", "db",
    # helpers
    "looks_like_url", "get_youtube_id", "extract_audio_url",
    "changeImageSize", "clear_title", "_download_file", "_create_circular_artwork", "_process_image_and_overlay",
    "get_thumb_from_url_or_webpage",
    "init_db_sync", "save_play_state_db", "remove_play_state_db", "_valid_log_target", "log_event_sync",
    "is_group_blocked_sync", "block_group_sync", "unblock_group_sync", "dlk_privilege_validator",
    "radio_buttons", "player_controls_markup", "update_radio_timer", "leave_voice_chat", "store_play_state",
    "_safe_call_py_method", "prepare_entry_from_reply", "play_entry", "track_watcher", "restore_playing_on_start",
    # startup tasks
    "startup_tasks",
]