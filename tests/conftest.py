import os
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import text
from app.repositories.database import connect
from app.models.orm import User
from app.services.deck_service import seed_deck


@pytest_asyncio.fixture
async def db():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for real PostgreSQL acceptance tests")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.fail("Use a disposable database whose name ends in _test")
    env = {**os.environ, "DATABASE_URL": url}
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    engine, sessions = connect(url)
    async with sessions.begin() as session:
        await session.execute(text("TRUNCATE users, cards CASCADE"))
        await seed_deck(session)
        session.add_all([User(telegram_id=n, chat_id=n, display_name=f"User {n}", interests=["study"], timezone="Europe/Moscow") for n in (101, 202)])
    yield sessions
    await engine.dispose()


class FakeBot:
    def __init__(self):
        self.calls = []
        self.fail_once = False

    async def send_message(self, chat_id, text, **kwargs):
        from aiogram.exceptions import TelegramNetworkError
        from aiogram.methods import SendMessage
        if self.fail_once:
            self.fail_once = False
            raise TelegramNetworkError(method=SendMessage(chat_id=chat_id, text=text), message="test failure")
        self.calls.append(("text", chat_id, text))
        return SimpleNamespace(message_id=len(self.calls))

    async def send_photo(self, chat_id, photo, **kwargs):
        self.calls.append(("image", chat_id, kwargs.get("caption")))
        return SimpleNamespace(message_id=len(self.calls), photo=[SimpleNamespace(file_id="test_file_id")])


class FakeLLM:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail
        self.settings = SimpleNamespace(llm_model="test-model")

    async def generate(self, kind, context, budget):
        from app.errors import AppError
        self.calls.append((kind, context))
        if self.fail:
            raise AppError("LLM_TIMEOUT")
        if kind == "reading":
            return {"interpretation": "Опираюсь только на текущий вопрос. Можно подумать о небольшом следующем шаге.", "reflection_question": "Что сейчас важно?", "memory_refs": []}
        return {"observations": ["В записях есть опыт обучения."], "reflection_questions": ["Что хотелось бы продолжить?"]}
