"""
Plugin: radio.py

All shared symbols are imported from DLK.core so the plugin has the full set of
helpers/clients/constants available (no missing imports).
"""
from DLK.core import *
import logging

logger = logging.getLogger(__name__)


@bot.on_message(filters.group & filters.command(["radio"]))
async def cmd_radio_menu(_, message: Message):
    chat_id = message.chat.id
    if is_group_blocked_sync(chat_id):
        return await message.reply_text("❌ This group is blocked from using DLK BOT.")
    kb = radio_buttons(0)
    try:
        await message.reply_text("📻 Radio Stations - choose one:", reply_markup=kb)
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
        # cleanup previous
        if chat_id in radio_tasks:
            radio_tasks[chat_id].cancel()
            radio_tasks.pop(chat_id, None)

        await asyncio.sleep(1)
        await _safe_call_py_method("play", chat_id, MediaStream(url))

        msg = await query.message.edit_caption(
            caption=f"🎧 Connecting to {station}...",
            reply_markup=player_controls_markup(chat_id)
        )

        start_time = time.time()
        store_play_state(chat_id, station, url, msg.id, start_time, elapsed=0.0, paused=False)
        radio_tasks[chat_id] = asyncio.create_task(update_radio_timer(chat_id, msg.id, station, start_time))
        radio_paused.discard(chat_id)
        await query.answer(f"Now playing {station} via assistant!", show_alert=False)
        log_event_sync("radio_started", {"chat_id": chat_id, "station": station, "by": user.id if user else None})
    except FloodWait as e:
        await leave_voice_chat(chat_id)
        wait_time = getattr(e, "value", None) or getattr(e, "x", None) or "unknown"
        await query.message.reply_text(f"⏳ Rate limit reached! Wait {wait_time} seconds.")
        await query.answer(f"Wait {wait_time}s", show_alert=True)
    except Exception as e:
        await leave_voice_chat(chat_id)
        logger.exception("General radio play error")
        await query.message.reply_text(f"❌ Failed to start radio! Error: {e}")


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