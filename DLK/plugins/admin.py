"""
Plugin: admin.py

All shared symbols are imported from DLK.core so admin commands and owner panel
have full access to DB, logging and utilities (no missing imports).
"""
from DLK.core import *
import logging

logger = logging.getLogger(__name__)


@bot.on_message(filters.group & filters.command(["bl", "block"]))
async def cmd_block_group(_, message: Message):
    if not message.from_user or message.from_user.id != OWNER_ID:
        return await message.reply_text("Only the bot owner can block this group.")
    chat_id = message.chat.id
    try:
        block_group_sync(chat_id, message.from_user.id, reason="blocked by owner via /bl")
        await message.reply_text("✅ This group has been blocked from using DLK BOT.")
        log_event_sync("group_blocked", {"chat_id": chat_id, "by": message.from_user.id})
    except Exception as e:
        logger.exception("Failed to block group %s", chat_id)
        await message.reply_text("Failed to block the group.")


@bot.on_message(filters.group & filters.command(["unbl", "unblock"]))
async def cmd_unblock_group(_, message: Message):
    if not message.from_user or message.from_user.id != OWNER_ID:
        return await message.reply_text("Only the bot owner can unblock this group.")
    chat_id = message.chat.id
    try:
        unblock_group_sync(chat_id)
        await message.reply_text("✅ This group has been unblocked.")
        log_event_sync("group_unblocked", {"chat_id": chat_id, "by": message.from_user.id})
    except Exception as e:
        logger.exception("Failed to unblock group %s", chat_id)
        await message.reply_text("Failed to unblock the group.")


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
        logger.exception("Failed to fetch blocked list.")
        await message.reply_text("Failed to fetch blocked list.")


@bot.on_message(filters.private & filters.command(["start"]))
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
        ])
        try:
            await query.message.reply_text(text, reply_markup=kb)
        except Exception:
            try:
                await query.message.edit_text(text, reply_markup=kb)
            except Exception:
                pass
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