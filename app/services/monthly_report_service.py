from sqlalchemy import select
from app.models.orm import MonthlyReport, Job, utcnow
from app.repositories.records import owner, check_generation
from app.repositories.statistics import monthly_statistics
from app.services.time_service import report_month


class MonthlyReportService:
    async def get_monthly_report(self, session, actor_id, month=None, now=None):
        now = now or utcnow()
        user = await owner(session, actor_id, lock=True)
        month = report_month(now, user.timezone, month)
        report = await session.scalar(select(MonthlyReport).where(MonthlyReport.user_id == user.id, MonthlyReport.month == month))
        if report:
            return report
        stats, notes = await monthly_statistics(session, user.id, month, user.timezone)
        count = stats["question_count"] + stats["day_count"] + stats["diary_count"]
        if count >= 3:
            await check_generation(session, user.id)
        report = MonthlyReport(user_id=user.id, month=month, statistics_json=stats, created_at=now)
        if count < 3:
            report.status = "empty" if count == 0 else "ready"
            report.result_json = {"observations": [], "reflection_questions": []}
            report.completed_at = now
        session.add(report)
        await session.flush()
        if count >= 3:
            session.add(Job(user_id=user.id, report_id=report.id, kind="llm_report", dedupe_key=f"llm:{report.id}", payload={"notes": notes}, created_at=now))
        else:
            session.add(Job(user_id=user.id, report_id=report.id, kind="delivery", dedupe_key=f"result:{report.id}"))
        return report
