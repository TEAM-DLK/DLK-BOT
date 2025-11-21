"""
Plugin: play.py

All shared symbols are imported from DLK.core to ensure every plugin has the
full set of utilities, clients and helpers available (no missing imports).
"""
from DLK.core import *
import logging

logger = logging.getLogger(__name__)


@bot.on_message(filters.group & filters.command(["play", "p"]))
async def cmd_play(_, message: Message):
    """
    /play <query|URL>  OR reply to audio with /play
    Delegates the heavy lifting to helpers available in DLK.core (bot.py re-exports).
    """
    chat_id = message.chat.id
    user = message.from_user
    if is_group_blocked_sync(chat_id):
        return await message.reply_text("❌ This group is blocked from using DLK BOT.")

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
        info_msg = await message.reply_text("🔎 Searching and preparing stream...")
        info = extract_audio_url(query)
        if info is None or not info.get("stream_url"):
            await info_msg.edit_text("❌ Could not extract audio stream. Ensure yt-dlp is installed and cookies.txt set if needed.")
            return
        entry = {
            "title": info.get("title"),
            "stream_url": info.get("stream_url"),
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


@bot.on_message(filters.group & filters.command(["skip", "s"]))
async def cmd_skip(_, message: Message):
    """
    Skip to next queued track or stop if none.
    """
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