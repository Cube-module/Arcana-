from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, func
from app.models.orm import Reading, ReadingCard, DiaryEntry, Card
from app.services.time_service import period


async def monthly_statistics(session, user_id, month, zone):
    start, end = period(month, zone)
    reading_filter = (Reading.user_id == user_id, Reading.status.in_(["ready", "fallback"]), Reading.created_at >= start, Reading.created_at < end)
    diary_filter = (DiaryEntry.user_id == user_id, DiaryEntry.created_at >= start, DiaryEntry.created_at < end)
    counts = dict((await session.execute(select(Reading.kind, func.count()).where(*reading_filter).group_by(Reading.kind))).all())
    entries = await session.scalar(select(func.count()).select_from(DiaryEntry).where(*diary_filter))
    topics = (await session.execute(select(Reading.topic, func.count().label("n")).where(*reading_filter, Reading.kind == "question").group_by(Reading.topic).order_by(func.count().desc(), Reading.topic).limit(4))).all()
    cards = (await session.execute(select(Card.name, func.count().label("n")).select_from(Reading).join(ReadingCard).join(Card).where(*reading_filter).group_by(Card.id, Card.name).order_by(func.count().desc(), Card.id).limit(5))).all()
    middle = datetime.combine(month.replace(day=16), time(), ZoneInfo(zone)).astimezone(timezone.utc)
    moods = []
    for lo, hi in ((start, middle), (middle, end)):
        n, avg = (await session.execute(select(func.count(DiaryEntry.mood), func.avg(DiaryEntry.mood)).where(*diary_filter, DiaryEntry.created_at >= lo, DiaryEntry.created_at < hi))).one()
        moods.append({"count": n, "average": round(float(avg), 1) if avg is not None else None})
    # Ordered short snippets sampled at evenly spaced ranks over the whole month.
    notes = (await session.execute(select(DiaryEntry.id, func.left(DiaryEntry.text, 400)).where(*diary_filter).order_by(DiaryEntry.created_at, DiaryEntry.id))).all()
    if len(notes) > 20:
        notes = [notes[round(i * (len(notes) - 1) / 19)] for i in range(20)]
    selected = [{"id": f"diary:{id}", "text": value} for id, value in notes]
    return {"question_count": counts.get("question", 0), "day_count": counts.get("day", 0), "diary_count": entries, "topics": [list(x) for x in topics], "cards": [list(x) for x in cards], "mood": moods}, selected
