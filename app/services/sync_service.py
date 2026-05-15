import hashlib
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import SURAH_NAMES
from app.models import (
    BridgeOutbox,
    Card,
    Deck,
    Preference,
    ReviewLog,
    SyncState,
    server_version_seq,
)
from app.schemas import (
    CardSync,
    DeckSync,
    PreferenceSync,
    PullChanges,
    PullResponse,
    PushRequest,
    ReviewLogSync,
    TableChanges,
)


async def pull(db: AsyncSession, user_id: UUID, last_pulled_at: int) -> PullResponse:
    cursor_result = await db.execute(sa.text("SELECT nextval('server_version_seq')"))
    cursor_val = cursor_result.scalar()
    if cursor_val is None:
        raise HTTPException(status_code=500, detail="Failed to obtain sync cursor")
    cursor: int = int(cursor_val)

    models = [Deck, Card, ReviewLog, Preference]
    sync_schemas = [DeckSync, CardSync, ReviewLogSync, PreferenceSync]
    schema_map: dict[str, type] = {
        "decks": DeckSync,
        "cards": CardSync,
        "review_logs": ReviewLogSync,
        "preferences": PreferenceSync,
    }
    table_names = ["decks", "cards", "review_logs", "preferences"]
    changes: dict = {}

    for table_name, model, schema in zip(table_names, models, sync_schemas):
        stmt = (
            select(model)
            .where(
                model.user_id == user_id,
                model.server_version > last_pulled_at,
                model.server_version <= cursor,
            )
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()

        created: list = []
        updated: list = []
        deleted: list[str] = []

        for row in rows:
            if row.is_deleted:
                deleted.append(row.id)
            else:
                updated.append(schema.model_validate(row))

        changes[table_name] = TableChanges[schema_map[table_name]](
            created=created, updated=updated, deleted=deleted
        )

    pull_changes = PullChanges(
        decks=changes["decks"],
        cards=changes["cards"],
        review_logs=changes["review_logs"],
        preferences=changes["preferences"],
    )
    return PullResponse(changes=pull_changes, timestamp=cursor)


async def push(db: AsyncSession, user_id: UUID, push_request: PushRequest) -> list[UUID]:
    now = datetime.now(timezone.utc)
    changes = push_request.changes

    await _verify_ownership(db, user_id, changes)
    await _verify_child_refs(db, user_id, changes)

    await _upsert_decks(db, user_id, changes.decks, now)
    await _upsert_cards(db, user_id, changes.cards, now)
    rl_batch = await _upsert_review_logs(db, user_id, changes.review_logs, now)
    await _upsert_preferences(db, user_id, changes.preferences, now)

    new_outbox_ids = await _write_outbox_entries(db, user_id, rl_batch, now)

    stmt = (
        pg_insert(SyncState)
        .values(user_id=user_id, last_pushed_at=now)
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_={"last_pushed_at": now},
        )
    )
    await db.execute(stmt)

    await db.commit()

    return new_outbox_ids


async def _verify_ownership(db: AsyncSession, user_id: UUID, changes) -> None:
    table_configs = [
        (Deck, changes.decks, "decks"),
        (Card, changes.cards, "cards"),
        (ReviewLog, changes.review_logs, "review_logs"),
        (Preference, changes.preferences, "preferences"),
    ]

    for model, table_changes, label in table_configs:
        all_ids = {item.id for item in table_changes.created + table_changes.updated}
        all_ids.update(table_changes.deleted)
        if not all_ids:
            continue

        result = await db.execute(
            select(model.id).where(
                model.id.in_(all_ids),
                model.user_id != user_id,
            )
        )
        foreign_ids = result.scalars().all()
        if foreign_ids:
            raise HTTPException(
                status_code=403,
                detail=f"Cannot modify {label} records owned by another user",
            )


async def _verify_child_refs(db: AsyncSession, user_id: UUID, changes) -> None:
    card_deck_ids = {item.deck_id for item in changes.cards.created + changes.cards.updated}
    if card_deck_ids:
        result = await db.execute(
            select(Deck.id).where(
                Deck.id.in_(card_deck_ids),
                Deck.user_id != user_id,
            )
        )
        foreign = result.scalars().all()
        if foreign:
            raise HTTPException(
                status_code=403,
                detail="Card references deck owned by another user",
            )

        result = await db.execute(
            select(Deck.id).where(Deck.id.in_(card_deck_ids))
        )
        existing = set(result.scalars().all())
        missing = card_deck_ids - existing
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Referenced decks do not exist: {', '.join(sorted(missing))}",
            )

    rl_card_ids = {item.card_id for item in changes.review_logs.created + changes.review_logs.updated}
    if rl_card_ids:
        result = await db.execute(
            select(Card.id).where(
                Card.id.in_(rl_card_ids),
                Card.user_id != user_id,
            )
        )
        foreign = result.scalars().all()
        if foreign:
            raise HTTPException(
                status_code=403,
                detail="ReviewLog references card owned by another user",
            )


async def _upsert_decks(db, user_id, table_changes, now):
    for item in table_changes.created + table_changes.updated:
        data = item.model_dump()
        _validate_verse_key(data["range_start"], "range_start")
        _validate_verse_key(data["range_end"], "range_end")
        start_surah_num = int(data["range_start"].split(":")[0])
        end_surah_num = int(data["range_end"].split(":")[0])
        if data.get("start_surah_name") is None:
            data["start_surah_name"] = SURAH_NAMES.get(start_surah_num)
        if data.get("end_surah_name") is None:
            data["end_surah_name"] = SURAH_NAMES.get(end_surah_num)
        data.update({"user_id": user_id, "is_deleted": False, "updated_at": now})
        stmt = (
            pg_insert(Deck)
            .values(**data)
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": data["name"],
                    "range_start": data["range_start"],
                    "range_end": data["range_end"],
                    "recitation_id": data["recitation_id"],
                    "start_surah_name": data["start_surah_name"],
                    "end_surah_name": data["end_surah_name"],
                    "user_id": user_id,
                    "is_deleted": False,
                    "server_version": server_version_seq.next_value(),
                    "updated_at": now,
                },
                where=(Deck.user_id == user_id),
            )
        )
        await db.execute(stmt)

    for item_id in table_changes.deleted:
        stmt = (
            update(Deck)
            .where(Deck.id == item_id, Deck.user_id == user_id)
            .values(
                is_deleted=True,
                server_version=server_version_seq.next_value(),
                updated_at=now,
            )
        )
        await db.execute(stmt)


async def _upsert_cards(db, user_id, table_changes, now):
    for item in table_changes.created + table_changes.updated:
        data = item.model_dump()
        data.update({"user_id": user_id, "is_deleted": False, "updated_at": now})
        stmt = (
            pg_insert(Card)
            .values(**data)
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "deck_id": data["deck_id"],
                    "verse_key": data["verse_key"],
                    "stability": data["stability"],
                    "difficulty": data["difficulty"],
                    "reps": data["reps"],
                    "lapses": data["lapses"],
                    "state": data["state"],
                    "due_date": data["due_date"],
                    "arabic_text": data["arabic_text"],
                    "audio_url": data["audio_url"],
                    "answer_verses": data["answer_verses"],
                    "user_id": user_id,
                    "is_deleted": False,
                    "server_version": server_version_seq.next_value(),
                    "updated_at": now,
                },
                where=(Card.user_id == user_id),
            )
        )
        await db.execute(stmt)

    for item_id in table_changes.deleted:
        stmt = (
            update(Card)
            .where(Card.id == item_id, Card.user_id == user_id)
            .values(
                is_deleted=True,
                server_version=server_version_seq.next_value(),
                updated_at=now,
            )
        )
        await db.execute(stmt)


async def _upsert_review_logs(db, user_id, table_changes, now) -> list[dict]:
    rl_items = []

    for item in table_changes.created + table_changes.updated:
        data = item.model_dump()
        data.update({"user_id": user_id, "is_deleted": False, "updated_at": now})
        stmt = (
            pg_insert(ReviewLog)
            .values(**data)
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "card_id": data["card_id"],
                    "grade": data["grade"],
                    "elapsed_days": data["elapsed_days"],
                    "scheduled_days": data["scheduled_days"],
                    "reviewed_at": data["reviewed_at"],
                    "user_id": user_id,
                    "is_deleted": False,
                    "server_version": server_version_seq.next_value(),
                    "updated_at": now,
                },
                where=(ReviewLog.user_id == user_id),
            )
        )
        await db.execute(stmt)
        rl_items.append(data)

    for item_id in table_changes.deleted:
        stmt = (
            update(ReviewLog)
            .where(ReviewLog.id == item_id, ReviewLog.user_id == user_id)
            .values(
                is_deleted=True,
                server_version=server_version_seq.next_value(),
                updated_at=now,
            )
        )
        await db.execute(stmt)

    return rl_items


async def _upsert_preferences(db, user_id, table_changes, now):
    for item in table_changes.created + table_changes.updated:
        data = item.model_dump(exclude_unset=True)
        data.update({"user_id": user_id, "is_deleted": False, "updated_at": now})
        set_dict = {
            "default_recitation_id": data["default_recitation_id"],
            "script_type": data["script_type"],
            "user_id": user_id,
            "is_deleted": False,
            "server_version": server_version_seq.next_value(),
            "updated_at": now,
        }
        if "mushaf_id" in data:
            set_dict["mushaf_id"] = data["mushaf_id"]
        if "timezone" in data:
            set_dict["timezone"] = data["timezone"]
        stmt = (
            pg_insert(Preference)
            .values(**data)
            .on_conflict_do_update(
                index_elements=["id"],
                set_=set_dict,
                where=(Preference.user_id == user_id),
            )
        )
        await db.execute(stmt)

    for item_id in table_changes.deleted:
        stmt = (
            update(Preference)
            .where(Preference.id == item_id, Preference.user_id == user_id)
            .values(
                is_deleted=True,
                server_version=server_version_seq.next_value(),
                updated_at=now,
            )
        )
        await db.execute(stmt)


_VERSE_KEY_RE = re.compile(r"^\d+:\d+$")


def _validate_verse_key(value: str, label: str) -> None:
    if not _VERSE_KEY_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {label}: must be in 'surah:ayah' format, e.g. '2:1', got '{value}'",
        )


def _parse_verse_key_parts(vk: str) -> tuple[int, int]:
    m = re.match(r"^(\d+):(\d+)$", vk)
    if m is None:
        return 0, 0
    return int(m.group(1)), int(m.group(2))


async def _write_outbox_entries(
    db: AsyncSession, user_id: UUID, rl_batch: list[dict], now: datetime
) -> list[UUID]:
    if not rl_batch:
        return []

    user_id_str = str(user_id)

    rl_payload = [
        {
            "id": r["id"],
            "card_id": r["card_id"],
            "grade": r["grade"],
            "elapsed_days": r["elapsed_days"],
            "scheduled_days": r["scheduled_days"],
            "reviewed_at": r["reviewed_at"].isoformat()
            if isinstance(r["reviewed_at"], datetime)
            else str(r["reviewed_at"]),
        }
        for r in rl_batch
    ]

    reviewed_ats: list[datetime] = []
    for r in rl_batch:
        ra = r["reviewed_at"]
        if isinstance(ra, datetime):
            reviewed_ats.append(ra)
        else:
            reviewed_ats.append(datetime.fromisoformat(str(ra)))

    card_ids = list({r["card_id"] for r in rl_batch})
    verse_result = await db.execute(
        select(Card.id, Card.verse_key).where(Card.id.in_(card_ids))
    )
    card_verse_map: dict[str, str] = {row[0]: row[1] for row in verse_result.all()}

    verse_keys_for_logs = []
    for r in rl_batch:
        vk = card_verse_map.get(r["card_id"])
        if vk:
            verse_keys_for_logs.append(vk)

    pref_result = await db.execute(
        select(Preference.mushaf_id, Preference.timezone).where(
            Preference.user_id == user_id, Preference.is_deleted == False
        ).limit(1)
    )
    pref_row = pref_result.first()
    pref_mushaf_id = pref_row[0] if pref_row else None
    pref_timezone = pref_row[1] if pref_row else None

    session_start = min(reviewed_ats)
    session_end = max(reviewed_ats)
    duration_seconds = int((session_end - session_start).total_seconds())

    sorted_vks = sorted(verse_keys_for_logs, key=_parse_verse_key_parts)
    last_vk = sorted_vks[-1] if sorted_vks else "1:1"
    last_chapter, last_verse = _parse_verse_key_parts(last_vk)

    sorted_ids = sorted(r["id"] for r in rl_batch)
    id_hash = hashlib.sha256(":".join(sorted_ids).encode()).hexdigest()[:16]

    new_ids: list[UUID] = []

    reading_session_stmt = (
        pg_insert(BridgeOutbox)
        .values(
            user_id=user_id,
            event_type="reading_session",
            dedupe_key=f"{user_id_str}:reading_session:{id_hash}",
            payload={
                "chapter_number": last_chapter,
                "verse_number": last_verse,
                "duration_seconds": duration_seconds,
                "mushaf_id": pref_mushaf_id,
                "review_logs": rl_payload,
                "pushed_at": now.isoformat(),
            },
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(BridgeOutbox.id)
    )
    result = await db.execute(reading_session_stmt)
    reading_id = result.scalar_one_or_none()
    if reading_id is not None:
        new_ids.append(reading_id)

    if pref_mushaf_id is not None and pref_timezone is not None:
        try:
            user_tz = ZoneInfo(pref_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return new_ids

        date_reviews: dict[date, list[dict]] = defaultdict(list)
        for r in rl_batch:
            ra = r["reviewed_at"]
            dt = ra if isinstance(ra, datetime) else datetime.fromisoformat(str(ra))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local_dt = dt.astimezone(user_tz)
            date_reviews[local_dt.date()].append(r)
        activity_dates = sorted(date_reviews.keys())
        latest_activity_date = max(activity_dates)
        for activity_date in activity_dates:
            day_revs = date_reviews[activity_date]
            day_vks = sorted(
                [vk for r in day_revs if (vk := card_verse_map.get(r["card_id"]))],
                key=_parse_verse_key_parts,
            )

            ranges: list[str] = []
            if day_vks:
                range_start = day_vks[0]
                range_end = day_vks[0]
                for vk in day_vks[1:]:
                    c2, v2 = _parse_verse_key_parts(vk)
                    c1, v1 = _parse_verse_key_parts(range_end)
                    if c2 == c1 and v2 == v1 + 1:
                        range_end = vk
                    else:
                        ranges.append(f"{range_start}-{range_end}")
                        range_start = vk
                        range_end = vk
                ranges.append(f"{range_start}-{range_end}")

            day_ats = []
            for r in day_revs:
                ra = r["reviewed_at"]
                day_ats.append(ra if isinstance(ra, datetime) else datetime.fromisoformat(str(ra)))
            day_seconds = int((max(day_ats) - min(day_ats)).total_seconds()) if len(day_ats) > 1 else 1

            activity_day_stmt = (
                pg_insert(BridgeOutbox)
                .values(
                    user_id=user_id,
                    event_type="activity_day",
                    dedupe_key=f"{user_id_str}:activity_day:{activity_date.isoformat()}",
                    payload={
                        "date": activity_date.isoformat(),
                        "type": "QURAN",
                        "seconds": day_seconds,
                        "ranges": ranges,
                        "mushaf_id": pref_mushaf_id,
                        "timezone": pref_timezone,
                    },
                )
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
                .returning(BridgeOutbox.id)
            )
            result = await db.execute(activity_day_stmt)
            activity_id = result.scalar_one_or_none()
            if activity_id is not None:
                new_ids.append(activity_id)

        streak_read_stmt = (
            pg_insert(BridgeOutbox)
            .values(
                user_id=user_id,
                event_type="streak_read",
                dedupe_key=f"{user_id_str}:streak_read:{latest_activity_date.isoformat()}",
                payload={"user_id": user_id_str},
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
            .returning(BridgeOutbox.id)
        )
        result = await db.execute(streak_read_stmt)
        streak_id = result.scalar_one_or_none()
        if streak_id is not None:
            new_ids.append(streak_id)

    return new_ids
