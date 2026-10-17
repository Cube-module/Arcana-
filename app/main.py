import asyncio
import logging
from logging.handlers import TimedRotatingFileHandler
from aiogram import Bot
from app.config import Settings, CONFIG, ROOT
from app.repositories.database import connect
from app.integrations.llm import LLMAdapter
from app.workers.updates import poll, update_loop
from app.workers.generation import generation_loop
from app.services.delivery_service import DeliveryService
from app.services.scheduler_service import SchedulerService
from app.services.deck_service import startup_check


async def main():
    settings = Settings()
    settings.validate_runtime()
    runtime = ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / "deletions.log").touch(exist_ok=True)
    handler = TimedRotatingFileHandler(runtime / "events.log", when="midnight", backupCount=14, encoding="utf-8", utc=True)
    logging.getLogger("arcana").addHandler(handler)
    logging.getLogger("arcana").setLevel(logging.INFO)
    # Third-party HTTP logs can include tokens in Telegram URLs.
    for name in ("httpx", "httpcore", "aiogram", "sqlalchemy", "aiohttp"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    engine, sessions = connect(settings.database_url.get_secret_value())
    bot, adapter = Bot(settings.bot_token.get_secret_value()), LLMAdapter(settings)
    try:
        await startup_check(sessions)
        await bot.get_me()
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(poll(sessions, bot))
            tasks.create_task(update_loop(sessions, bot, settings.demo_mode))
            for _ in range(CONFIG["llm"]["concurrency"]):
                tasks.create_task(generation_loop(sessions, adapter))
            tasks.create_task(DeliveryService(sessions, bot).run())
            tasks.create_task(SchedulerService(sessions, settings.demo_mode).run())
    finally:
        await adapter.close()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # No exception strings: network errors may embed secrets or request text.
        print(f"Startup failed ({type(exc).__name__}). Check environment, migration 0001 and deck; see README.")
        raise SystemExit(1) from None
