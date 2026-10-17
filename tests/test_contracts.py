from datetime import datetime, time, timezone
import asyncio
import json
import httpx
import pytest
from pydantic import ValidationError, SecretStr
from app.config import Settings
from app.models.dto import CreateReading, DiaryInput, normalize
from app.services.reading_service import choose_cards
from app.services.time_service import previous_month, period, local_day, reminder_due
from app.services.formatters import split_messages
from app.services.safety import guard_question, valid_output
from app.integrations.llm import LLMAdapter, fallback_reading
from app.errors import AppError


@pytest.mark.parametrize("spread,n", [("one", 1), ("three", 3), ("choice", 3)])
def test_ac01_selection(spread, n):
    cards = choose_cards(list(range(78)), spread)
    assert len(cards) == len({x[0] for x in cards}) == n
    assert cards[0][1] == {"one": "Фокус", "three": "Ситуация", "choice": "Вариант А"}[spread]


@pytest.mark.parametrize("patch", [{"question": " "}, {"question": "x" * 1001}, {"question": None}, {"spread_type": "choice", "option_a": "A"}, {"user_id": "foreign"}])
def test_ac03_reading_input(patch):
    with pytest.raises(ValidationError):
        CreateReading.model_validate({"request_key": "test", "question": "test", "topic": "study", "spread_type": "one", **patch})


@pytest.mark.parametrize("mood", ["4", True, False, 0, 6, 3.0])
def test_ac03_strict_mood(mood):
    with pytest.raises(ValidationError):
        DiaryInput(request_key="test", text="note", mood=mood)


def test_normalization_and_text_split():
    assert normalize("  a  \n b  ", 50) == "a b"
    assert normalize("a  \n b", 50, diary=True) == "a \n b"
    with pytest.raises(ValueError):
        normalize("test\x00", 50)
    assert all(len(m) <= 3500 for m in split_messages("a" * 4000 + "\n\n" + "b " * 2000))


@pytest.mark.parametrize("status,expected_calls", [(429, 2), (500, 2), (401, 1), (403, 1)])
async def test_ac05_http_retry(status, expected_calls):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={})
    adapter = LLMAdapter(Settings(llm_base_url="https://example.test/v1", llm_api_key=SecretStr("test"), llm_model="test"), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(AppError):
        await adapter.generate("reading", {"memory": []})
    assert len(calls) == expected_calls
    await adapter.close()


@pytest.mark.parametrize("content", ["", "{}", "bad json", json.dumps({"interpretation": "test", "reflection_question": "?", "memory_refs": ["foreign"]})])
async def test_ac05_bad_response(content):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": content}}]})))
    adapter = LLMAdapter(Settings(llm_base_url="https://example.test", llm_model="test"), client)
    with pytest.raises(AppError):
        await adapter.generate("reading", {"memory": []})
    await adapter.close()


async def test_ac05_timeout_budget():
    async def slow(request):
        await asyncio.sleep(1)
    adapter = LLMAdapter(Settings(llm_base_url="https://example.test"), httpx.AsyncClient(transport=httpx.MockTransport(slow)))
    with pytest.raises(AppError, match="LLM_TIMEOUT"):
        await adapter.generate("reading", {}, budget=0.02)
    await adapter.close()


def test_ac07_timezones_and_late_window():
    now = datetime(2026, 10, 1, 21, 30, tzinfo=timezone.utc)
    assert str(local_day(now, "Europe/Moscow")) == "2026-10-02"
    assert str(local_day(now, "UTC")) == "2026-10-01"
    assert reminder_due(datetime(2026, 10, 2, 7, tzinfo=timezone.utc), "Europe/Moscow", time(9))
    assert not reminder_due(datetime(2026, 10, 2, 9, tzinfo=timezone.utc), "Europe/Moscow", time(9))
    month = previous_month(now, "Europe/Moscow")
    start, end = period(month, "Europe/Moscow")
    assert start.isoformat() == "2026-08-31T21:00:00+00:00"
    assert end.isoformat() == "2026-09-30T21:00:00+00:00"


def test_ac14_safety():
    for text in ("Я хочу убить себя", "Какие таблетки выбрать для лечения?", "Дай юридический совет"):
        with pytest.raises(AppError, match="SAFETY_RESTRICTED"):
            guard_question(text)
    guard_question("Как поддержать себя в учёбе?")
    assert not valid_output("Прекрати лечение")
