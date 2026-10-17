import asyncio
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, func
from sqlalchemy.exc import OperationalError
from app.config import Settings, CONFIG
from app.errors import AppError
from app.integrations.llm import LLMAdapter
from app.models.dto import DiaryInput
from app.models.orm import Job, DiaryEntry, Reading, utcnow
from app.repositories.jobs import ingest, claim, cleanup
from app.repositories.records import owner, history
from app.repositories.statistics import monthly_statistics
from app.services.profile_service import deletion_token, delete_profile, set_state
from app.services.diary_service import DiaryService
from app.services.time_service import previous_month, period
from app.services.monthly_report_service import MonthlyReportService
from app.services.delivery_service import DeliveryService
from app.services.deck_service import load_deck
from app.workers.updates import process_update, minimal_event
from app.bot.handlers import Dialog
from conftest import FakeBot


async def test_llm_contract_and_concurrency():
    active = maximum = 0
    bodies = []
    async def handler(request):
        nonlocal active, maximum
        import json
        bodies.append(json.loads(request.content))
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"interpretation":"Текущий вопрос", "reflection_question":"Что важно?", "memory_refs":[]}'}}]})
    adapter = LLMAdapter(Settings(llm_base_url="https://example.test/v1", llm_api_key=SecretStr("SECRET"), llm_model="CONFIGURED_MODEL"), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await asyncio.gather(*(adapter.generate("reading", {"question": "test", "memory": []}) for _ in range(10)))
    assert maximum == 3
    assert all(b["model"] == "CONFIGURED_MODEL" and b["temperature"] == 0.7 and b["max_tokens"] == 1200 and b["stream"] is False for b in bodies)
    assert "SECRET" not in str(bodies)
    await adapter.close()


def test_minimal_update_never_retains_media_or_contacts():
    message = SimpleNamespace(chat=SimpleNamespace(id=1, type="private"), from_user=SimpleNamespace(id=1), text=None, photo=["SECRET_FILE_ID"])
    event = minimal_event(SimpleNamespace(callback_query=None, message=message, update_id=1))
    assert event["media"] and "SECRET_FILE_ID" not in str(event)
    message.chat.type = "group"
    assert minimal_event(SimpleNamespace(callback_query=None, message=message, update_id=2)) is None


def test_complete_deck():
    deck = load_deck()
    assert len(deck) == 78
    assert len({c["code"] for c in deck}) == 78


@pytest.mark.postgres
async def test_storage_failure_has_no_success_receipt(db):
    class BrokenDiary(Dialog):
        async def handle(self, session, event):
            await DiaryService().add_diary_entry(session, 101, DiaryInput(request_key="aborted", text="must roll back"))
            raise OperationalError("INSERT", {}, Exception("failure"))
    bot = FakeBot()
    await ingest(db, [{"update_id": 1, "actor_id": 101, "chat_id": 101, "action": "", "text": "must roll back"}])
    await process_update(db, bot, BrokenDiary(), await claim(db, ["update"]))
    assert len(bot.calls) == 1 and "Не удалось сохранить" in bot.calls[0][2]
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(DiaryEntry)) == 0
        assert await session.scalar(select(func.count()).select_from(Job).where(Job.kind == "delivery")) == 0


@pytest.mark.postgres
async def test_monthly_note_sampling_and_history_cursor(db):
    async with db.begin() as session:
        user = await owner(session, 101)
        month = previous_month(utcnow(), user.timezone)
        start, _ = period(month, user.timezone)
        for i in range(30):
            session.add(DiaryEntry(user_id=user.id, text=(str(i) + ":") * 600, request_key=f"sample:{i}", created_at=start + timedelta(hours=i * 20)))
    async with db() as session:
        stats, notes = await monthly_statistics(session, user.id, month, user.timezone)
        import json
        assert len(notes) == 20 and len(json.dumps(notes, ensure_ascii=False)) <= 10000
        assert notes[0]["text"].startswith("0:") and notes[-1]["text"].startswith("29:")
        first, cursor = await history(session, user.id, DiaryEntry)
        second, _ = await history(session, user.id, DiaryEntry, cursor)
        assert len(first) == len(second) == 10
        assert not {x.id for x in first} & {x.id for x in second}


@pytest.mark.postgres
async def test_delete_token_is_owner_bound_and_expires(db, tmp_path):
    registry = tmp_path / "deleted.log"
    async with db.begin() as session:
        a, b = await owner(session, 101), await owner(session, 202)
        token = deletion_token(a)
        deletion_token(b)
        with pytest.raises(AppError):
            await delete_profile(session, b, token, registry)
        a.fsm_expires_at = utcnow() - timedelta(seconds=1)
        with pytest.raises(AppError):
            await delete_profile(session, a, token, registry)
    assert not registry.exists()


@pytest.mark.postgres
async def test_retention_clears_expired_draft_and_receipts(db):
    async with db.begin() as session:
        user = await owner(session, 101)
        set_state(user, "question", {"private": "draft"})
        user.fsm_expires_at = utcnow() - timedelta(seconds=1)
        session.add(Job(user_id=user.id, kind="update", state="done", dedupe_key="old", payload={}, created_at=utcnow() - timedelta(days=8)))
        session.add(Job(user_id=user.id, kind="update", state="failed", dedupe_key="error", payload={}, last_error_code="STORAGE_ERROR", created_at=utcnow() - timedelta(days=15)))
    async with db.begin() as session:
        await cleanup(session)
    async with db() as session:
        user = await owner(session, 101)
        assert user.fsm_payload is None
        assert await session.scalar(select(func.count()).select_from(Job)) == 0
