from datetime import datetime, timedelta, timezone
from uuid import UUID

from httpx import HTTPStatusError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import User
from app.services.qf_client import _post_qf_token


class RefreshTokenExpiredError(Exception):
    pass


async def get_valid_token(db: AsyncSession, user_id: UUID) -> str:
    result = await db.execute(
        select(
            User.qf_access_token,
            User.qf_refresh_token,
            User.token_expires_at,
        ).where(User.id == user_id)
    )
    row = result.one_or_none()
    if row is None:
        raise RefreshTokenExpiredError("User not found or has no tokens")

    access_token, refresh_token, expires_at = row

    buffer = datetime.now(timezone.utc) + timedelta(seconds=60)
    if expires_at is not None and expires_at > buffer and access_token:
        return access_token

    if not refresh_token:
        raise RefreshTokenExpiredError("No refresh token available")

    try:
        token_data = await _post_qf_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
    except HTTPStatusError as e:
        if 400 <= e.response.status_code < 500:
            raise RefreshTokenExpiredError(
                f"Refresh token expired or revoked: {e.response.status_code}"
            ) from e
        raise

    new_access_token = token_data["access_token"]
    new_refresh_token = token_data.get("refresh_token", refresh_token)
    expires_in = token_data.get("expires_in", 3600)
    new_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            qf_access_token=new_access_token,
            qf_refresh_token=new_refresh_token,
            token_expires_at=new_expires_at,
            updated_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()

    return new_access_token
