import asyncio
import logging
from time import monotonic
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from pydantic import ValidationError
from aiogram.types import InlineKeyboardMarkup
from app.models.orm import User, Job
from app.repositories.jobs import claim, leased, enqueue, ingest
from app.bot.handlers import Dialog, reply
from app.errors import AppError

log = logging.getLogger("arcana")


def minimal_event(update):
    callback = update.callback_query
    message = callback.message if callback else update.message
    actor = callback.from_user if callback else (message.from_user if message else None)
    if not message or not actor or message.chat.type != "private":
        return None
    return {"update_id": update.update_id, "actor_id": actor.id, "chat_id": message.chat.id, "action": callback.data or "" if callback else "", "text": "" if callback else (message.text or ""), "media": not callback and message.text is None}


async def poll(sessions, bot):
    offset = None
    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=25, allowed_updates=["message", "callback_query"], request_timeout=30)
            for item in updates:
                if item.callback_query:
                    try:
                        await bot.answer_callback_query(item.callback_query.id, request_timeout=2)
                    except Exception:
                        pass
            events = [event for item in updates if (event := minimal_event(item)) is not None]
            await ingest(sessions, events)  # COMMIT is complete before advancing offset.
            if updates:
                offset = updates[-1].update_id + 1
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("request_id=poll action=get_updates error_code=TRANSPORT_OR_STORAGE")
            await asyncio.sleep(1)


async def process_update(sessions, bot, dialog, job):
    try:
        await _process_update(sessions, bot, dialog, job)
    except (SQLAlchemyError, OSError):
        log.error("request_id=%s action=update error_code=STORAGE_ERROR", job.id)
        try:
            await bot.send_message(job.payload["chat_id"], "Не удалось сохранить. Попробуй ещё раз.", parse_mode=None)
        except Exception:
            pass
        # Leave the original update recoverable; never commit a success receipt.


async def _process_update(sessions, bot, dialog, job):
    started = monotonic()
    direct, error = None, None
    async with sessions.begin() as session:
        live = await leased(session, job)
        if not live:
            return
        event = live.payload
        await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": event["actor_id"]})
        try:
            async with session.begin_nested():
                response = await dialog.handle(session, event)
        except AppError as exc:
            error, response = exc.code, reply(exc.message)
        except (ValidationError, ValueError):
            error, response = "VALIDATION_ERROR", reply("Проверь длину текста и обязательные поля. Вопрос: 1–1000, вариант: 1–200, заметка: 1–2000 символов. Настроение: 1–5.")
        user = await session.scalar(select(User).where(User.telegram_id == event["actor_id"]))
        if response:
            if user:
                await enqueue(session, user_id=user.id, kind="delivery", dedupe_key=f"reply:{event['update_id']}", payload={"messages": [response]})
            else:
                direct = response
        # The deletion transaction may already have cascaded this job.
        current = await session.scalar(select(Job).where(Job.id == job.id).execution_options(populate_existing=True))
        if current:
            current.state, current.payload, current.last_error_code = "done", {}, error
    if direct:
        try:
            await bot.send_message(event["chat_id"], direct["text"], reply_markup=InlineKeyboardMarkup.model_validate(direct["reply_markup"]), parse_mode=None)
        except Exception:
            log.warning("request_id=%s action=onboarding_reply error_code=TELEGRAM_UNAVAILABLE", job.id)
    log.info("request_id=%s action=update duration=%.3f error_code=%s", job.id, monotonic() - started, error or "NONE")


async def update_loop(sessions, bot, demo_mode=True):
    dialog = Dialog(demo_mode)
    # One ordered update consumer preserves Telegram order, including before registration.
    while True:
        try:
            job = await claim(sessions, ["update"])
            if job:
                await process_update(sessions, bot, dialog, job)
            else:
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("request_id=update action=update error_code=STORAGE_ERROR")
            await asyncio.sleep(1)
