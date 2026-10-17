from datetime import datetime, timedelta, timezone, time, date
from zoneinfo import ZoneInfo
from app.errors import AppError


def local_day(now, zone):
    return now.astimezone(ZoneInfo(zone)).date()


def period(month, zone):
    start = datetime.combine(month.replace(day=1), time(), ZoneInfo(zone))
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def previous_month(now, zone):
    return (local_day(now, zone).replace(day=1) - timedelta(days=1)).replace(day=1)


def report_month(now, zone, value=None):
    try:
        month = date.fromisoformat(value + "-01") if value else previous_month(now, zone)
    except ValueError:
        raise AppError("VALIDATION_ERROR") from None
    if month >= local_day(now, zone).replace(day=1):
        raise AppError("VALIDATION_ERROR", "Выбери завершённый календарный месяц.")
    return month


def reminder_due(now, zone, at, hours=2):
    local = now.astimezone(ZoneInfo(zone))
    scheduled = datetime.combine(local.date(), at, ZoneInfo(zone))
    elapsed = (now - scheduled.astimezone(timezone.utc)).total_seconds()
    return 0 <= elapsed <= hours * 3600
