import argparse
import asyncio
from datetime import timedelta
from uuid import UUID, uuid4
from sqlalchemy import select
from aiogram import Bot
from app.config import ROOT, Settings
from app.repositories.database import connect
from app.models.orm import User, Reading, ReadingCard, DiaryEntry, utcnow
from app.services.deck_service import seed_deck, startup_check
from app.services.time_service import previous_month, period
from app.services.profile_service import apply_deletions
from app.integrations.llm import LLMAdapter


async def demo(session, actor_id, now=None):
    now = now or utcnow()
    user = await session.scalar(select(User).where(User.telegram_id == actor_id).with_for_update())
    if user is None:
        raise ValueError("Complete /start onboarding before loading demo history")
    month = previous_month(now, user.timezone)
    start, _ = period(month, user.timezone)
    for i, day in enumerate((2, 8, 17, 24)):
        key = f"demo:{user.id}:{month}:{i}"
        if await session.scalar(select(Reading.id).where(Reading.request_key == key)):
            continue
        timestamp = start + timedelta(days=day, hours=12)
        reading = Reading(user_id=user.id, kind="question", spread_type="one", topic="study", question="Демонстрация: как поддержать интерес к учёбе?", local_date=month + timedelta(days=day), status="fallback", result_json={"interpretation": "Демонстрационная запись: замечаю свой прогресс и выбираю посильную задачу.", "reflection_question": "Что помогло мне сегодня?", "memory_refs": []}, request_key=key, created_at=timestamp, completed_at=timestamp, prompt_version="demo")
        session.add(reading)
        await session.flush()
        session.add(ReadingCard(reading_id=reading.id, position=1, card_id=[8, 14, 8, 17][i], position_label="Фокус"))
        session.add(DiaryEntry(user_id=user.id, reading_id=reading.id, text=["Демо: составил план занятий.", "Демо: попросил объяснить сложную тему.", "Демо: заметил небольшой прогресс.", "Демо: нашёл время для отдыха."][i], mood=[3, 3, 4, 5][i], request_key=key + ":note", created_at=timestamp))
    return month


async def main(args):
    settings = Settings()
    if args.command == "smoke":
        settings.validate_runtime()
        bot, adapter = Bot(settings.bot_token.get_secret_value()), LLMAdapter(settings)
        try:
            me = await bot.get_me()
            await adapter.generate("reading", {"question": "На что обратить внимание в учёбе?", "topic": "study", "cards": [{"name": "Умеренность", "meaning": "Постепенность", "label": "Фокус"}], "memory": []})
            print(f"Telegram and LLM OK. Bot: https://t.me/{me.username}")
        finally:
            await bot.session.close()
            await adapter.close()
        return
    engine, sessions = connect(settings.database_url.get_secret_value())
    try:
        if args.command == "check":
            await startup_check(sessions)
            print("Database, migration and deck OK")
        else:
            async with sessions.begin() as session:
                if args.command == "seed":
                    await seed_deck(session)
                    (ROOT / "runtime").mkdir(exist_ok=True)
                    (ROOT / "runtime/deletions.log").touch(exist_ok=True)
                elif args.command == "demo":
                    if not settings.demo_mode:
                        raise ValueError("Demo requires DEMO_MODE=true")
                    month = await demo(session, args.telegram_id)
                    print(f"Demo history: {month:%Y-%m}")
                elif args.command == "apply-deletions":
                    await apply_deletions(session, ROOT / "runtime/deletions.log")
            print("OK")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("seed", "check", "smoke", "apply-deletions"):
        sub.add_parser(name)
    demo_parser = sub.add_parser("demo")
    demo_parser.add_argument("telegram_id", type=int)
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as exc:
        print(f"Command failed: {type(exc).__name__}. Check README and configuration.")
        raise SystemExit(1) from None
