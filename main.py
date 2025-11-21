"""
main.py

Launcher that:
- imports the core bot module (bot.py)
- imports DLK.core (re-export shim)
- dynamically loads all modules in DLK.plugins (so every plugin gets the full re-exported namespace)
- starts assistant, PyTgCalls and bot clients
- runs plugin startup tasks appended to DLK.core.startup_tasks
- schedules restore_playing_on_start (if DB enabled)
- keeps process alive with pyrogram.idle() and gracefully stops clients on exit
"""
import logging
import pkgutil
import importlib
import asyncio
import sys
from typing import List

# Import the main bot implementation first so DLK.core and plugins can access
# the already-loaded objects (avoids circular import issues).
import bot  # the big file you pasted earlier (bot.py)

logger = logging.getLogger(__name__)


def load_core():
    """
    Import DLK.core so plugins can "from DLK.core import *".
    """
    try:
        import DLK.core  # noqa: F401
        logger.info("Imported DLK.core")
    except ModuleNotFoundError:
        logger.warning("DLK.core not found. Make sure DLK/core.py exists.")


def load_plugins(package_name: str = "DLK.plugins") -> List[str]:
    loaded = []
    try:
        package = importlib.import_module(package_name)
    except ModuleNotFoundError:
        logger.warning("Plugins package %s not found. Skipping plugin load.", package_name)
        return loaded

    for finder, name, ispkg in pkgutil.iter_modules(package.__path__):
        full_name = f"{package_name}.{name}"
        try:
            importlib.import_module(full_name)
            loaded.append(full_name)
            logger.info("Loaded plugin: %s", full_name)
        except Exception as e:
            logger.exception("Failed to load plugin %s: %s", full_name, e)
    return loaded


def start_clients():
    """
    Start assistant, PyTgCalls and bot clients.
    """
    try:
        logger.info("Starting assistant client...")
        bot.assistant.start()
    except Exception as e:
        logger.exception("Failed to start assistant: %s", e)
        raise

    try:
        logger.info("Starting call_py (PyTgCalls)...")
        bot.call_py.start()
    except Exception as e:
        logger.exception("Failed to start call_py: %s", e)
        raise

    try:
        logger.info("Starting bot client...")
        bot.bot.start()
    except Exception as e:
        logger.exception("Failed to start bot: %s", e)
        raise

    # best-effort: fill assistant/bot metadata
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


def stop_clients():
    """
    Try to stop clients gracefully.
    """
    try:
        logger.info("Stopping call_py...")
        bot.call_py.stop()
    except Exception:
        logger.exception("Error stopping call_py")

    try:
        logger.info("Stopping assistant...")
        bot.assistant.stop()
    except Exception:
        logger.exception("Error stopping assistant")

    try:
        logger.info("Stopping bot...")
        bot.bot.stop()
    except Exception:
        logger.exception("Error stopping bot")


async def run_startup_tasks():
    """
    Run async start-up tasks appended to DLK.core.startup_tasks by plugins.
    Each entry may be:
      - an async function (coroutine function) -> call and create_task
      - a callable that returns a coroutine -> call and create_task
    Tasks are scheduled (not awaited) so the main idle loop continues.
    """
    try:
        import DLK.core as core
    except Exception:
        logger.debug("DLK.core not importable; no startup tasks to run.")
        return

    tasks = getattr(core, "startup_tasks", [])
    if not tasks:
        logger.debug("No startup tasks registered.")
        return

    for idx, t in enumerate(tasks):
        try:
            if asyncio.iscoroutinefunction(t):
                asyncio.create_task(t(), name=f"startup-task-{idx}")
                logger.info("Scheduled startup coroutine function %s", getattr(t, "__name__", str(t)))
            elif callable(t):
                res = t()
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res, name=f"startup-task-{idx}")
                    logger.info("Scheduled startup coroutine returned by %s", getattr(t, "__name__", str(t)))
                else:
                    logger.debug("Startup callable %s returned non-coroutine; ignored.", getattr(t, "__name__", str(t)))
            else:
                logger.debug("Startup entry %s is not callable; ignored.", str(t))
        except Exception as e:
            logger.exception("Failed to schedule startup task %s: %s", str(t), e)


def main():
    logging.basicConfig(level=logging.INFO)
    logger.info("DLK main starting...")

    # initialize DB (if configured)
    try:
        bot.init_db_sync()
    except Exception as e:
        logger.warning("DB init failed: %s", e)

    # import core re-exports
    load_core()

    # load plugins (these should import from DLK.core)
    loaded = load_plugins("DLK.plugins")
    logger.info("Plugins loaded: %s", loaded)

    # start clients (assistant, call_py, bot)
    try:
        start_clients()
    except Exception:
        logger.error("Failed to start clients; aborting.")
        stop_clients()
        sys.exit(1)

    # schedule restore_playing_on_start if available
    try:
        loop = asyncio.get_event_loop()
        if bot.restore_playing_on_start is not None:
            try:
                loop.create_task(bot.restore_playing_on_start())
                logger.info("Scheduled restore_playing_on_start()")
            except Exception as e:
                logger.warning("Could not schedule restore_playing_on_start: %s", e)
    except Exception as e:
        logger.debug("Event loop scheduling issue: %s", e)

    # schedule plugin startup tasks
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(run_startup_tasks())
        logger.info("Scheduled plugin startup tasks runner.")
    except Exception as e:
        logger.warning("Could not schedule startup tasks runner: %s", e)

    # log start
    try:
        bot.log_event_sync("bot_started", {"ts": bot.time.time(), "owner": bot.OWNER_ID})
    except Exception:
        try:
            bot.log_event_sync("bot_started", {"ts": time.time(), "owner": bot.OWNER_ID})
        except Exception:
            pass

    # keep alive with pyrogram.idle()
    from pyrogram import idle
    try:
        logger.info("Entering idle() - bot is now running.")
        idle()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down.")
    finally:
        stop_clients()
        logger.info("DLK main stopped.")


if __name__ == "__main__":
    main()