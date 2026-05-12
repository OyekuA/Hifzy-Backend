from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import sqlalchemy as sa
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import BridgeOutbox, User
from app.services import token_service


async def _process_reading_session(
    row: BridgeOutbox, access_token: str, client: httpx.AsyncClient
) -> None:
    body = {
        "chapterNumber": row.payload.get("chapter_number"),
        "verseNumber": row.payload.get("verse_number"),
    }

    response = await client.post(
        f"{settings.qf_user_api_base_url}/auth/v1/reading-sessions",
        json=body,
        headers={
            "x-auth-token": access_token,
            "x-client-id": settings.qf_client_id,
        },
    )
    response.raise_for_status()


async def _process_activity_day(
    row: BridgeOutbox, access_token: str, client: httpx.AsyncClient
) -> None:
    body: dict = {
        "type": row.payload.get("type", "QURAN"),
        "seconds": row.payload.get("seconds", 1),
        "ranges": row.payload.get("ranges", []),
        "mushafId": row.payload.get("mushaf_id"),
    }
    date_val = row.payload.get("date")
    if date_val:
        body["date"] = date_val

    response = await client.post(
        f"{settings.qf_user_api_base_url}/auth/v1/activity-days",
        json=body,
        headers={
            "x-auth-token": access_token,
            "x-client-id": settings.qf_client_id,
            "x-timezone": row.payload.get("timezone"),
        },
    )
    response.raise_for_status()


async def _process_streak_read(
    row: BridgeOutbox,
    access_token: str,
    client: httpx.AsyncClient,
    db: AsyncSession,
    user_id: UUID,
) -> None:
    response = await client.get(
        f"{settings.qf_user_api_base_url}/auth/v1/streaks",
        headers={
            "x-auth-token": access_token,
            "x-client-id": settings.qf_client_id,
        },
    )
    response.raise_for_status()
    data = response.json()

    streaks = data.get("data", [])
    streak_count = 0
    if streaks and isinstance(streaks, list):
        current = streaks[0]
        streak_count = current.get("days", 0)

    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            cached_streak_count=streak_count,
            cached_streak_synced_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()


async def process_outbox_row(db: AsyncSession, row: BridgeOutbox) -> None:
    row_id = UUID(str(row.id))
    user_id: UUID = UUID(str(row.user_id))
    row_payload: dict = dict(row.__dict__.get("payload") or {})
    try:
        access_token = await token_service.get_valid_token(db, user_id)
    except token_service.RefreshTokenExpiredError as e:
        row_payload["error"] = f"Token refresh failed: {e}"
        await db.execute(
            update(BridgeOutbox)
            .where(BridgeOutbox.id == row_id)
            .values(status="failed", processing_at=None, payload=row_payload)
        )
        await db.commit()
        return

    try:
        async with httpx.AsyncClient() as client:
            event_type = str(row.event_type)
            if event_type == "reading_session":
                await _process_reading_session(row, access_token, client)
            elif event_type == "activity_day":
                if not row.payload.get("mushaf_id") or not row.payload.get("timezone"):
                    row_payload["error"] = "Missing mushaf_id or timezone in payload"
                    await db.execute(
                        update(BridgeOutbox)
                        .where(BridgeOutbox.id == row_id)
                        .values(status="failed", processing_at=None, payload=row_payload)
                    )
                    await db.commit()
                    return
                await _process_activity_day(row, access_token, client)
            elif event_type == "streak_read":
                await _process_streak_read(
                    row, access_token, client, db, user_id
                )

        await db.execute(
            update(BridgeOutbox)
            .where(BridgeOutbox.id == row_id)
            .values(status="done", processing_at=None)
        )
        await db.commit()
    except httpx.HTTPStatusError as e:
        if 400 <= e.response.status_code < 500:
            try:
                err_body = str(e.response.text)
            except Exception:
                err_body = "unable to read response body"
            row_payload["error"] = f"QF API {e.response.status_code}: {err_body[:500]}"
            await db.execute(
                update(BridgeOutbox)
                .where(BridgeOutbox.id == row_id)
                .values(status="failed", processing_at=None, payload=row_payload)
            )
            await db.commit()
        else:
            retry_count_dict = row.__dict__.get('retry_count', 0)
            retry_count_val = int(retry_count_dict)
            new_retry_count = retry_count_val + 1
            backoff_seconds = min(2 ** new_retry_count, 3600)
            await db.execute(
                update(BridgeOutbox)
                .where(BridgeOutbox.id == row_id)
                .values(
                    retry_count=new_retry_count,
                    next_retry_at=datetime.now(timezone.utc) + timedelta(
                        seconds=backoff_seconds
                    ),
                    status="pending",
                    processing_at=None,
                )
            )
            await db.commit()
    except Exception as e:
        row_payload["error"] = f"Unexpected error: {e}"
        retry_count_dict = row.__dict__.get('retry_count', 0)
        retry_count_val = int(retry_count_dict)
        retry_count_int = retry_count_val + 1
        backoff_seconds = min(2 ** retry_count_int, 3600)
        await db.execute(
            update(BridgeOutbox)
            .where(BridgeOutbox.id == row_id)
            .values(
                retry_count=retry_count_int,
                next_retry_at=datetime.now(timezone.utc) + timedelta(
                    seconds=backoff_seconds
                ),
                status="pending",
                processing_at=None,
                payload=row_payload,
            )
        )
        await db.commit()


async def process_outbox_rows_for_ids(ids: list[UUID]) -> None:
    if not ids:
        return
    async with AsyncSessionLocal() as db:
        claim_stmt = (
            update(BridgeOutbox)
            .where(
                BridgeOutbox.id.in_(ids),
                BridgeOutbox.status == "pending",
            )
            .values(
                status="processing",
                processing_at=sa.func.now(),
            )
            .returning(BridgeOutbox.id)
        )
        result = await db.execute(claim_stmt)
        claimed_ids = list(result.scalars().all())
        await db.commit()

        for outbox_id in claimed_ids:
            row_result = await db.execute(
                select(BridgeOutbox).where(BridgeOutbox.id == outbox_id)
            )
            row = row_result.scalar_one_or_none()
            if row is None:
                continue
            await process_outbox_row(db, row)


async def claim_and_sweep() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(BridgeOutbox)
            .where(
                BridgeOutbox.status == "processing",
                BridgeOutbox.processing_at.isnot(None),
                BridgeOutbox.processing_at
                < sa.func.now() - sa.text("interval '10 minutes'"),
            )
            .values(
                status="pending",
                retry_count=BridgeOutbox.retry_count + 1,
                next_retry_at=sa.func.now(),
                processing_at=None,
            )
        )
        await db.commit()

        claim_stmt = (
            update(BridgeOutbox)
            .where(
                BridgeOutbox.status == "pending",
                sa.or_(
                    BridgeOutbox.next_retry_at.is_(None),
                    BridgeOutbox.next_retry_at <= sa.func.now(),
                ),
            )
            .values(
                status="processing",
                processing_at=sa.func.now(),
            )
            .returning(BridgeOutbox.id)
        )
        result = await db.execute(claim_stmt)
        claimed_ids = list(result.scalars().all())
        await db.commit()

        for outbox_id in claimed_ids:
            row_result = await db.execute(
                select(BridgeOutbox).where(BridgeOutbox.id == outbox_id)
            )
            row = row_result.scalar_one_or_none()
            if row is None:
                continue
            await process_outbox_row(db, row)
