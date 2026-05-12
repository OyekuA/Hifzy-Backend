import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.constants import SURAH_VERSE_COUNTS, PAGE_TO_VERSE, TOTAL_PAGES
from app.models import CachedVerse
from app.schemas import ChapterOut, MetadataResponse, ReciterOut, VerseOut
from app.services.qf_client import _post_qf_token

_token_cache: dict[str, Any] = {"access_token": None, "expires_at": None}
_token_lock = asyncio.Lock()
EARLY_REFRESH_SECONDS = 300

_metadata_cache: dict = {"data": None, "fetched_at": None}
_metadata_lock = asyncio.Lock()
METADATA_CACHE_TTL = timedelta(hours=1)

AUDIO_STALE_DAYS = 7

_VERSE_KEY_RE = re.compile(r"^\d+:\d+$")


def _validate_verse_key(value: str, label: str) -> tuple[int, int]:
    if not _VERSE_KEY_RE.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: must be in 'surah:ayah' format, e.g. '2:1', got '{value}'")
    surah_str, ayah_str = value.split(":")
    surah = int(surah_str)
    ayah = int(ayah_str)
    return surah, ayah


def parse_verse_range(range_start: str, range_end: str) -> list[str]:
    start_surah_int, start_ayah_int = _validate_verse_key(range_start, "range_start")
    end_surah_int, end_ayah_int = _validate_verse_key(range_end, "range_end")

    if start_surah_int < 1 or end_surah_int > 114:
        raise HTTPException(status_code=400, detail="Surah number must be between 1 and 114")
    if start_surah_int > end_surah_int:
        raise HTTPException(status_code=400, detail="range_start surah must not exceed range_end surah")
    if start_surah_int == end_surah_int and start_ayah_int > end_ayah_int:
        raise HTTPException(status_code=400, detail="range_start ayah must not exceed range_end ayah for the same surah")

    verse_keys = []
    for surah in range(start_surah_int, end_surah_int + 1):
        max_ayah = SURAH_VERSE_COUNTS.get(surah, 0)
        if max_ayah == 0:
            raise HTTPException(status_code=400, detail=f"Invalid surah number: {surah}")
        ayah_start = start_ayah_int if surah == start_surah_int else 1
        ayah_end = end_ayah_int if surah == end_surah_int else max_ayah
        if ayah_start < 1 or ayah_end > max_ayah:
            raise HTTPException(status_code=400, detail=f"Invalid ayah range for surah {surah}")
        for ayah in range(ayah_start, ayah_end + 1):
            verse_keys.append(f"{surah}:{ayah}")

    return verse_keys


def resolve_page_range(page_start: int, page_end: int) -> tuple[str, str]:
    if page_start < 1 or page_end > TOTAL_PAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Page number must be between 1 and {TOTAL_PAGES}",
        )
    if page_start > page_end:
        raise HTTPException(status_code=400, detail="page_start must not exceed page_end")

    range_start = PAGE_TO_VERSE.get(page_start)
    range_end = PAGE_TO_VERSE.get(page_end)

    if range_start is None or range_end is None:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid page number(s): {page_start}, {page_end}",
        )

    if page_end < TOTAL_PAGES:
        next_page_key = PAGE_TO_VERSE[page_end + 1]
        end_surah_str, end_ayah_str = next_page_key.split(":")
        end_surah = int(end_surah_str)
        end_ayah = int(end_ayah_str)
        if end_ayah == 1:
            if end_surah == 1:
                range_end = "1:1"
            else:
                prev_surah = end_surah - 1
                range_end = f"{prev_surah}:{SURAH_VERSE_COUNTS[prev_surah]}"
        else:
            range_end = f"{end_surah}:{end_ayah - 1}"
    else:
        range_end = "114:6"

    return range_start, range_end


async def get_client_credentials_token() -> str:
    async with _token_lock:
        now = datetime.now(timezone.utc)
        if _token_cache["access_token"] and _token_cache["expires_at"]:
            if (_token_cache["expires_at"] - now).total_seconds() > EARLY_REFRESH_SECONDS:
                return _token_cache["access_token"]

        try:
            data = await _post_qf_token(
                {
                    "grant_type": "client_credentials",
                    "scope": "content",
                },
            )
        except Exception:
            raise HTTPException(status_code=503, detail="Failed to obtain QF content token")

        access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        _token_cache["access_token"] = access_token
        _token_cache["expires_at"] = now + timedelta(seconds=expires_in)
        return access_token


async def fetch_verses_from_cache(
    db: AsyncSession, verse_keys: list[str], recitation_id: int
) -> dict[str, CachedVerse]:
    if not verse_keys:
        return {}
    stmt = select(CachedVerse).where(
        CachedVerse.verse_key.in_(verse_keys),
        CachedVerse.recitation_id == recitation_id,
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return {str(row.verse_key): row for row in rows}


async def _fetch_verses_text(
    token: str, from_key: str, to_key: str, page: int
) -> list[dict]:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.qf_content_base_url}/verses/by_range",
            params={
                "from": from_key,
                "to": to_key,
                "fields": "text_uthmani",
                "per_page": 50,
                "page": page,
            },
            headers={
                "x-auth-token": token,
                "x-client-id": settings.qf_client_id,
            },
        )
        response.raise_for_status()
        data = response.json()
    return data.get("verses", [])


async def _fetch_recitation_audio(
    token: str, recitation_id: int, chapter_number: int
) -> dict[str, str]:
    audio_map: dict[str, str] = {}
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            response = await client.get(
                f"{settings.qf_content_base_url}/recitations/{recitation_id}/by_chapter/{chapter_number}",
                params={"per_page": 50, "page": page},
                headers={
                    "x-auth-token": token,
                    "x-client-id": settings.qf_client_id,
                },
            )
            response.raise_for_status()
            data = response.json()
            audio_files = data.get("audio_files", [])
            for af in audio_files:
                raw_url = (af.get("url") or "").strip()
                if not raw_url:
                    continue
                if raw_url.startswith("http") or raw_url.startswith("//"):
                    audio_map[af["verse_key"]] = raw_url
                else:
                    audio_map[af["verse_key"]] = urljoin(settings.qf_audio_base_url, raw_url)
            if len(audio_files) < 50:
                break
            page += 1
    return audio_map


def _parse_verse_key(vk: str) -> tuple[int, int]:
    surah_str, ayah_str = vk.split(":")
    return int(surah_str), int(ayah_str)


def _are_adjacent(vk_a: str, vk_b: str) -> bool:
    sa, aa = _parse_verse_key(vk_a)
    sb, ab = _parse_verse_key(vk_b)
    if sa == sb:
        return ab == aa + 1
    if sb == sa + 1:
        return aa == SURAH_VERSE_COUNTS.get(sa, 0) and ab == 1
    return False


def _group_contiguous(verse_keys: list[str]) -> list[list[str]]:
    if not verse_keys:
        return []
    sorted_keys = sorted(verse_keys, key=lambda vk: _parse_verse_key(vk))
    groups = []
    current_group = [sorted_keys[0]]
    for i in range(1, len(sorted_keys)):
        if _are_adjacent(current_group[-1], sorted_keys[i]):
            current_group.append(sorted_keys[i])
        else:
            groups.append(current_group)
            current_group = [sorted_keys[i]]
    groups.append(current_group)
    return groups


async def fetch_verses_from_qf(
    token: str, verse_keys: list[str], recitation_id: int
) -> list[dict]:
    if not verse_keys:
        return []

    groups = _group_contiguous(verse_keys)
    target_set = set(verse_keys)

    merged: dict[str, dict] = {}
    chapter_numbers: set[int] = set()
    for group in groups:
        all_verses = []
        from_key = group[0]
        to_key = group[-1]
        page = 1
        while True:
            try:
                verses = await _fetch_verses_text(token, from_key, to_key, page)
            except httpx.HTTPError:
                raise HTTPException(status_code=503, detail="QF Content API unavailable")
            all_verses.extend(verses)
            if len(verses) < 50:
                break
            page += 1
        for v in all_verses:
            vk = v.get("verse_key", "")
            if vk in target_set:
                merged[vk] = {
                    "verse_key": vk,
                    "arabic_text": v.get("text_uthmani", ""),
                    "audio_url": None,
                }
                surah, _ = _parse_verse_key(vk)
                chapter_numbers.add(surah)

    audio_map: dict[str, str] = {}
    for chapter in chapter_numbers:
        try:
            ch_audio = await _fetch_recitation_audio(token, recitation_id, chapter)
        except httpx.HTTPError:
            continue
        audio_map.update(ch_audio)

    for vk, entry in merged.items():
        if vk in audio_map:
            entry["audio_url"] = audio_map[vk]

    return [merged[vk] for vk in sorted(merged, key=lambda k: _parse_verse_key(k))]


async def upsert_cached_verses(
    db: AsyncSession, verses: list[dict], recitation_id: int
) -> None:
    if not verses:
        return
    now = datetime.now(timezone.utc)
    for verse in verses:
        stmt = (
            pg_insert(CachedVerse)
            .values(
                verse_key=verse["verse_key"],
                recitation_id=recitation_id,
                arabic_text=verse["arabic_text"],
                audio_url=verse["audio_url"],
                cached_at=now,
            )
            .on_conflict_do_update(
                index_elements=[CachedVerse.verse_key, CachedVerse.recitation_id],
                set_={
                    "arabic_text": verse["arabic_text"],
                    "audio_url": verse["audio_url"],
                    "cached_at": now,
                },
            )
        )
        await db.execute(stmt)
    await db.commit()


async def get_verses(
    db: AsyncSession,
    recitation_id: int,
    range_start: str | None = None,
    range_end: str | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
) -> list[VerseOut]:
    if range_start is not None and range_end is not None:
        verse_keys = parse_verse_range(range_start, range_end)
    elif page_start is not None and page_end is not None:
        resolved_start, resolved_end = resolve_page_range(page_start, page_end)
        verse_keys = parse_verse_range(resolved_start, resolved_end)
    else:
        raise HTTPException(
            status_code=400,
            detail="Either (range_start + range_end) OR (page_start + page_end) must be provided",
        )

    cached_orm = await fetch_verses_from_cache(db, verse_keys, recitation_id)

    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(days=AUDIO_STALE_DAYS)
    missing_keys = []
    for vk in verse_keys:
        row = cached_orm.get(vk)
        if row is None:
            missing_keys.append(vk)
        else:
            row_dict = {c.name: getattr(row, c.name) for c in row.__table__.c if hasattr(row, c.name)}
            audio_url_val = row_dict.get('audio_url')
            cached_at_val = row_dict.get('cached_at')
            if audio_url_val is None:
                missing_keys.append(vk)
            elif cached_at_val is not None and cached_at_val < stale_threshold:
                missing_keys.append(vk)

    cached: dict[str, dict] = {}
    for vk, row in cached_orm.items():
        cached[vk] = {
            "verse_key": row.verse_key,
            "arabic_text": row.arabic_text,
            "audio_url": row.audio_url,
        }

    if missing_keys:
        try:
            token = await get_client_credentials_token()
            fetched = await fetch_verses_from_qf(token, missing_keys, recitation_id)
            fetched_map = {v["verse_key"]: v for v in fetched}
        except HTTPException:
            if cached:
                fetched_map = {}
            else:
                raise

        verses_to_upsert = []
        for vk in missing_keys:
            f = fetched_map.get(vk)
            if f:
                verses_to_upsert.append(f)
                cached[vk] = f

        await upsert_cached_verses(db, verses_to_upsert, recitation_id)

    result: list[VerseOut] = []
    for vk in verse_keys:
        row = cached.get(vk)
        if row is None:
            continue
        verse_key = row.get("verse_key", vk)
        arabic_text = row.get("arabic_text", "")
        audio_url = row.get("audio_url")
        result.append(
            VerseOut(
                verse_key=verse_key,
                arabic_text=arabic_text,
                audio_url=audio_url,
            )
        )
    return result


async def _call_qf_api(endpoint: str, token: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.qf_content_base_url}{endpoint}",
            headers={
                "x-auth-token": token,
                "x-client-id": settings.qf_client_id,
            },
            params=params or {},
        )
        response.raise_for_status()
        return response.json()


async def get_metadata() -> MetadataResponse:
    async with _metadata_lock:
        now = datetime.now(timezone.utc)
        if (
            _metadata_cache["data"] is not None
            and _metadata_cache["fetched_at"] is not None
            and (now - _metadata_cache["fetched_at"]) < METADATA_CACHE_TTL
        ):
            return _metadata_cache["data"]

        try:
            token = await get_client_credentials_token()
            chapters_data = await _call_qf_api("/chapters", token, {"language": "en"})
            recitations_data = await _call_qf_api("/resources/recitations", token, {"language": "en"})
        except (httpx.HTTPError, HTTPException):
            if _metadata_cache["data"] is not None:
                return _metadata_cache["data"]
            raise HTTPException(status_code=503, detail="QF Content API unavailable")

        chapters = [
            ChapterOut(
                id=c["id"],
                name_simple=c["name_simple"],
                name_arabic=c["name_arabic"],
                verses_count=c["verses_count"],
            )
            for c in chapters_data.get("chapters", [])
        ]

        reciters = [
            ReciterOut(
                id=r["id"],
                name=r.get("reciter_name", ""),
            )
            for r in recitations_data.get("recitations", [])
        ]

        result = MetadataResponse(chapters=chapters, reciters=reciters)
        _metadata_cache["data"] = result
        _metadata_cache["fetched_at"] = now
        return result
