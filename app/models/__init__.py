import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON, BIGINT

from app.database import Base

server_version_seq = sa.Sequence("server_version_seq")


def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = sa.Column(UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    qf_user_id = sa.Column(sa.String, unique=True, nullable=False)
    email = sa.Column(sa.String, nullable=False)
    first_name = sa.Column(sa.String, nullable=True)
    last_name = sa.Column(sa.String, nullable=True)
    qf_access_token = sa.Column(sa.String, nullable=True)
    qf_refresh_token = sa.Column(sa.String, nullable=True)
    token_expires_at = sa.Column(sa.DateTime(timezone=True), nullable=True)
    cached_streak_count = sa.Column(sa.Integer, nullable=True)
    cached_streak_synced_at = sa.Column(sa.DateTime(timezone=True), nullable=True)
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = sa.Column(sa.DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class OAuthState(Base):
    __tablename__ = "oauth_state"

    id = sa.Column(UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    state = sa.Column(sa.String, unique=True, nullable=False)
    code_verifier = sa.Column(sa.String, nullable=False)
    expires_at = sa.Column(sa.DateTime(timezone=True), nullable=False)


class ExchangeCode(Base):
    __tablename__ = "exchange_codes"

    id = sa.Column(UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False)
    code = sa.Column(sa.String, unique=True, nullable=False)
    used = sa.Column(sa.Boolean, nullable=False, default=False)
    expires_at = sa.Column(sa.DateTime(timezone=True), nullable=False)


class CachedVerse(Base):
    __tablename__ = "cached_verses"

    verse_key = sa.Column(sa.String, primary_key=True)
    recitation_id = sa.Column(sa.Integer, primary_key=True)
    arabic_text = sa.Column(sa.Text, nullable=False)
    audio_url = sa.Column(sa.String, nullable=True)
    cached_at = sa.Column(sa.DateTime(timezone=True), nullable=False, default=_utcnow)


class DailyVerse(Base):
    __tablename__ = "daily_verse"

    date = sa.Column(sa.Date, primary_key=True)
    verse_key = sa.Column(sa.String, nullable=False)
    arabic_text = sa.Column(sa.Text, nullable=False)
    chapter_id = sa.Column(sa.Integer, nullable=False)
    verse_number = sa.Column(sa.Integer, nullable=False)
    juz_number = sa.Column(sa.Integer, nullable=True)
    page_number = sa.Column(sa.Integer, nullable=True)
    translation_text = sa.Column(sa.Text, nullable=True)
    translation_resource_id = sa.Column(sa.Integer, nullable=True)
    requested_translation_id = sa.Column(sa.Integer, nullable=True)
    fetched_at = sa.Column(sa.DateTime(timezone=True), nullable=False, default=_utcnow)


class Deck(Base):
    __tablename__ = "decks"

    id = sa.Column(sa.String, primary_key=True)
    user_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False)
    name = sa.Column(sa.String, nullable=False)
    range_start = sa.Column(sa.String, nullable=False)
    range_end = sa.Column(sa.String, nullable=False)
    recitation_id = sa.Column(sa.Integer, nullable=False)
    is_deleted = sa.Column(sa.Boolean, nullable=False, server_default=sa.text("false"))
    server_version = sa.Column(sa.BigInteger, server_default=server_version_seq.next_value(), nullable=False)
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = sa.Column(sa.DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class Card(Base):
    __tablename__ = "cards"

    id = sa.Column(sa.String, primary_key=True)
    user_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False)
    deck_id = sa.Column(sa.String, sa.ForeignKey("decks.id"), nullable=False)
    verse_key = sa.Column(sa.String, nullable=False)
    stability = sa.Column(sa.Float, nullable=False)
    difficulty = sa.Column(sa.Float, nullable=False)
    reps = sa.Column(sa.Integer, nullable=False)
    lapses = sa.Column(sa.Integer, nullable=False)
    state = sa.Column(sa.String, nullable=False)
    due_date = sa.Column(sa.DateTime(timezone=True), nullable=False)
    is_deleted = sa.Column(sa.Boolean, nullable=False, server_default=sa.text("false"))
    server_version = sa.Column(sa.BigInteger, server_default=server_version_seq.next_value(), nullable=False)
    updated_at = sa.Column(sa.DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class ReviewLog(Base):
    __tablename__ = "review_logs"

    id = sa.Column(sa.String, primary_key=True)
    user_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False)
    card_id = sa.Column(sa.String, sa.ForeignKey("cards.id"), nullable=False)
    grade = sa.Column(sa.Integer, nullable=False)
    elapsed_days = sa.Column(sa.Integer, nullable=False)
    scheduled_days = sa.Column(sa.Integer, nullable=False)
    reviewed_at = sa.Column(sa.DateTime(timezone=True), nullable=False)
    is_deleted = sa.Column(sa.Boolean, nullable=False, server_default=sa.text("false"))
    server_version = sa.Column(sa.BigInteger, server_default=server_version_seq.next_value(), nullable=False)
    updated_at = sa.Column(sa.DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class Preference(Base):
    __tablename__ = "preferences"

    id = sa.Column(sa.String, primary_key=True)
    user_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False)
    default_recitation_id = sa.Column(sa.Integer, nullable=False)
    script_type = sa.Column(sa.String, nullable=False)
    mushaf_id = sa.Column(sa.Integer, nullable=True)
    timezone = sa.Column(sa.String, nullable=True)
    is_deleted = sa.Column(sa.Boolean, nullable=False, server_default=sa.text("false"))
    server_version = sa.Column(sa.BigInteger, server_default=server_version_seq.next_value(), nullable=False)
    updated_at = sa.Column(sa.DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class SyncState(Base):
    __tablename__ = "sync_state"

    user_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True)
    last_pushed_at = sa.Column(sa.DateTime(timezone=True), nullable=True)


class BridgeOutbox(Base):
    __tablename__ = "bridge_outbox"

    id = sa.Column(UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False)
    event_type = sa.Column(sa.String, nullable=False)
    dedupe_key = sa.Column(sa.String, unique=True, nullable=False)
    payload = sa.Column(JSON, nullable=False)
    status = sa.Column(sa.String, nullable=False, default="pending")
    retry_count = sa.Column(sa.Integer, nullable=False, default=0)
    next_retry_at = sa.Column(sa.DateTime(timezone=True), nullable=True)
    processing_at = sa.Column(sa.DateTime(timezone=True), nullable=True)
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, default=_utcnow)


class Goal(Base):
    __tablename__ = "goals"

    id = sa.Column(UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False)
    qf_goal_id = sa.Column(sa.String, nullable=True)
    range_start = sa.Column(sa.String, nullable=False)
    range_end = sa.Column(sa.String, nullable=False)
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = sa.Column(sa.DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = sa.Column(UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    user_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False)
    token = sa.Column(sa.String, unique=True, nullable=False, index=True)
    expires_at = sa.Column(sa.DateTime(timezone=True), nullable=False)
    revoked = sa.Column(sa.Boolean, nullable=False, default=False)
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, default=_utcnow)
