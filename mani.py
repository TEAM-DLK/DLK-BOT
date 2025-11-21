```python
"""
main.py

Application entrypoint that loads the core bot (bot.py) and dynamically imports
all plugins placed in the DLK/plugins package.

Usage:
    python main.py

Notes:
- This loader expects the original bot.py to expose the main Client objects and
  helper functions (bot, assistant, call_py, init_db_sync, restore_playing_on_start, log_event_sync, OWNER_ID, etc).
- Place each command/callback handler in a separate file under DLK/plugins/.
- Importing the plugin modules registers handlers on the `bot` and `assistant` objects
  because plugin files use the `@bot.on_message` / `@bot.on_callback_query` decorators.
"""

import logging
import pkgutil
import importlib
import asyncio

# import the bot core (the existing file in the repository)
# bot.py should remain as a utilities/core file (no __main__ startup run).
import bot  # noqa: E402

logger = logging.getLogger(__name__)


def load_plugins(package_name: str = "DLK.plugins"):
    """
    Dynamically import all modules from the given package. Each module can
    register handlers on import.
    """
    logger.info("Loading plugins from %s", package_name)
    try:
        package = importlib.import_module(package_name)
    except ModuleNotFoundError:
        logger.warning("Package %s not found. No plugins loaded.", package_name)
        return []

    loaded = []
    for finder, name, ispkg in pkgutil.iter_modules(package.__path__):
        full_name = f"{package_name}.{name}"
        try:
            importlib.import_module(full_name)
            loaded.append(full_name)
            logger.info("Loaded plugin: %s", full_name)
        except Exception as e:
            logger.exception("Failed to load plugin %s: %s", full_name, e)
    return loaded


async def async_main():
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting DLK main...")

    # initialize DB (if configured)
    try:
        bot.init_db_sync()
    except Exception as e:
        logger.warning("DB init failed: %s", e)

    # load plugins (they register handlers on import)
    load_plugins("DLK.plugins")

    # start clients
    logger.info("Starting assistant, call_py and bot clients...")
    try:
        bot.assistant.start()
        bot.call_py.start()
        bot.bot.start()
    except Exception as e:
        logger.exception("Failed to start clients: %s", e)
        raise

    # set assistant and bot metadata if available (best-effort)
    try:
        me = bot.assistant.get_me()
        bot.ASSISTANT_USERNAME = getattr(me, "username", None)
        bot.ASSISTANT_ID = getattr(me, "id", None)
    except Exception:
        bot.ASSISTANT_USERNAME = bot.ASSISTANT_USERNAME or "assistant"
        bot.ASSISTANT_ID = bot.ASSISTANT_ID or None

    try:
        me2 = bot.bot.get_me()
        bot.BOT_USERNAME = getattr(me2, "username", None)
    except Exception:
        bot.BOT_USERNAME = bot.BOT_USERNAME or None

    # restore playing states (if DB configured)
    try:
        asyncio.get_event_loop().create_task(bot.restore_playing_on_start())
    except Exception:
        logger.debug("Could not schedule restore_playing_on_start")

    bot.log_event_sync("bot_started", {"ts": bot.time.time() if hasattr(bot, "time") else None, "owner": bot.OWNER_ID})

    # keep running
    from pyrogram import idle
    try:
        idle()
    finally:
        try:
            logger.info("Stopping clients...")
            bot.call_py.stop()
            bot.assistant.stop()
            bot.bot.stop()
        except Exception:
            logger.exception("Error while stopping clients.")


if __name__ == "__main__":
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("Shutting down due to KeyboardInterrupt")
