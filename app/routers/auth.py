from uuid import UUID

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_current_user, get_db
from app.models import ExchangeCode, User
from app.schemas import ExchangeRequest, ExchangeResponse, UserProfile
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login(db: AsyncSession = Depends(get_db)):
    code_verifier, code_challenge = auth_service.generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    await auth_service.store_oauth_state(db, state, code_verifier)
    authorization_url = auth_service.build_qf_authorization_url(state, code_challenge)
    return RedirectResponse(url=authorization_url, status_code=302)


@router.get("/callback")
async def callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if error:
        return RedirectResponse(
            url=f"{settings.frontend_url}/auth/callback?error={error}&error_description={error_description or ''}",
            status_code=302,
        )

    if not code or not state:
        return RedirectResponse(
            url=f"{settings.frontend_url}/auth/callback?error=invalid_request&error_description=Missing+required+parameters",
            status_code=302,
        )

    try:
        code_verifier = await auth_service.consume_oauth_state(db, state)
    except HTTPException as exc:
        return RedirectResponse(
            url=f"{settings.frontend_url}/auth/callback?error={exc.detail}&error_description=OAuth+state+validation+failed",
            status_code=302,
        )

    try:
        qf_tokens = await auth_service.exchange_code_with_qf(code, code_verifier)
    except HTTPException:
        raise
    user = await auth_service.upsert_user(db, qf_tokens)
    one_time_code = await auth_service.create_exchange_code(db, UUID(str(user.__dict__.get('id', str(user.id)))))
    return RedirectResponse(
        url=f"{settings.frontend_url}/auth/callback?code={one_time_code}",
        status_code=302,
    )


@router.post("/exchange", response_model=ExchangeResponse)
async def exchange(body: ExchangeRequest, db: AsyncSession = Depends(get_db)):
    user_id = await auth_service.consume_exchange_code(db, body.code)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=400, detail="User not found")
    token = auth_service.create_jwt(user)
    return ExchangeResponse(token=token)


@router.get("/me", response_model=UserProfile)
async def me(user_id: UUID = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserProfile.model_validate(user)


@router.post("/logout")
async def logout(
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(ExchangeCode)
        .where(ExchangeCode.user_id == user_id, ExchangeCode.used == False)
        .values(used=True)
    )
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(qf_access_token=None, qf_refresh_token=None)
    )
    await db.commit()
    return {"ok": True}
