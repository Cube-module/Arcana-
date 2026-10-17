import asyncio
import logging
from time import monotonic
from app.models.orm import Reading, MonthlyReport, User, utcnow
from app.repositories.records import cards_for
from app.repositories.jobs import claim, leased, enqueue
from app.services.memory_service import MemoryService
from app.integrations.llm import fallback_reading
from app.errors import AppError
from app.config import CONFIG

log = logging.getLogger("arcana")


async def generate_job(sessions, adapter, job):
    started = monotonic()
    async with sessions.begin() as session:
        live = await leased(session, job)
        if live is None:
            return
        model = Reading if job.kind == "llm_reading" else MonthlyReport
        record = await session.get(model, job.reading_id or job.report_id)
        if not record or not await session.get(User, job.user_id):
            return
        if record.status in ("ready", "fallback", "empty"):
            live.state, live.payload = "done", {}
            return
        record.status = "running"
        if model is Reading:
            cards = await cards_for(session, record.id)
            memory = await MemoryService().collect(session, record)
            context = {"question": record.question, "topic": record.topic, "option_a": record.option_a, "option_b": record.option_b, "cards": [{k: c[k] for k in ("name", "meaning", "label")} for c in cards], "memory": memory}
        else:
            context = {"statistics": record.statistics_json, "notes": job.payload.get("notes", [])}
        budget = CONFIG["llm"]["budget"] - (utcnow() - record.created_at).total_seconds()
    # The HTTP call deliberately runs after transaction completion.
    code = None
    try:
        result = await adapter.generate("reading" if model is Reading else "monthly", context, budget)
        status = "ready"
    except AppError as exc:
        code = exc.code
        status = "fallback"
        result = fallback_reading(cards) if model is Reading else {"observations": [], "reflection_questions": []}
    async with sessions.begin() as session:
        live = await leased(session, job)
        if live is None:
            return  # Deleted owner or stale lease: discard the late result.
        record = await session.get(model, job.reading_id or job.report_id)
        if record is None or record.status not in ("queued", "running"):
            return
        record.status, record.result_json, record.completed_at = status, result, utcnow()
        record.prompt_version = "reading_v1" if model is Reading else "monthly_v1"
        if model is Reading:
            record.memory_ids = [x["id"] for x in context["memory"]]
            record.model_name = adapter.settings.llm_model if status == "ready" else None
        live.state, live.payload, live.last_error_code = "done", {}, code
        await enqueue(session, user_id=job.user_id, kind="delivery", reading_id=job.reading_id, report_id=job.report_id, dedupe_key=f"result:{record.id}")
    log.info("request_id=%s action=%s duration=%.3f error_code=%s", job.id, job.kind, monotonic() - started, code or "NONE")


async def generation_loop(sessions, adapter):
    while True:
        try:
            job = await claim(sessions, ["llm_reading", "llm_report"])
            if job:
                await generate_job(sessions, adapter, job)
            else:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("request_id=worker action=generation error_code=STORAGE_ERROR")
            await asyncio.sleep(1)
