from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Goal, Preference
from app.services.content_service import parse_verse_range
from app.services.token_service import get_valid_token, RefreshTokenExpiredError


async def create_goal(db: AsyncSession, user_id: UUID, range_start: str, range_end: str, mushaf_id: int | None = None, user_timezone: str | None = None) -> Goal:
    try:
        access_token = await get_valid_token(db, user_id)
    except RefreshTokenExpiredError:
        raise HTTPException(status_code=401, detail="Session expired, please log in again")

    if mushaf_id is None or user_timezone is None:
        pref_result = await db.execute(
            select(Preference.mushaf_id, Preference.timezone).where(
                Preference.user_id == user_id, Preference.is_deleted == False
            ).limit(1)
        )
        pref_row = pref_result.first()
        if pref_row is not None:
            if mushaf_id is None:
                mushaf_id = pref_row[0]
            if user_timezone is None:
                user_timezone = pref_row[1]

    if mushaf_id is None:
        raise HTTPException(status_code=400, detail="mushaf_id is required: set it in preferences or pass it explicitly")
    if user_timezone is None:
        raise HTTPException(status_code=400, detail="timezone is required: set it in preferences or pass it explicitly")

    try:
        ZoneInfo(user_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid IANA timezone value")

    body = {
        "type": "QURAN_RANGE",
        "amount": f"{range_start}-{range_end}",
        "duration": 1,
        "category": "QURAN",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.qf_user_api_base_url}/auth/v1/goals",
                params={"mushafId": mushaf_id},
                headers={
                    "x-auth-token": access_token,
                    "x-client-id": settings.qf_client_id,
                    "x-timezone": user_timezone,
                },
                json=body,
            )
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"QF User API unavailable: {e.__class__.__name__}: {e}",
        )

    if not response.is_success:
        try:
            err_body = response.text[:500]
        except Exception:
            err_body = "(could not read body)"
        raise HTTPException(
            status_code=502,
            detail=f"QF User API returned {response.status_code}: {err_body}",
        )

    resp_data = response.json()
    qf_goal_id = resp_data.get("id") or (resp_data.get("data") or {}).get("id")
    if not qf_goal_id:
        raise HTTPException(
            status_code=502,
            detail=f"QF User API returned unexpected response (no goal id): {response.text[:500]}",
        )

    goal = Goal(user_id=user_id, qf_goal_id=qf_goal_id, range_start=range_start, range_end=range_end)
    db.add(goal)
    await db.commit()
    await db.refresh(goal)

    return goal


async def update_goal(db: AsyncSession, user_id: UUID, goal_id: UUID, range_start: str, range_end: str, mushaf_id: int | None = None, user_timezone: str | None = None) -> Goal:
    parse_verse_range(range_start, range_end)

    result = await db.execute(select(Goal).where(Goal.id == goal_id))
    goal = result.scalar_one_or_none()
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")

    goal_user_id: UUID = UUID(str(goal.user_id))
    if goal_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        access_token = await get_valid_token(db, user_id)
    except RefreshTokenExpiredError:
        raise HTTPException(status_code=401, detail="Session expired, please log in again")

    if mushaf_id is None or user_timezone is None:
        pref_result = await db.execute(
            select(Preference.mushaf_id, Preference.timezone).where(
                Preference.user_id == user_id, Preference.is_deleted == False
            ).limit(1)
        )
        pref_row = pref_result.first()
        if pref_row is not None:
            if mushaf_id is None:
                mushaf_id = pref_row[0]
            if user_timezone is None:
                user_timezone = pref_row[1]

    if mushaf_id is None:
        raise HTTPException(status_code=400, detail="mushaf_id is required: set it in preferences or pass it explicitly")
    if user_timezone is None:
        raise HTTPException(status_code=400, detail="timezone is required: set it in preferences or pass it explicitly")

    try:
        ZoneInfo(user_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid IANA timezone value")

    body = {
        "type": "QURAN_RANGE",
        "amount": f"{range_start}-{range_end}",
        "duration": 1,
        "category": "QURAN",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{settings.qf_user_api_base_url}/auth/v1/goals/{goal.qf_goal_id}",
                params={"mushafId": mushaf_id},
                headers={
                    "x-auth-token": access_token,
                    "x-client-id": settings.qf_client_id,
                    "x-timezone": user_timezone,
                },
                json=body,
            )
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"QF User API unavailable: {e.__class__.__name__}: {e}",
        )

    if not response.is_success:
        try:
            err_body = response.text[:500]
        except Exception:
            err_body = "(could not read body)"
        raise HTTPException(
            status_code=502,
            detail=f"QF User API returned {response.status_code}: {err_body}",
        )

    now = datetime.now(timezone.utc)
    await db.execute(
        update(Goal)
        .where(Goal.id == goal_id)
        .values(range_start=range_start, range_end=range_end, updated_at=now)
    )
    await db.commit()
    await db.refresh(goal)

    return goal
