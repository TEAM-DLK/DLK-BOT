"""
Plugin: startup.py

Sends a startup notification to the bot OWNER_ID when the bot comes online.
The plugin appends an async callable to DLK.core.startup_tasks so main.py
schedules it after the clients are started.

Behavior:
- Waits a short delay (2 seconds) to allow clients to fully initialize.
- Attempts to send a message to OWNER_ID with basic info (bot username,
  assistant username, host, start time).
- Logs an event via log_event_sync.
"""
from DLK.core import bot, OWNER_ID, BOT_USERNAME, ASSISTANT_USERNAME, log_event_sync, startup_tasks
import asyncio
import platform
import time
import logging

logger = logging.getLogger(__name__)


async def _notify_owner_startup():
    # small delay to let clients warm up
    await asyncio.sleep(2)

    if not OWNER_ID:
        logger.warning("OWNER_ID not set; cannot send startup message.")
        return

    try:
        host = platform.node() or "unknown-host"
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        bot_u = f"@{BOT_USERNAME}" if BOT_USERNAME else "unknown"
        assist_u = f"@{ASSISTANT_USERNAME}" if ASSISTANT_USERNAME else "assistant (unknown)"
        text = (
            "✅ DLK Bot started\n\n"
            f"Bot: {bot_u}\n"
            f"Assistant: {assist_u}\n"
            f"Host: {host}\n"
            f"UTC Start: {now}\n"
        )
        # send message (best-effort)
        try:
            await bot.send_message(OWNER_ID, text, disable_web_page_preview=True)
            logger.info("Startup message sent to owner %s", OWNER_ID)
        except Exception as e:
            logger.exception("Failed to send startup message to owner: %s", e)
        # log event to DB/channel if configured
        try:
            log_event_sync("owner_notified_start", {"owner": OWNER_ID, "host": host, "bot": BOT_USERNAME, "assistant": ASSISTANT_USERNAME})
        except Exception:
            pass
    except Exception as e:
        logger.exception("Unexpected error in startup notification: %s", e)


# Register the startup task so main.py will schedule it after starting clients
startup_tasks.append(_notify_owner_startup)