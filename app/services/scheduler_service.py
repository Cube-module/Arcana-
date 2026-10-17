import asyncio
import logging
from datetime import time
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.models.orm import User, Job, utcnow
from app.services.reading_service import ReadingService
from app.services.monthly_report_service import MonthlyReportService
from app.services.time_service import reminder_due
from app.repositories.jobs import enqueue, cleanup
from app.errors import AppError
from app.config import CONFIG


class SchedulerService:
    def __init__(self, sessions, demo_mode=True):
        self.sessions = sessions
        self.readings = ReadingService(demo_mode)

    async def tick(self, now=None):
        now = now or utcnow()
        async with self.sessions() as session:
            users = list((await session.scalars(select(User))).all())
        for user in users:
            if user.fsm_state and user.fsm_state.startswith("onboard"):
                continue
            try:
                if user.reminders_enabled and reminder_due(now, user.timezone, user.reminder_time, CONFIG["late_delivery_hours"]):
                    async with self.sessions.begin() as session:
                        record = await self.readings.get_or_create_day(session, user.telegram_id, now)
                        if record.status in ("ready", "fallback"):
                            await enqueue(session, user_id=user.id, kind="delivery", reading_id=record.id, dedupe_key=f"result:{record.id}")
                local = now.astimezone(ZoneInfo(user.timezone))
                if local.day <= CONFIG["monthly_catchup_days"] and (local.day > 1 or local.time() >= time(9)):
                    async with self.sessions.begin() as session:
                        await MonthlyReportService().get_monthly_report(session, user.telegram_id, now=now)
            except AppError:
                pass  # Busy background requests wait for the next tick.
        async with self.sessions.begin() as session:
            await cleanup(session)

    async def run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger("arcana").error("request_id=scheduler action=schedule error_code=STORAGE_ERROR")
            await asyncio.sleep(60)
