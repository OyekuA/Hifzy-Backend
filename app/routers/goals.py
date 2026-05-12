from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db
from app.schemas import GoalCreate, GoalOut, GoalUpdate
from app.services import goal_service

router = APIRouter(prefix="/goals", tags=["goals"])


@router.post("", response_model=GoalOut, status_code=201)
async def create_goal(
    body: GoalCreate,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GoalOut:
    return await goal_service.create_goal(db, user_id, body.range_start, body.range_end, body.mushaf_id, body.timezone)


@router.patch("/{goal_id}", response_model=GoalOut)
async def update_goal(
    goal_id: UUID,
    body: GoalUpdate,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GoalOut:
    return await goal_service.update_goal(db, user_id, goal_id, body.range_start, body.range_end, body.mushaf_id, body.timezone)
