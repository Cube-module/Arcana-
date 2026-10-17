import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from aiogram.types import FSInputFile, InlineKeyboardMarkup
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest, TelegramNetworkError, TelegramServerError
from app.models.orm import User, Reading, MonthlyReport, Card, utcnow
from app.repositories.records import cards_for
from app.repositories.jobs import leased, claim
from app.services.formatters import reading_text, monthly_text, split_messages
from app.bot.keyboards import buttons
from app.config import ROOT

log = logging.getLogger("arcana")


async def delivery_steps(session, job):
    if "messages" in job.payload:
        return [{"kind": "text", **m} for m in job.payload["messages"]]
    if job.reading_id:
        reading = await session.get(Reading, job.reading_id)
        if reading is None or reading.result_json is None:
            return []
        cards = await cards_for(session, reading.id)
        steps = [{"kind": "image", "card": card} for card in cards]
        text = reading_text(reading, cards)
        markup = buttons([("Записать мысли", f"note:{reading.id}")])
    else:
        report = await session.get(MonthlyReport, job.report_id)
        if report is None or report.result_json is None:
            return []
        steps = []
        text = monthly_text(report)
        markup = buttons([("История месяца", f"month_history:{report.id}")])
    messages = split_messages(text)
    for i, message in enumerate(messages):
        steps.append({"kind": "text", "text": message, "reply_markup": markup if i == len(messages) - 1 else None})
    return steps


class DeliveryService:
    def __init__(self, sessions, bot):
        self.sessions, self.bot = sessions, bot

    async def deliver(self, job):
        async with self.sessions.begin() as session:
            if not await leased(session, job):
                return
            user = await session.get(User, job.user_id)
            if user is None:
                return
            chat_id = user.chat_id
            steps = await delivery_steps(session, job)
        for index, step in enumerate(steps):
            async with self.sessions.begin() as session:
                live = await leased(session, job)
                if not live:
                    return
                if str(index) in live.payload.get("sent", {}):
                    continue
            try:
                file_id = None
                if step["kind"] == "image":
                    card = step["card"]
                    path = ROOT / card["image_path"]
                    original = path.exists()
                    if not original:
                        path = ROOT / "assets/images/back.jpg"
                        log.warning("request_id=%s action=image error_code=MISSING_IMAGE card_id=%s", job.id, card["id"])
                    media = card["telegram_file_id"] or FSInputFile(path)
                    try:
                        sent = await self.bot.send_photo(chat_id, media, caption=f"{card['label']}: {card['name']}", request_timeout=10)
                    except TelegramBadRequest:
                        if not card["telegram_file_id"]:
                            raise
                        sent = await self.bot.send_photo(chat_id, FSInputFile(path), caption=f"{card['label']}: {card['name']}", request_timeout=10)
                    file_id = sent.photo[-1].file_id if original and sent.photo else None
                else:
                    markup = InlineKeyboardMarkup.model_validate(step["reply_markup"]) if step.get("reply_markup") else None
                    sent = await self.bot.send_message(chat_id, step["text"], reply_markup=markup, parse_mode=None, request_timeout=10)
                async with self.sessions.begin() as session:
                    live = await leased(session, job)
                    if live is None:
                        return
                    payload = dict(live.payload)
                    payload["sent"] = {**payload.get("sent", {}), str(index): sent.message_id}
                    live.payload = payload
                    if file_id:
                        card_row = await session.get(Card, step["card"]["id"])
                        card_row.telegram_file_id = file_id
            except TelegramForbiddenError:
                async with self.sessions.begin() as session:
                    live = await leased(session, job)
                    user = await session.get(User, job.user_id)
                    if user:
                        user.reminders_enabled = False
                    if live:
                        live.state, live.last_error_code, live.payload = "failed", "TELEGRAM_UNAVAILABLE", {}
                return
            except (TelegramRetryAfter, TelegramNetworkError, TelegramServerError, TelegramBadRequest, OSError, TimeoutError) as exc:
                async with self.sessions.begin() as session:
                    live = await leased(session, job)
                    if live:
                        live.last_error_code = "TELEGRAM_UNAVAILABLE"
                        if isinstance(exc, TelegramRetryAfter):
                            delay = exc.retry_after
                            live.state = "pending"
                        elif live.attempts <= 3:
                            delay = [5, 20, 60][live.attempts - 1]
                            live.state = "pending"
                        else:
                            delay = 0
                            live.state, live.payload = "failed", {}
                        live.available_at = utcnow() + timedelta(seconds=delay)
                return
        async with self.sessions.begin() as session:
            live = await leased(session, job)
            if live:
                # Retain only message receipts, never text after completion.
                live.state, live.payload = "done", {"sent": live.payload.get("sent", {})}

    async def run(self):
        while True:
            try:
                job = await claim(self.sessions, ["delivery"])
                if job:
                    await self.deliver(job)
                else:
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.error("request_id=delivery action=delivery error_code=STORAGE_ERROR")
                await asyncio.sleep(1)
