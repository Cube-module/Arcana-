from datetime import datetime, date, time, timezone
from uuid import UUID, uuid4
from sqlalchemy import BigInteger, String, Text, SmallInteger, Integer, Boolean, Date, Time, DateTime, ForeignKey, ForeignKeyConstraint, CheckConstraint, UniqueConstraint, Index, text as sql_text
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB, ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=uuid4)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    display_name: Mapped[str] = mapped_column(String(40))
    interests: Mapped[list] = mapped_column(ARRAY(Text))
    timezone: Mapped[str] = mapped_column(String(64))
    consent_version: Mapped[str] = mapped_column(String(20), default="1.0")
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reminder_time: Mapped[time] = mapped_column(Time, default=time(9))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fsm_state: Mapped[str | None] = mapped_column(String(40))
    fsm_payload: Mapped[dict | None] = mapped_column(JSONB)
    fsm_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("length(display_name) BETWEEN 1 AND 40"), CheckConstraint("cardinality(interests) BETWEEN 1 AND 4"))


class Card(Base):
    __tablename__ = "cards"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(Text)
    meaning: Mapped[str] = mapped_column(Text)
    image_path: Mapped[str] = mapped_column(Text)
    telegram_file_id: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("id BETWEEN 0 AND 77"), CheckConstraint("length(meaning)>0"))


class Reading(Base):
    __tablename__ = "readings"
    id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(10))
    spread_type: Mapped[str] = mapped_column(String(10))
    topic: Mapped[str | None] = mapped_column(String(30))
    question: Mapped[str | None] = mapped_column(String(1000))
    option_a: Mapped[str | None] = mapped_column(String(200))
    option_b: Mapped[str | None] = mapped_column(String(200))
    local_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="queued")
    result_json: Mapped[dict | None] = mapped_column(JSONB)
    memory_ids: Mapped[list] = mapped_column(JSONB, default=list)
    model_name: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_key: Mapped[str] = mapped_column(String(200), unique=True)
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        CheckConstraint("kind IN ('question','day')"),
        CheckConstraint("spread_type IN ('one','three','choice')"),
        CheckConstraint("status IN ('queued','running','ready','fallback','failed')"),
        CheckConstraint("(kind='question' AND question IS NOT NULL AND length(question)>0 AND topic IS NOT NULL AND topic IN ('relationships','work','study','self_development')) OR (kind='day' AND question IS NULL AND topic IS NULL AND spread_type='one')"),
        CheckConstraint("(spread_type='choice' AND option_a IS NOT NULL AND option_b IS NOT NULL AND length(option_a) BETWEEN 1 AND 200 AND length(option_b) BETWEEN 1 AND 200) OR (spread_type<>'choice' AND option_a IS NULL AND option_b IS NULL)"),
        CheckConstraint("status NOT IN ('ready','fallback') OR (result_json IS NOT NULL AND completed_at IS NOT NULL)"),
        Index("uq_day", "user_id", "local_date", unique=True, postgresql_where=sql_text("kind='day'")),
        Index("ix_readings_history", "user_id", sql_text("created_at DESC"), sql_text("id DESC")),
    )


class ReadingCard(Base):
    __tablename__ = "reading_cards"
    reading_id: Mapped[UUID] = mapped_column(ForeignKey("readings.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"))
    position_label: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("reading_id", "card_id"), CheckConstraint("position BETWEEN 1 AND 3"))


class DiaryEntry(Base):
    __tablename__ = "diary_entries"
    id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    reading_id: Mapped[UUID | None] = mapped_column(PGUUID)
    text: Mapped[str] = mapped_column(String(2000))
    mood: Mapped[int | None] = mapped_column(SmallInteger)
    request_key: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        ForeignKeyConstraint(["reading_id", "user_id"], ["readings.id", "readings.user_id"], ondelete="CASCADE"),
        CheckConstraint("mood IS NULL OR mood BETWEEN 1 AND 5"), CheckConstraint("length(text) BETWEEN 1 AND 2000"),
        Index("ix_diary_history", "user_id", sql_text("created_at DESC"), sql_text("id DESC")),
    )


class MonthlyReport(Base):
    __tablename__ = "monthly_reports"
    id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    month: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="queued")
    statistics_json: Mapped[dict] = mapped_column(JSONB)
    result_json: Mapped[dict | None] = mapped_column(JSONB)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("user_id", "month"), UniqueConstraint("id", "user_id"),
        CheckConstraint("EXTRACT(DAY FROM month)=1"),
        CheckConstraint("status IN ('queued','running','ready','fallback','empty','failed')"),
        CheckConstraint("status NOT IN ('ready','fallback','empty') OR (result_json IS NOT NULL AND completed_at IS NOT NULL)"),
    )


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[UUID] = mapped_column(PGUUID, primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    reading_id: Mapped[UUID | None] = mapped_column(PGUUID)
    report_id: Mapped[UUID | None] = mapped_column(PGUUID)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    state: Mapped[str] = mapped_column(String(12), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        ForeignKeyConstraint(["reading_id", "user_id"], ["readings.id", "readings.user_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["report_id", "user_id"], ["monthly_reports.id", "monthly_reports.user_id"], ondelete="CASCADE"),
        CheckConstraint("state IN ('pending','running','done','failed','skipped')"),
        CheckConstraint("attempts>=0"),
        CheckConstraint("(kind='update' AND reading_id IS NULL AND report_id IS NULL) OR (kind='llm_reading' AND user_id IS NOT NULL AND reading_id IS NOT NULL AND report_id IS NULL) OR (kind='llm_report' AND user_id IS NOT NULL AND report_id IS NOT NULL AND reading_id IS NULL) OR (kind='delivery' AND user_id IS NOT NULL AND NOT (reading_id IS NOT NULL AND report_id IS NOT NULL))"),
        Index("ix_jobs_queue", "state", "available_at"),
    )
