import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db
from app.schemas import PullResponse, PushRequest
from app.services import bridge_service, sync_service

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/pull", response_model=PullResponse)
async def pull(
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    lastPulledAt: int = Query(default=0),
    schemaVersion: int = Query(default=1),
):
    return await sync_service.pull(db, user_id, lastPulledAt)


@router.post("/push")
async def push(
    body: PushRequest,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    new_outbox_ids = await sync_service.push(db, user_id, body)
    if new_outbox_ids:
        asyncio.create_task(
            bridge_service.process_outbox_rows_for_ids(new_outbox_ids)
        )
    return {"ok": True}
