import json
from datetime import timedelta
from sqlalchemy import select
from app.models.orm import Reading, DiaryEntry, utcnow


class MemoryService:
    async def collect(self, session, reading, now=None):
        now = now or utcnow()
        readings = list((await session.scalars(select(Reading).where(Reading.user_id == reading.user_id, Reading.id != reading.id, Reading.topic == reading.topic, Reading.status.in_(["ready", "fallback"]), Reading.created_at >= now - timedelta(days=90)).order_by(Reading.created_at.desc(), Reading.id.desc()).limit(5))).all())
        linked = list((await session.scalars(select(DiaryEntry).where(DiaryEntry.user_id == reading.user_id, DiaryEntry.reading_id.in_([r.id for r in readings])).order_by(DiaryEntry.created_at.desc(), DiaryEntry.id.desc()).limit(5))).all())
        standalone = list((await session.scalars(select(DiaryEntry).where(DiaryEntry.user_id == reading.user_id, DiaryEntry.reading_id.is_(None)).order_by(DiaryEntry.created_at.desc(), DiaryEntry.id.desc()).limit(5 - len(linked)))).all())
        items = [(r.created_at, {"id": f"reading:{r.id}", "question": r.question, "interpretation": (r.result_json or {}).get("interpretation", "")}) for r in readings]
        items += [(d.created_at, {"id": f"diary:{d.id}", "text": d.text}) for d in linked + standalone]
        items.sort(key=lambda x: (x[0], x[1]["id"]), reverse=True)
        while items and len(json.dumps([x[1] for x in items], ensure_ascii=False)) > 6000:
            items.pop()
        return [x[1] for x in items]
