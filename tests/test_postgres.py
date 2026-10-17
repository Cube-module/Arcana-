import asyncio
from datetime import timedelta, datetime, timezone, time
from uuid import uuid4
from time import perf_counter
from types import SimpleNamespace
import pytest
from sqlalchemy import select, func, text, update
from sqlalchemy.exc import IntegrityError
from app.models.orm import User, Card, Reading, ReadingCard, DiaryEntry, MonthlyReport, Job, utcnow
from app.models.dto import CreateReading, DiaryInput
from app.repositories.records import owner, owned, cards_for, history
from app.repositories.jobs import claim, ingest
from app.repositories.statistics import monthly_statistics
from app.services.reading_service import ReadingService
from app.services.diary_service import DiaryService
from app.services.memory_service import MemoryService
from app.services.monthly_report_service import MonthlyReportService
from app.services.delivery_service import DeliveryService
from app.services.scheduler_service import SchedulerService
from app.services.profile_service import deletion_token, delete_profile, apply_deletions, set_state
from app.services.time_service import previous_month, period
from app.services.formatters import monthly_text
from app.workers.generation import generate_job
from app.workers.updates import process_update
from app.bot.handlers import Dialog
from app.cli import demo
from app.errors import AppError
from app.config import CONFIG
from conftest import FakeLLM, FakeBot

pytestmark = pytest.mark.postgres


async def reading(db, actor=101, key=None, spread="one", now=None):
    async with db.begin() as session:
        return await ReadingService().create_reading(session, actor, CreateReading(request_key=key or uuid4().hex, question="Как поддержать интерес к учёбе?", topic="study", spread_type=spread, **({"option_a": "Учиться дома", "option_b": "Учиться в библиотеке"} if spread == "choice" else {})), now)


async def finish(db, fail=False):
    job = await claim(db, ["llm_reading", "llm_report"])
    assert job
    adapter = FakeLLM(fail)
    await generate_job(db, adapter, job)
    return adapter


@pytest.mark.parametrize("spread,n", [("one", 1), ("three", 3), ("choice", 3)])
async def test_ac01_persistent_reading(db, spread, n):
    record = await reading(db, spread=spread)
    async with db() as session:
        cards = await cards_for(session, record.id)
        assert len(cards) == len({x["id"] for x in cards}) == n
        assert (await session.get(Reading, record.id)).result_json is None
    await finish(db)
    async with db() as session:
        user = await owner(session, 101)
        rows, _ = await history(session, user.id)
        assert rows[0].status == "ready"
        assert await cards_for(session, record.id) == cards


async def test_ac02_memory_isolation(db):
    own = await reading(db)
    await finish(db)
    other = await reading(db, actor=202)
    await finish(db)
    async with db.begin() as session:
        await DiaryService().add_diary_entry(session, 101, DiaryInput(request_key="own", text="OWN_MEMORY", reading_id=own.id))
        await DiaryService().add_diary_entry(session, 202, DiaryInput(request_key="other", text="FOREIGN_MEMORY", reading_id=other.id))
    current = await reading(db)
    async with db() as session:
        memory = await MemoryService().collect(session, current)
        assert any("OWN_MEMORY" in str(x) for x in memory)
        assert "FOREIGN_MEMORY" not in str(memory)
        assert f"reading:{other.id}" not in str(memory)
        assert f"reading:{current.id}" not in str(memory)


async def test_ac04_concurrent_request_deduplication(db):
    a, b = await asyncio.gather(reading(db, key="same"), reading(db, key="same"))
    assert a.id == b.id
    async with db.begin() as session:
        x = await DiaryService().add_diary_entry(session, 101, DiaryInput(request_key="same-note", text="hello"))
    async with db.begin() as session:
        y = await DiaryService().add_diary_entry(session, 101, DiaryInput(request_key="same-note", text="hello"))
        assert x.id == y.id
    event = {"update_id": 7, "actor_id": 101, "chat_id": 101, "action": "menu", "text": ""}
    await ingest(db, [event, event])
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(Job).where(Job.kind == "update")) == 1


async def test_ac05_fallback_same_cards(db):
    record = await reading(db)
    async with db() as session:
        before = await cards_for(session, record.id)
    await finish(db, fail=True)
    async with db() as session:
        result = await session.get(Reading, record.id)
        assert result.status == "fallback" and "без персонализации" in result.result_json["interpretation"]
        assert await cards_for(session, record.id) == before


async def test_ac06_day_race_and_next_day(db):
    now = datetime(2026, 10, 16, 7, tzinfo=timezone.utc)
    async with db.begin() as session:
        user = await owner(session, 101)
        user.reminders_enabled = True
    async def manual(moment):
        async with db.begin() as session:
            return await ReadingService().get_or_create_day(session, 101, moment)
    first, _ = await asyncio.gather(manual(now), SchedulerService(db).tick(now))
    again = await manual(now)
    assert first.id == again.id
    await finish(db)
    next_day = await manual(now + timedelta(days=1))
    assert next_day.id != first.id
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(Reading)) == 2


async def test_ac07_scheduler_1h_3h(db):
    async with db.begin() as session:
        a, b = await owner(session, 101), await owner(session, 202)
        a.reminders_enabled = b.reminders_enabled = True
        a.timezone, b.timezone = "Europe/Moscow", "Asia/Yekaterinburg"
    await SchedulerService(db).tick(datetime(2026, 10, 16, 7, tzinfo=timezone.utc))
    async with db() as session:
        rows = list((await session.scalars(select(Reading))).all())
        assert len(rows) == 1
        assert rows[0].user_id == (await owner(session, 101)).id


async def test_ac08_diary_persistence_and_rollback(db):
    record = await reading(db)
    async with db.begin() as session:
        linked = await DiaryService().add_diary_entry(session, 101, DiaryInput(request_key="linked", text="first\nsecond", mood=4, reading_id=record.id))
        await DiaryService().add_diary_entry(session, 101, DiaryInput(request_key="standalone", text="free", mood=None))
    async with db() as session:
        stored = await session.get(DiaryEntry, linked.id)
        assert stored.text == "first\nsecond" and stored.mood == 4
    with pytest.raises(RuntimeError):
        async with db.begin() as session:
            await DiaryService().add_diary_entry(session, 101, DiaryInput(request_key="rollback", text="must not persist"))
            raise RuntimeError("database transaction abort")
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(DiaryEntry)) == 2


async def test_ac09_statistics_boundaries_and_mood(db):
    now = utcnow()
    async with db.begin() as session:
        month = await demo(session, 101, now)
        user = await owner(session, 101)
        start, end = period(month, user.timezone)
        session.add_all([DiaryEntry(user_id=user.id, text="start included", request_key="start", created_at=start), DiaryEntry(user_id=user.id, text="end excluded", request_key="end", created_at=end)])
    async with db() as session:
        stats, notes = await monthly_statistics(session, user.id, month, user.timezone)
        assert stats["question_count"] == 4 and stats["day_count"] == 0 and stats["diary_count"] == 5
        assert ["Сила", 2] in stats["cards"]
        assert stats["mood"] == [{"count": 2, "average": 3.0}, {"count": 2, "average": 4.5}]
        assert "end excluded" not in str(notes)


@pytest.mark.parametrize("count,status", [(0, "empty"), (2, "ready"), (4, "queued")])
async def test_ac10_monthly_states_and_repeat(db, count, status):
    async with db.begin() as session:
        user = await owner(session, 101)
        month = previous_month(utcnow(), user.timezone)
        start, end = period(month, user.timezone)
        for i in range(count):
            session.add(DiaryEntry(user_id=user.id, text="note", request_key=f"note:{i}", created_at=start + timedelta(days=i)))
    async with db.begin() as session:
        report = await MonthlyReportService().get_monthly_report(session, 101)
        assert report.status == status
    if count >= 3:
        await finish(db, fail=True)
    async with db.begin() as session:
        same = await MonthlyReportService().get_monthly_report(session, 101)
        assert same.id == report.id
        if count >= 3:
            assert same.status == "fallback" and "фактическая сводка" in monthly_text(same)
        assert await session.scalar(select(func.count()).select_from(MonthlyReport)) == 1


async def test_ac11_foreign_objects_and_composite_fk(db):
    record = await reading(db)
    async with db.begin() as session:
        entry = await DiaryService().add_diary_entry(session, 101, DiaryInput(request_key="entry", text="private"))
        report = await MonthlyReportService().get_monthly_report(session, 101)
    for model, row in ((Reading, record), (DiaryEntry, entry), (MonthlyReport, report)):
        async with db() as session:
            user = await owner(session, 202)
            with pytest.raises(AppError, match="NOT_FOUND"):
                await owned(session, model, user.id, row.id)
    with pytest.raises(IntegrityError):
        async with db.begin() as session:
            user = await owner(session, 202)
            session.add(DiaryEntry(user_id=user.id, reading_id=record.id, text="foreign", request_key="foreign"))


async def test_ac12_lease_and_delivery_resume(db):
    record = await reading(db)
    stale = await claim(db, ["llm_reading"])
    async with db.begin() as session:
        row = await session.get(Job, stale.id)
        row.locked_until = utcnow() - timedelta(seconds=1)
    adapter = FakeLLM()
    await generate_job(db, adapter, stale)
    assert not adapter.calls
    fresh = await claim(db, ["llm_reading"])
    await generate_job(db, adapter, fresh)
    bot = FakeBot()
    bot.fail_once = True
    delivery = await claim(db, ["delivery"])
    service = DeliveryService(db, bot)
    await service.deliver(delivery)
    assert len([c for c in bot.calls if c[0] == "image"]) == 1
    async with db.begin() as session:
        row = await session.get(Job, delivery.id)
        row.available_at = utcnow()
    await service.deliver(await claim(db, ["delivery"]))
    assert len([c for c in bot.calls if c[0] == "image"]) == 1
    assert len(adapter.calls) == 1


async def test_ac13_delete_late_result_restore(db, tmp_path):
    record = await reading(db)
    job = await claim(db, ["llm_reading"])
    registry = tmp_path / "deletions.log"
    async with db.begin() as session:
        user = await owner(session, 101, lock=True)
        token = deletion_token(user)
        set_state(user, None)  # cancel
    async with db() as session:
        assert await owner(session, 101)
    class DeleteDuringLLM(FakeLLM):
        async def generate(self, *args):
            async with db.begin() as session:
                user = await owner(session, 101, lock=True)
                token = deletion_token(user)
                self.old_id = user.id
                await delete_profile(session, user, token, registry)
            return await super().generate(*args)
    adapter = DeleteDuringLLM()
    await generate_job(db, adapter, job)
    async with db.begin() as session:
        assert await session.get(Reading, record.id) is None
        assert await session.get(Job, job.id) is None
        assert await session.scalar(select(func.count()).select_from(Card)) == 78
        session.add(User(id=adapter.old_id, telegram_id=101, chat_id=101, display_name="restored", interests=["study"], timezone="UTC"))
    async with db.begin() as session:
        await apply_deletions(session, registry)
    async with db() as session:
        assert await session.get(User, adapter.old_id) is None


async def test_ac14_pilot_local_db(db, caplog):
    async with db.begin() as session:
        session.add_all(User(telegram_id=i, chat_id=i, display_name="pilot", interests=["study"], timezone="UTC") for i in range(1000, 1098))
    durations = []
    async def action(i):
        started = perf_counter()
        async with db.begin() as session:
            await DiaryService().add_diary_entry(session, 1000 + i, DiaryInput(request_key=f"pilot:{i}", text="PRIVATE_CANARY"))
        async with db() as session:
            user = await owner(session, 1000 + i)
            await history(session, user.id, DiaryEntry)
        durations.append((perf_counter() - started) * 1000)
    for batch in range(5):
        await asyncio.gather(*(action(i) for i in range(batch * 10, batch * 10 + 10)))
    assert len(durations) == 50
    p95 = sorted(durations)[47]
    print(f"LOCAL_DB_50_ACTIONS_P95_MS={p95:.1f}")
    assert "PRIVATE_CANARY" not in caplog.text
    assert p95 < 500


async def test_dialog_transaction_and_duplicate_confirmation(db):
    bot, dialog = FakeBot(), Dialog()
    async def send(n, action="", value=""):
        await ingest(db, [{"update_id": n, "actor_id": 101, "chat_id": 101, "action": action, "text": value}])
        await process_update(db, bot, dialog, await claim(db, ["update"]))
    await send(1, "reading")
    await send(2, "topic:study")
    await send(3, value="Как учиться спокойнее?")
    await send(4, "spread:three")
    async with db() as session:
        user = await owner(session, 101)
        token = user.fsm_payload["token"]
    await send(5, f"confirm:{token}")
    await send(6, f"confirm:{token}")
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(Reading)) == 1
        updates = list((await session.scalars(select(Job).where(Job.kind == "update"))).all())
        assert all(j.payload == {} and j.state == "done" for j in updates)


async def test_queue_quota_storage_and_busy(db, monkeypatch):
    await reading(db)
    with pytest.raises(AppError, match="BUSY"):
        await reading(db)
    monkeypatch.setitem(CONFIG["llm"], "queue_limit", 1)
    with pytest.raises(AppError, match="QUEUE_FULL"):
        await reading(db, actor=202)
    await finish(db)
    monkeypatch.setitem(CONFIG["quota"], "daily", 1)
    with pytest.raises(AppError, match="QUOTA_EXCEEDED"):
        await reading(db)
    async with db.begin() as session:
        day = await ReadingService().get_or_create_day(session, 101)
        assert day.kind == "day"
    monkeypatch.setitem(CONFIG, "storage_limit", 1)
    async with db.begin() as session:
        await DiaryService().add_diary_entry(session, 101, DiaryInput(request_key="first-note", text="note"))
    with pytest.raises(AppError, match="STORAGE_LIMIT"):
        async with db.begin() as session:
            await DiaryService().add_diary_entry(session, 101, DiaryInput(request_key="second-note", text="note"))


async def test_onboarding_media_and_durable_dialog(db):
    bot, dialog = FakeBot(), Dialog()
    async def send(n, action="", value="", media=False):
        await ingest(db, [{"update_id": n, "actor_id": 303, "chat_id": 303, "action": action, "text": value, "media": media}])
        await process_update(db, bot, dialog, await claim(db, ["update"]))
    await send(1, value="/start")
    assert "условия" in bot.calls[0][2]
    async with db() as session:
        assert await session.scalar(select(User).where(User.telegram_id == 303)) is None
    await send(2, "consent")
    await send(3, value="Анна")
    await send(4, "interest:study")
    await send(5, "interests_done")
    await send(6, "zone:0")
    await send(7, "onboard_done")
    await send(8, media=True)
    async with db() as session:
        user = await owner(session, 303)
        assert user.display_name == "Анна" and user.interests == ["study"]
        assert user.timezone == "Europe/Moscow" and not user.reminders_enabled
        responses = list((await session.scalars(select(Job).where(Job.kind == "delivery", Job.user_id == user.id))).all())
        assert any("только текст" in str(j.payload) for j in responses)
        assert await session.scalar(select(func.count()).select_from(Reading)) == 0


async def test_update_lease_preserves_actor_order(db):
    await ingest(db, [{"update_id": n, "actor_id": 101, "chat_id": 101, "action": "menu", "text": ""} for n in (1, 2)])
    first = await claim(db, ["update"])
    assert first.payload["update_id"] == 1
    assert await claim(db, ["update"]) is None
    async with db.begin() as session:
        row = await session.get(Job, first.id)
        row.locked_until = utcnow() - timedelta(seconds=1)
    recovered = await claim(db, ["update"])
    assert recovered.id == first.id and recovered.attempts == 2


async def test_memory_cap_removes_whole_old_records(db):
    async with db.begin() as session:
        user = await owner(session, 101)
        for i in range(5):
            session.add(DiaryEntry(user_id=user.id, text=str(i) * 2000, request_key=f"long:{i}", created_at=utcnow() - timedelta(days=i)))
    record = await reading(db)
    async with db() as session:
        memory = await MemoryService().collect(session, record)
        import json
        assert len(json.dumps(memory, ensure_ascii=False)) <= 6000
        assert len(memory) == 2
        assert all(len(x["text"]) == 2000 for x in memory)
