import base64
from typing import Any

import httpx

from app.config import settings


def _basic_auth_header() -> dict[str, str]:
    basic_auth = base64.b64encode(
        f"{settings.qf_client_id}:{settings.qf_client_secret}".encode()
    ).decode()
    return {
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


async def _post_qf_token(
    data: dict[str, str],
    timeout: float = 15.0,
) -> dict[str, Any]:
    headers = _basic_auth_header()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.qf_auth_base_url}/oauth2/token",
            data=data,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()
