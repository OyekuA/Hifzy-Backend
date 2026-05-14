import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from jose import jwt
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ExchangeCode, OAuthState, RefreshToken, User
from app.services.qf_client import _post_qf_token


def generate_pkce_pair() -> tuple[str, str]:
    code_verifier = secrets.token_urlsafe(43)[:128]
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


async def store_oauth_state(db: AsyncSession, state: str, code_verifier: str) -> None:
    now = datetime.now(timezone.utc)
    await db.execute(delete(OAuthState).where(OAuthState.expires_at < now))
    await db.execute(delete(ExchangeCode).where(ExchangeCode.expires_at < now))
    oauth_state = OAuthState(
        state=state,
        code_verifier=code_verifier,
        expires_at=now + timedelta(minutes=10),
    )
    db.add(oauth_state)
    await db.commit()


async def consume_oauth_state(db: AsyncSession, state: str) -> str:
    result = await db.execute(select(OAuthState).where(OAuthState.state == state))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    expires_at_val = datetime.fromisoformat(str(row.expires_at))
    if expires_at_val < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    code_verifier: str = str(row.code_verifier)
    await db.delete(row)
    await db.commit()
    return code_verifier


async def exchange_code_with_qf(code: str, code_verifier: str) -> dict:
    try:
        return await _post_qf_token(
            {
                "grant_type": "authorization_code",
                "redirect_uri": settings.qf_redirect_uri,
                "code": code,
                "code_verifier": code_verifier,
            },
        )
    except Exception:
        raise HTTPException(
            status_code=502, detail="QF OAuth token exchange failed"
        )


async def upsert_user(db: AsyncSession, qf_token_response: dict) -> User:
    id_token = qf_token_response["id_token"]
    claims = jwt.get_unverified_claims(id_token)

    qf_user_id = claims["sub"]
    email = claims.get("email", "")
    expires_in = qf_token_response.get("expires_in", 0)
    token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    first_name = claims.get("first_name")
    last_name = claims.get("last_name")

    stmt = (
        pg_insert(User)
        .values(
            qf_user_id=qf_user_id,
            email=email,
            first_name=first_name,
            last_name=last_name,
            qf_access_token=qf_token_response.get("access_token"),
            qf_refresh_token=qf_token_response.get("refresh_token"),
            token_expires_at=token_expires_at,
        )
        .on_conflict_do_update(
            index_elements=[User.qf_user_id],
            set_={
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "qf_access_token": qf_token_response.get("access_token"),
                "qf_refresh_token": qf_token_response.get("refresh_token"),
                "token_expires_at": token_expires_at,
                "updated_at": datetime.now(timezone.utc),
            },
        )
        .returning(User)
    )
    result = await db.execute(stmt)
    await db.commit()
    return result.scalar_one()


async def create_exchange_code(db: AsyncSession, user_id: UUID) -> str:
    code = secrets.token_urlsafe(32)
    exchange = ExchangeCode(
        user_id=user_id,
        code=code,
        used=False,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    db.add(exchange)
    await db.commit()
    return code


async def consume_exchange_code(db: AsyncSession, code: str) -> UUID:
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ExchangeCode).where(
            ExchangeCode.code == code,
            ExchangeCode.expires_at > now,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    used_val = bool(row.__dict__.get('used', False))
    if used_val:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    user_id_val = UUID(str(row.__dict__.get('user_id')))
    await db.execute(
        update(ExchangeCode)
        .where(ExchangeCode.id == row.id)
        .values(used=True)
    )
    await db.commit()
    return user_id_val


def create_jwt(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_jwt(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])


def build_qf_authorization_url(state: str, code_challenge: str) -> str:
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": settings.qf_client_id,
        "redirect_uri": settings.qf_redirect_uri,
        "scope": "openid offline_access goal reading_session activity_day streak.read",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{settings.qf_auth_base_url}/oauth2/auth?{urlencode(params)}"


async def create_refresh_token(db: AsyncSession, user_id: UUID, *, commit: bool = True) -> str:
    token = secrets.token_urlsafe(48)
    refresh = RefreshToken(
        user_id=user_id,
        token=token,
        revoked=False,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(refresh)
    if commit:
        await db.commit()
    return token


async def rotate_refresh_token(db: AsyncSession, token_str: str) -> tuple[User, str]:
    result = await db.execute(
        select(RefreshToken)
        .where(
            RefreshToken.token == token_str,
            RefreshToken.revoked == False,
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
        .with_for_update()
    )
    row = result.scalar_one_or_none()
    if row is None:
        check = await db.execute(
            select(RefreshToken).where(RefreshToken.token == token_str)
        )
        existing = check.scalar_one_or_none()
        if existing is None:
            raise HTTPException(status_code=401, detail="invalid_refresh_token")
        raise HTTPException(status_code=401, detail="refresh_token_expired")

    row.revoked = True
    db.add(row)

    new_token_str = await create_refresh_token(db, row.user_id, commit=False)

    result = await db.execute(select(User).where(User.id == row.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="user_not_found")

    await db.commit()
    return user, new_token_str
