"""The concrete internal application API from section 3.1; no HTTP server."""
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from app.models.dto import DTO, ApiResponse, ApiError, CreateReading, DiaryInput, Onboarding, ReadingInput, HistoryInput, MonthInput, ReminderInput, DeleteInput
from app.models.orm import Reading, DiaryEntry, MonthlyReport
from app.repositories.records import owner, owned, cards_for, history, actor_profile
from app.services.reading_service import ReadingService
from app.services.diary_service import DiaryService
from app.services.monthly_report_service import MonthlyReportService
from app.services.profile_service import finish_onboarding, save_reminder_settings, delete_profile
from app.services.formatters import monthly_text
from app.errors import AppError, MESSAGES


class ApplicationService:
    def __init__(self, demo_mode=True):
        self.readings = ReadingService(demo_mode)

    async def call(self, session, actor_id, method, values, request_id):
        try:
            async with session.begin_nested():
                result = await self._execute(session, actor_id, method, values)
            return ApiResponse(ok=True, data=result, request_id=request_id)
        except ValidationError as exc:
            field = str(exc.errors()[0]["loc"][0]) if exc.errors()[0]["loc"] else None
            error = ApiError(code="VALIDATION_ERROR", message=MESSAGES["VALIDATION_ERROR"], field=field)
        except AppError as exc:
            error = ApiError(code=exc.code, message=exc.message, retryable=exc.code in ("BUSY", "QUEUE_FULL", "STORAGE_ERROR"))
        except SQLAlchemyError:
            error = ApiError(code="STORAGE_ERROR", message=MESSAGES["STORAGE_ERROR"], retryable=True)
        return ApiResponse(ok=False, error=error, request_id=request_id)

    async def _execute(self, session, actor_id, method, values):
        if method == "create_reading":
            record = await self.readings.create_reading(session, actor_id, CreateReading.model_validate(values))
        elif method == "get_day_card":
            DTO.model_validate(values)
            record = await self.readings.get_or_create_day(session, actor_id)
        elif method == "get_reading":
            data = ReadingInput.model_validate(values)
            user = await owner(session, actor_id)
            record = await owned(session, Reading, user.id, data.reading_id)
        elif method == "add_diary_entry":
            entry = await DiaryService().add_diary_entry(session, actor_id, DiaryInput.model_validate(values))
            return {"entry_id": str(entry.id), "created_at": entry.created_at.isoformat()}
        elif method == "get_monthly_report":
            data = MonthInput.model_validate(values)
            report = await MonthlyReportService().get_monthly_report(session, actor_id, data.month)
            return {"report_id": str(report.id), "status": report.status, "statistics": report.statistics_json, "text": monthly_text(report) if report.result_json is not None else None}
        elif method in ("list_history", "list_diary"):
            data = HistoryInput.model_validate(values)
            user = await owner(session, actor_id)
            model = Reading if method == "list_history" else DiaryEntry
            cursor = str(data.cursor.id) if data.cursor else None
            if data.cursor:
                boundary = await owned(session, model, user.id, data.cursor.id)
                if boundary.created_at != data.cursor.created_at:
                    raise AppError("VALIDATION_ERROR")
            rows, next_id = await history(session, user.id, model, cursor, data.limit, data.kind)
            items = []
            for row in rows:
                item = {"id": str(row.id), "created_at": row.created_at.isoformat()}
                item.update({"kind": row.kind, "status": row.status} if model is Reading else {"text": row.text, "mood": row.mood, "reading_id": str(row.reading_id) if row.reading_id else None})
                items.append(item)
            return {"items": items, "next_cursor": {"created_at": rows[-1].created_at.isoformat(), "id": next_id} if next_id else None}
        elif method == "finish_onboarding":
            data = Onboarding.model_validate(values)
            user = await actor_profile(session, actor_id)
            if user is None or not user.fsm_state or not user.fsm_state.startswith("onboard"):
                raise AppError("CONSENT_REQUIRED")
            await finish_onboarding(session, user, data)
            return {"user_id": str(user.id), "menu": "main"}
        elif method == "save_reminder_settings":
            data = ReminderInput.model_validate(values)
            user = await owner(session, actor_id, lock=True)
            save_reminder_settings(user, data.enabled, data.time)
            return {"enabled": user.reminders_enabled, "time": user.reminder_time.strftime("%H:%M")}
        elif method == "delete_profile":
            data = DeleteInput.model_validate(values)
            user = await owner(session, actor_id, lock=True)
            await delete_profile(session, user, data.confirmation_token)
            return {"deleted": True}
        else:
            raise AppError("NOT_FOUND")
        cards = await cards_for(session, record.id)
        return {"reading_id": str(record.id), "status": record.status, "cards": [{"position": c["position"], "card_id": c["id"], "position_label": c["label"], "name": c["name"]} for c in cards], **(record.result_json or {})}


def require_data(response):
    if not response.ok:
        raise AppError(response.error.code, response.error.message)
    return response.data
