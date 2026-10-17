from datetime import timedelta
from secrets import SystemRandom
from sqlalchemy import select, func
from app.config import CONFIG
from app.models.orm import Reading, ReadingCard, Card, Job, utcnow
from app.models.dto import POSITIONS, CreateReading
from app.repositories.records import owner, check_generation
from app.services.time_service import local_day
from app.services.safety import guard_question
from app.errors import AppError


def choose_cards(ids, spread):
    if len(ids) != 78 or len(set(ids)) != 78:
        raise ValueError("Deck must contain exactly 78 different cards")
    return list(zip(SystemRandom().sample(ids, len(POSITIONS[spread])), POSITIONS[spread]))


class ReadingService:
    def __init__(self, demo_mode=True):
        self.demo_mode = demo_mode

    async def create_reading(self, session, actor_id, data: CreateReading, now=None):
        now = now or utcnow()
        user = await owner(session, actor_id, lock=True)
        existing = await session.scalar(select(Reading).where(Reading.request_key == data.request_key, Reading.user_id == user.id))
        if existing:
            return existing
        guard_question(data.question + " " + (data.option_a or "") + " " + (data.option_b or ""))
        day = local_day(now, user.timezone)
        start = day if self.demo_mode else day - timedelta(days=day.weekday())
        maximum = CONFIG["quota"]["daily" if self.demo_mode else "weekly"]
        count = await session.scalar(select(func.count()).select_from(Reading).where(Reading.user_id == user.id, Reading.kind == "question", Reading.status != "failed", Reading.local_date >= start, Reading.local_date <= day))
        if count >= maximum:
            reset = day + timedelta(days=1) if self.demo_mode else start + timedelta(days=7)
            raise AppError("QUOTA_EXCEEDED", f"Лимит исчерпан. Новые расклады доступны с {reset:%d.%m.%Y} по твоему часовому поясу.")
        return await self._create(session, user, "question", data.spread_type, day, now, **data.model_dump(exclude={"spread_type"}))

    async def get_or_create_day(self, session, actor_id, now=None):
        now = now or utcnow()
        user = await owner(session, actor_id, lock=True)
        day = local_day(now, user.timezone)
        existing = await session.scalar(select(Reading).where(Reading.user_id == user.id, Reading.kind == "day", Reading.local_date == day))
        if existing:
            return existing
        return await self._create(session, user, "day", "one", day, now, request_key=f"day:{user.id}:{day}")

    async def _create(self, session, user, kind, spread, day, now, **fields):
        if await session.scalar(select(func.count()).select_from(Reading).where(Reading.user_id == user.id)) >= CONFIG["storage_limit"]:
            raise AppError("STORAGE_LIMIT")
        await check_generation(session, user.id)
        ids = list((await session.scalars(select(Card.id).order_by(Card.id))).all())
        selected = choose_cards(ids, spread)
        reading = Reading(user_id=user.id, kind=kind, spread_type=spread, local_date=day, created_at=now, **fields)
        session.add(reading)
        await session.flush()
        for pos, (card_id, label) in enumerate(selected, 1):
            session.add(ReadingCard(reading_id=reading.id, position=pos, card_id=card_id, position_label=label))
        session.add(Job(user_id=user.id, kind="llm_reading", reading_id=reading.id, dedupe_key=f"llm:{reading.id}", created_at=now))
        await session.flush()
        return reading
