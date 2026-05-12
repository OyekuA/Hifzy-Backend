from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, ExpiredSignatureError

from app.services.auth_service import decode_jwt
from app.services.content_service import get_client_credentials_token

oauth2_scheme = HTTPBearer(auto_error=False)


async def get_db():
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_current_user(
    token: HTTPAuthorizationCredentials | None = Depends(oauth2_scheme),
) -> UUID:
    if token is None:
        raise HTTPException(status_code=401, detail="Missing or invalid credentials")
    try:
        payload = decode_jwt(token.credentials)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    try:
        return UUID(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Malformed token payload")


async def get_content_token() -> str:
    return await get_client_credentials_token()
