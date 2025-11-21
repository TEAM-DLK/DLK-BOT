```python
"""
Plugin: radio.py

Contains radio station menu handlers, radio playback callbacks, and radio-specific commands.
"""

import re
import asyncio
import logging
from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery

from bot import (
    bot,
    assistant,
    call_py,
    RADIO_STATION,
    radio_buttons,
    player_controls_markup,
    radio_queue,
    radio_tasks,
    radio_state,
    radio_paused,
    track_watchers,
    log_event_sync,
    is_group_blocked_sync,
    dlk_privilege_validator,
    leave_voice_chat,
    _safe_call_py_method,
)
from pytgcalls.types import MediaStream
from pyrogram.errors import RPCError, FloodWait

# ntgcalls is optional in original file; import if used
try:
    import ntgcalls
except Exception:
    ntgcalls = None

logger = logging.getLogger(__name__)


@bot.on_message(filters.group & filters.command(["radio"]))
async def cmd_radio_menu(_, message):
    chat_id = message.chat.id
    if is_group_blocked_sync(chat_id):
        return await message.reply_text("â This group is blocked from using DLK BOT.")
    kb = radio_buttons(0)
    try:
        await message.reply_text("ð» Radio Stations - choose one:", reply_markup=kb)
    except Exception:
        await message.reply_text("Failed to show radio menu.")


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
        # Ensure assistant is in group - bot.py contains more complete logic, but we attempt to play only
        # cleanup previous
        if chat_id in radio_tasks:
            radio_tasks[chat_id].cancel()
            radio_tasks.pop(chat_id, None)

        await asyncio.sleep(1)
        await _safe_call_py_method("play", chat_id, MediaStream(url))

        msg = await query.message.edit_caption(
            caption=f"ð§ Connecting to {station}...",
            reply_markup=player_controls_markup(chat_id)
        )

        start_time = asyncio.get_event_loop().time() if hasattr(asyncio.get_event_loop(), "time") else __import__("time").time()
        store_time = __import__("time").time()
        # store state (lightweight) - prefer bot.store_play_state if available
        try:
            # try to use helper if available in bot module
            from bot import store_play_state
            store_play_state(chat_id, station, url, msg.id, store_time, elapsed=0.0, paused=False)
        except Exception:
            radio_state[chat_id] = {"chat_id": chat_id, "station": station, "url": url, "msg_id": msg.id, "start_time": store_time, "paused": False}

        radio_tasks[chat_id] = asyncio.create_task(bot.update_radio_timer(chat_id, msg.id, station, store_time))
        radio_paused.discard(chat_id)
        await query.answer(f"Now playing {station} via assistant!", show_alert=False)
        log_event_sync("radio_started", {"chat_id": chat_id, "station": station, "by": user.id if user else None})
    except FloodWait as e:
        await leave_voice_chat(chat_id)
        wait_time = getattr(e, "value", None) or getattr(e, "x", None) or "unknown"
        await query.message.reply_text(f"â³ Rate limit reached! Wait {wait_time} seconds.")
        await query.answer(f"Wait {wait_time}s", show_alert=True)
    except ntgcalls and ntgcalls.TelegramServerError:  # type: ignore
        await leave_voice_chat(chat_id)
        await query.message.reply_text("â Cannot connect to voice chat! Ensure voice chat is active and assistant has permissions.")
        await query.answer("Voice chat not ready!", show_alert=True)
    except RPCError as e:
        await leave_voice_chat(chat_id)
        await query.message.reply_text(f"Failed to play radio! Assistant error: {e}")
    except Exception as e:
        await leave_voice_chat(chat_id)
        logger.exception("General radio play error")
        await query.message.reply_text(f"â Failed to start radio! Error: {e}")


@bot.on_callback_query(filters.regex(r"^radio_page_(\d+)$"))
async def cb_radio_page(_, query: CallbackQuery):
    try:
        m = re.match(r"radio_page_(\d+)", query.data)
        if not m:
            return await query.answer()
        page = int(m.group(1))
        kb = radio_buttons(page)
        try:
            await query.message.edit_text("ð» Radio Stations - choose one:", reply_markup=kb)
        except Exception:
            try:
                await query.message.edit_reply_markup(reply_markup=kb)
            except Exception:
                logger.debug("Could not edit radio menu message for pagination.")
        await query.answer()
    except Exception as e:
        logger.debug(f"radio_page handler failed: {e}")
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
        logger.debug(f"radio_close handler failed: {e}")
        try:
            await query.answer("Failed to close menu.", show_alert=True)
        except Exception:
            pass
