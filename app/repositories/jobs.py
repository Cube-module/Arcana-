from datetime import timedelta
from sqlalchemy import select, or_, and_, update, delete, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased
from app.config import CONFIG
from app.models.orm import Job, User, utcnow


async def enqueue(session, **values):
    await session.execute(insert(Job).values(**values).on_conflict_do_nothing(index_elements=[Job.dedupe_key]))


async def claim(sessions, kinds):
    async with sessions.begin() as session:
        now = utcnow()
        query = select(Job).where(Job.kind.in_(kinds), or_(and_(Job.state == "pending", Job.available_at <= now), and_(Job.state == "running", Job.locked_until < now)))
        if kinds == ["update"]:
            earlier = aliased(Job)
            # A pending event must not overtake an older event with an unexpired lease.
            query = query.where(~select(earlier.id).where(earlier.kind == "update", earlier.state.in_(["pending", "running"]), earlier.payload["actor_id"].astext == Job.payload["actor_id"].astext, or_(earlier.created_at < Job.created_at, and_(earlier.created_at == Job.created_at, earlier.id < Job.id))).exists())
        job = await session.scalar(query.order_by(Job.created_at, Job.id).with_for_update(skip_locked=True).limit(1))
        if not job:
            return None
        job.state = "running"
        job.attempts += 1
        job.locked_until = now + timedelta(seconds=CONFIG["lease"])
        await session.flush()
        return job


async def leased(session, job, lock=True):
    query = select(Job).where(Job.id == job.id, Job.state == "running", Job.attempts == job.attempts, Job.locked_until > utcnow())
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


async def ingest(sessions, events):
    async with sessions.begin() as session:
        for event in events:
            await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": event["actor_id"]})
            user = await session.scalar(select(User).where(User.telegram_id == event["actor_id"]))
            await enqueue(session, user_id=user.id if user else None, kind="update", dedupe_key=f"update:{event['update_id']}", payload=event)


async def cleanup(session):
    now = utcnow()
    await session.execute(delete(Job).where(Job.state.in_(["done", "skipped"]), Job.last_error_code.is_(None), Job.created_at < now - timedelta(days=7)))
    await session.execute(delete(Job).where(Job.state.in_(["done", "skipped"]), Job.last_error_code.is_not(None), Job.created_at < now - timedelta(days=14)))
    await session.execute(delete(Job).where(Job.state == "failed", Job.created_at < now - timedelta(days=14)))
    await session.execute(update(User).where(User.fsm_expires_at < now, User.fsm_state.like("onboard%")).values(fsm_payload=None, fsm_state="onboard_name", fsm_expires_at=None))
    await session.execute(update(User).where(User.fsm_expires_at < now, ~User.fsm_state.like("onboard%")).values(fsm_payload=None, fsm_state="expired", fsm_expires_at=None))
