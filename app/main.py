import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import OAuthState, ExchangeCode
from app.routers import auth, content, sync, goals
from app.services import bridge_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncSessionLocal() as session:
        now = datetime.now(timezone.utc)
        await session.execute(delete(OAuthState).where(OAuthState.expires_at < now))
        await session.execute(delete(ExchangeCode).where(ExchangeCode.expires_at < now))
        await session.commit()

    outbox_task = asyncio.create_task(_outbox_sweep())

    yield

    outbox_task.cancel()
    try:
        await outbox_task
    except asyncio.CancelledError:
        pass


async def _outbox_sweep():
    while True:
        await asyncio.sleep(60)
        try:
            await bridge_service.claim_and_sweep()
        except Exception:
            pass


app = FastAPI(title="Hifzy", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(content.router)
app.include_router(sync.router)
app.include_router(goals.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
