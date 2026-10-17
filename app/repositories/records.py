from datetime import datetime, timedelta
from uuid import UUID
from sqlalchemy import select, func, tuple_, text
from app.models.orm import User, Reading, ReadingCard, Card, DiaryEntry, MonthlyReport, Job, utcnow
from app.errors import AppError
from app.config import CONFIG


async def actor_profile(session, actor_id):
    return await session.scalar(select(User).where(User.telegram_id == actor_id).with_for_update())


async def by_request(session, model, user_id, request_key):
    return await session.scalar(select(model).where(model.user_id == user_id, model.request_key == request_key))


async def bind_updates(session, actor_id, user_id):
    from sqlalchemy import update
    await session.execute(update(Job).where(Job.kind == "update", Job.user_id.is_(None), Job.payload["actor_id"].astext == str(actor_id)).values(user_id=user_id))


async def initial_delivery_pending(session, record_id):
    return await session.scalar(select(Job.id).where(Job.dedupe_key == f"result:{record_id}", Job.state.in_(["pending", "running"])))


async def owner(session, actor_id, lock=False):
    query = select(User).where(User.telegram_id == actor_id)
    if lock:
        query = query.with_for_update()
    user = await session.scalar(query)
    if user is None:
        raise AppError("CONSENT_REQUIRED")
    if user.fsm_state and user.fsm_state.startswith("onboard"):
        raise AppError("CONSENT_REQUIRED")
    return user


async def owned(session, model, user_id, object_id):
    try:
        object_id = UUID(str(object_id))
    except ValueError:
        raise AppError("NOT_FOUND") from None
    row = await session.scalar(select(model).where(model.id == object_id, model.user_id == user_id))
    if row is None:
        raise AppError("NOT_FOUND")
    return row


async def cards_for(session, reading_id):
    rows = (await session.execute(select(ReadingCard, Card).join(Card, Card.id == ReadingCard.card_id).where(ReadingCard.reading_id == reading_id).order_by(ReadingCard.position))).all()
    return [dict(id=c.id, name=c.name, meaning=c.meaning, image_path=c.image_path, telegram_file_id=c.telegram_file_id, position=rc.position, label=rc.position_label) for rc, c in rows]


async def check_generation(session, user_id):
    # Serializes admission, including the global queue count, across short transactions.
    await session.execute(text("SELECT pg_advisory_xact_lock(726001)"))
    active = (Job.kind.in_(["llm_reading", "llm_report"]), Job.state.in_(["pending", "running"]))
    if await session.scalar(select(func.count()).select_from(Job).where(*active, Job.user_id == user_id)):
        raise AppError("BUSY")
    if await session.scalar(select(func.count()).select_from(Job).where(*active)) >= CONFIG["llm"]["queue_limit"]:
        raise AppError("QUEUE_FULL")


async def history(session, user_id, model=Reading, cursor=None, limit=10, kind=None, bounds=None):
    if type(limit) is not int or not 1 <= limit <= 10:
        raise AppError("VALIDATION_ERROR")
    query = select(model).where(model.user_id == user_id)
    if bounds:
        query = query.where(model.created_at >= bounds[0], model.created_at < bounds[1])
    if kind is not None and model is Reading:
        if kind not in ("question", "day"):
            raise AppError("VALIDATION_ERROR")
        query = query.where(Reading.kind == kind)
    if cursor:
        # A short UUID in Telegram resolves both cursor boundaries under ownership check.
        boundary = await owned(session, model, user_id, cursor)
        query = query.where(tuple_(model.created_at, model.id) < tuple_(boundary.created_at, boundary.id))
    rows = list((await session.scalars(query.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1))).all())
    return rows[:limit], str(rows[limit - 1].id) if len(rows) > limit else None
