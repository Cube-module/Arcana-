from datetime import timedelta, datetime, timezone
import pytest
from pydantic import ValidationError
from sqlalchemy import select, func
from app.models.dto import ApiResponse
from app.models.orm import User, Reading, DiaryEntry, MonthlyReport, Job, utcnow
from app.services.application_service import ApplicationService
from app.services.scheduler_service import SchedulerService
from app.services.monthly_report_service import MonthlyReportService
from app.services.delivery_service import DeliveryService
from app.workers.generation import generate_job
from app.repositories.jobs import claim
from app.repositories.records import owner
from app.cli import demo
from conftest import FakeLLM, FakeBot


def test_response_has_exactly_one_data_or_error():
    with pytest.raises(ValidationError):
        ApiResponse(ok=True, request_id="test")
    with pytest.raises(ValidationError):
        ApiResponse(ok=False, data={}, request_id="test")


@pytest.mark.postgres
async def test_internal_api_contract_and_unknown_fields(db):
    api = ApplicationService()
    async with db.begin() as session:
        bad = await api.call(session, 101, "create_reading", {"request_key": "bad", "question": "question", "spread_type": "one", "topic": "study", "user_id": "forged"}, "req-1")
        assert not bad.ok and bad.error.code == "VALIDATION_ERROR" and bad.data is None
        good = await api.call(session, 101, "create_reading", {"request_key": "good", "question": "question", "spread_type": "one", "topic": "study"}, "req-2")
        assert good.ok and good.error is None and len(good.data["cards"]) == 1
        found = await api.call(session, 101, "get_reading", {"reading_id": good.data["reading_id"]}, "req-3")
        assert found.data["reading_id"] == good.data["reading_id"]
        denied = await api.call(session, 202, "get_reading", {"reading_id": good.data["reading_id"]}, "req-4")
        assert denied.error.code == "NOT_FOUND"
        note = await api.call(session, 101, "add_diary_entry", {"request_key": "note", "text": "note", "mood": 4}, "req-5")
        assert note.ok and note.data["entry_id"]
        history = await api.call(session, 101, "list_diary", {}, "req-6")
        assert history.data["items"][0]["mood"] == 4
        assert await session.scalar(select(func.count()).select_from(Reading)) == 1


@pytest.mark.postgres
async def test_ac10_full_monthly_generation_and_delivery(db):
    async with db.begin() as session:
        await demo(session, 101)
        report = await MonthlyReportService().get_monthly_report(session, 101)
    adapter = FakeLLM()
    await generate_job(db, adapter, await claim(db, ["llm_report"]))
    bot = FakeBot()
    await DeliveryService(db, bot).deliver(await claim(db, ["delivery"]))
    async with db() as session:
        stored = await session.get(MonthlyReport, report.id)
        assert stored.status == "ready"
        assert stored.prompt_version == "monthly_v1"
        assert stored.statistics_json["question_count"] == 4
    assert len(adapter.calls) == 1 and adapter.calls[0][0] == "monthly"
    assert any("Раскладов по вопросу: 4" in c[2] for c in bot.calls)
    assert any("В записях есть опыт обучения" in c[2] for c in bot.calls)


@pytest.mark.postgres
async def test_monthly_catchup_window_and_invalid_month(db):
    scheduler = SchedulerService(db)
    await scheduler.tick(datetime(2026, 10, 8, 10, tzinfo=timezone.utc))
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(MonthlyReport)) == 0
    await scheduler.tick(datetime(2026, 10, 7, 10, tzinfo=timezone.utc))
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(MonthlyReport)) == 2
    async with db.begin() as session:
        result = await ApplicationService().call(session, 101, "get_monthly_report", {"month": "2099-01"}, "future")
        assert not result.ok and result.error.code == "VALIDATION_ERROR"
