from sqlalchemy import select, func
from app.config import CONFIG
from app.models.orm import DiaryEntry, Reading
from app.models.dto import DiaryInput
from app.repositories.records import owner, owned
from app.errors import AppError


class DiaryService:
    async def add_diary_entry(self, session, actor_id, data: DiaryInput):
        user = await owner(session, actor_id, lock=True)
        row = await session.scalar(select(DiaryEntry).where(DiaryEntry.user_id == user.id, DiaryEntry.request_key == data.request_key))
        if row:
            return row
        if data.reading_id:
            await owned(session, Reading, user.id, data.reading_id)
        if await session.scalar(select(func.count()).select_from(DiaryEntry).where(DiaryEntry.user_id == user.id)) >= CONFIG["storage_limit"]:
            raise AppError("STORAGE_LIMIT")
        row = DiaryEntry(user_id=user.id, **data.model_dump())
        session.add(row)
        await session.flush()
        return row
