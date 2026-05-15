from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")


def _validate_iana_timezone(v: str | None) -> str | None:
    if v is None:
        return None
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Invalid IANA timezone: '{v}'")
    return v


class ExchangeRequest(BaseModel):
    code: str


class ExchangeResponse(BaseModel):
    access_token: str
    refresh_token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str


class UserProfile(BaseModel):
    id: UUID
    email: str
    first_name: str | None = None
    last_name: str | None = None
    cached_streak_count: int | None = None
    cached_streak_synced_at: datetime | None = None

    model_config = {"from_attributes": True}


class VerseOut(BaseModel):
    verse_key: str
    arabic_text: str
    audio_url: str | None

    model_config = {"from_attributes": True}


class VersesResponse(BaseModel):
    verses: list[VerseOut]


class ChapterOut(BaseModel):
    id: int
    name_simple: str
    name_arabic: str
    verses_count: int

    model_config = {"from_attributes": True}


class ReciterOut(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class MetadataResponse(BaseModel):
    chapters: list[ChapterOut]
    reciters: list[ReciterOut]


class DeckSync(BaseModel):
    id: str
    name: str
    range_start: str
    range_end: str
    recitation_id: int
    start_surah_name: str | None = None
    end_surah_name: str | None = None
    is_deleted: bool
    server_version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CardSync(BaseModel):
    id: str
    deck_id: str
    verse_key: str
    stability: float
    difficulty: float
    reps: int
    lapses: int
    state: str
    due_date: datetime
    is_deleted: bool
    server_version: int
    updated_at: datetime
    arabic_text: str | None = None
    audio_url: str | None = None
    answer_verses: str | None = None

    model_config = {"from_attributes": True}


class ReviewLogSync(BaseModel):
    id: str
    card_id: str
    grade: int
    elapsed_days: int
    scheduled_days: int
    reviewed_at: datetime
    is_deleted: bool
    server_version: int
    updated_at: datetime

    model_config = {"from_attributes": True}


class PreferenceSync(BaseModel):
    id: str
    default_recitation_id: int
    script_type: str
    mushaf_id: int | None
    timezone: str | None
    is_deleted: bool
    server_version: int
    updated_at: datetime

    model_config = {"from_attributes": True}


class TableChanges(BaseModel, Generic[T]):
    created: list[T]
    updated: list[T]
    deleted: list[str]


class PullChanges(BaseModel):
    decks: TableChanges[DeckSync]
    cards: TableChanges[CardSync]
    review_logs: TableChanges[ReviewLogSync]
    preferences: TableChanges[PreferenceSync]


class PullResponse(BaseModel):
    changes: PullChanges
    timestamp: int


class DeckPush(BaseModel):
    id: str
    name: str
    range_start: str
    range_end: str
    recitation_id: int
    start_surah_name: str | None = None
    end_surah_name: str | None = None


class CardPush(BaseModel):
    id: str
    deck_id: str
    verse_key: str
    stability: float
    difficulty: float
    reps: int
    lapses: int
    state: str
    due_date: datetime
    arabic_text: str | None = None
    audio_url: str | None = None
    answer_verses: str | None = None


class ReviewLogPush(BaseModel):
    id: str
    card_id: str
    grade: int
    elapsed_days: int
    scheduled_days: int
    reviewed_at: datetime


class PreferencePush(BaseModel):
    id: str
    default_recitation_id: int
    script_type: str
    mushaf_id: int | None = None
    timezone: str | None = None

    @field_validator("timezone", mode="before")
    @classmethod
    def validate_timezone(cls, v: str | None) -> str | None:
        return _validate_iana_timezone(v)


class PushTableChanges(BaseModel, Generic[T]):
    created: list[T]
    updated: list[T]
    deleted: list[str]


class PushChanges(BaseModel):
    decks: PushTableChanges[DeckPush] = Field(default_factory=lambda: PushTableChanges(created=[], updated=[], deleted=[]))
    cards: PushTableChanges[CardPush] = Field(default_factory=lambda: PushTableChanges(created=[], updated=[], deleted=[]))
    review_logs: PushTableChanges[ReviewLogPush] = Field(default_factory=lambda: PushTableChanges(created=[], updated=[], deleted=[]))
    preferences: PushTableChanges[PreferencePush] = Field(default_factory=lambda: PushTableChanges(created=[], updated=[], deleted=[]))


class PushRequest(BaseModel):
    changes: PushChanges
    lastPulledAt: int = 0


class GoalCreate(BaseModel):
    range_start: str
    range_end: str
    mushaf_id: int | None = None
    timezone: str | None = None

    @field_validator("timezone", mode="before")
    @classmethod
    def validate_timezone(cls, v: str | None) -> str | None:
        return _validate_iana_timezone(v)


class GoalUpdate(BaseModel):
    range_start: str
    range_end: str
    mushaf_id: int | None = None
    timezone: str | None = None

    @field_validator("timezone", mode="before")
    @classmethod
    def validate_timezone(cls, v: str | None) -> str | None:
        return _validate_iana_timezone(v)


class GoalOut(BaseModel):
    id: UUID
    user_id: UUID
    qf_goal_id: str | None
    range_start: str
    range_end: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DailyVerseOut(BaseModel):
    verse_key: str
    arabic_text: str
    chapter_id: int
    verse_number: int
    juz_number: int | None = None
    page_number: int | None = None
    translation_text: str | None = None
    tafsir_url: str | None = None

    model_config = {"from_attributes": True}
