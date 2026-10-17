from typing import Literal, Annotated
from uuid import UUID
import re
import unicodedata
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictBool, field_validator, model_validator
from datetime import datetime
from app.config import CONFIG

Topic = Literal["relationships", "work", "study", "self_development"]
Spread = Literal["one", "three", "choice"]
POSITIONS = {"one": ["Фокус"], "three": ["Ситуация", "Ресурс", "Следующий шаг"], "choice": ["Вариант А", "Вариант Б", "Что учесть"]}


def normalize(value, maximum, diary=False):
    if not isinstance(value, str):
        raise ValueError("Ожидается текст")
    value = unicodedata.normalize("NFC", value).strip().replace("\r\n", "\n")
    if any(unicodedata.category(c).startswith("C") and c not in "\t\n\r" for c in value):
        raise ValueError("Управляющие символы запрещены")
    value = re.sub(r"[^\S\n]+" if diary else r"\s+", " ", value)
    if not 1 <= len(value) <= maximum:
        raise ValueError(f"Нужно от 1 до {maximum} символов")
    return value


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Onboarding(DTO):
    display_name: str
    interests: list[Topic] = Field(min_length=1, max_length=4)
    timezone: str
    consent: Literal[True]

    @field_validator("display_name", mode="before")
    @classmethod
    def name(cls, v):
        return normalize(v, CONFIG["input"]["name"])

    @model_validator(mode="after")
    def valid(self):
        if len(set(self.interests)) != len(self.interests) or self.timezone not in CONFIG["timezones"]:
            raise ValueError("Выбери интересы и поддерживаемый часовой пояс")
        return self


class CreateReading(DTO):
    request_key: str = Field(min_length=1, max_length=200)
    question: str
    topic: Topic
    spread_type: Spread
    option_a: str | None = None
    option_b: str | None = None

    @field_validator("question", mode="before")
    @classmethod
    def question_text(cls, v):
        return normalize(v, CONFIG["input"]["question"])

    @field_validator("option_a", "option_b", mode="before")
    @classmethod
    def option(cls, v):
        return None if v is None else normalize(v, CONFIG["input"]["option"])

    @model_validator(mode="after")
    def options(self):
        if self.spread_type == "choice":
            if self.option_a is None or self.option_b is None:
                raise ValueError("Для выбора нужны оба варианта А и Б")
        elif self.option_a is not None or self.option_b is not None:
            raise ValueError("Варианты нужны только для расклада Выбор")
        return self


class DiaryInput(DTO):
    request_key: str = Field(min_length=1, max_length=200)
    text: str
    mood: Annotated[StrictInt, Field(ge=1, le=5)] | None = None
    reading_id: UUID | None = None

    @field_validator("text", mode="before")
    @classmethod
    def text_value(cls, v):
        return normalize(v, CONFIG["input"]["diary"], diary=True)


class ReadingResult(DTO):
    interpretation: str = Field(min_length=1, max_length=2700)
    reflection_question: str = Field(min_length=1, max_length=250)
    memory_refs: list[str] = Field(max_length=10)


class MonthlyResult(DTO):
    observations: list[str] = Field(min_length=1, max_length=5)
    reflection_questions: list[str] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def length(self):
        if any(not x.strip() for x in self.observations + self.reflection_questions) or sum(map(len, self.observations + self.reflection_questions)) > 4200:
            raise ValueError("Invalid report length")
        return self


class HistoryCursor(DTO):
    created_at: datetime
    id: UUID


class HistoryInput(DTO):
    cursor: HistoryCursor | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=10)] = 10
    kind: Literal["question", "day"] | None = None


class ReadingInput(DTO):
    reading_id: UUID


class MonthInput(DTO):
    month: str | None = None


class ReminderInput(DTO):
    enabled: StrictBool
    time: str


class DeleteInput(DTO):
    confirmation_token: str


class ApiError(DTO):
    code: str
    message: str
    field: str | None = None
    retryable: bool = False


class ApiResponse(DTO):
    ok: bool
    data: dict | None = None
    error: ApiError | None = None
    request_id: str

    @model_validator(mode="after")
    def exclusive(self):
        if (self.ok and (self.data is None or self.error is not None)) or (not self.ok and (self.error is None or self.data is not None)):
            raise ValueError("Exactly one of data/error is required")
        return self
