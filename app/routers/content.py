from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.schemas import DailyVerseOut, MetadataResponse, VerseOut
from app.services import content_service

router = APIRouter(prefix="/content", tags=["content"])


@router.get("/verses")
async def get_verses(
    recitation_id: int = Query(..., description="Recitation ID for audio URLs"),
    range_start: str | None = Query(None, description="Start verse key, e.g. 2:1. Required if page_start/page_end not provided."),
    range_end: str | None = Query(None, description="End verse key, e.g. 2:10. Required if page_start/page_end not provided."),
    page_start: int | None = Query(None, description="Start page number (1-604). Required if range_start/range_end not provided."),
    page_end: int | None = Query(None, description="End page number (1-604). Required if range_start/range_end not provided."),
    db: AsyncSession = Depends(get_db),
) -> list[VerseOut]:
    return await content_service.get_verses(
        db=db,
        recitation_id=recitation_id,
        range_start=range_start,
        range_end=range_end,
        page_start=page_start,
        page_end=page_end,
    )


@router.get("/metadata")
async def get_metadata(
) -> MetadataResponse:
    return await content_service.get_metadata()


@router.get("/daily-verse", description="Returns a random verse of the day, cached per UTC day. The first request of any UTC day determines the verse for all subsequent requests until midnight UTC. Translation is cached with the verse; the first translation_id used each day wins. A tafsir_url deep link to Quran.com is included in the response.")
async def get_daily_verse(
    translation_id: int = Query(85, description="QF translation resource ID. Default 85 = M.A.S. Abdel Haleem (Oxford World's Classics)"),
    db: AsyncSession = Depends(get_db),
) -> DailyVerseOut:
    return await content_service.get_daily_verse(db, translation_id=translation_id)
