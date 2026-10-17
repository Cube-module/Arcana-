from datetime import timedelta, time
from secrets import token_urlsafe, compare_digest
import os
from sqlalchemy import select, delete
from app.models.orm import User, Job, utcnow
from app.models.dto import Onboarding
from app.errors import AppError
from app.config import CONFIG, ROOT

CONSENT = "Аркана — расклады и дневник для самопознания и развлечения, а не достоверные предсказания. Мы сохраняем профиль, вопросы, карты и заметки до удаления профиля. Внешняя ИИ-модель получает вопрос и ограниченную часть истории, без Telegram ID и токенов. Имена и другие сведения, которые ты сам напишешь в тексте, могут попасть в контекст: не указывай контакты и секреты. Черновики хранятся 10 минут. Удаление профиля не удаляет переписку Telegram и данные у провайдера ИИ; резервные копии хранятся до 7 суток. Принимаешь условия и передачу текста модели?"


def set_state(user, state, payload=None, ttl=None):
    user.fsm_state = state
    user.fsm_payload = payload or {}
    user.fsm_expires_at = utcnow() + timedelta(seconds=ttl or CONFIG["session_ttl"]) if state else None


async def finish_onboarding(session, user, data: Onboarding):
    user.display_name, user.interests, user.timezone = data.display_name, data.interests, data.timezone
    set_state(user, None)
    await session.flush()


def save_reminder_settings(user, enabled, value):
    if type(enabled) is not bool:
        raise AppError("VALIDATION_ERROR")
    try:
        at = time.fromisoformat(value)
        if len(value) != 5 or at.tzinfo:
            raise ValueError()
    except ValueError:
        raise AppError("VALIDATION_ERROR", "Введи время в формате ЧЧ:ММ, например 09:00.") from None
    user.reminders_enabled, user.reminder_time = enabled, at


def deletion_token(user):
    token = token_urlsafe(16)
    set_state(user, "delete", {"token": token}, ttl=300)
    return token


async def delete_profile(session, user, token, registry=None):
    if user.fsm_state != "delete" or not user.fsm_expires_at or user.fsm_expires_at < utcnow() or not compare_digest((user.fsm_payload or {}).get("token", ""), token):
        raise AppError("SESSION_EXPIRED")
    registry = registry or ROOT / "runtime" / "deletions.log"
    registry.parent.mkdir(parents=True, exist_ok=True)
    # Write ahead of deletion: even a crash after COMMIT cannot resurrect this UUID in a backup.
    with registry.open("a", encoding="utf-8") as output:
        output.write(f"{user.id}\n")
        output.flush()
        os.fsync(output.fileno())
    await session.delete(user)
    await session.flush()


async def apply_deletions(session, registry):
    from uuid import UUID
    if not registry.exists():
        raise ValueError("Deletion registry must be present before restore is opened")
    for value in set(registry.read_text(encoding="utf-8").splitlines()):
        await session.execute(delete(User).where(User.id == UUID(value)))
