"""AutoVideo OpenAPI 客户端（上游素材查询 / 推送载荷）。"""
from __future__ import annotations

from typing import Any

import httpx

from .errors import UpstreamServiceError
from .settings import Settings


class AutoVideoService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def list_materials(
        self,
        *,
        page: int,
        page_size: int,
        q: str,
        archived: str,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if q:
            params["q"] = q
        if archived:
            params["archived"] = archived
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.settings.autovideo_base_url}/openapi/materials",
                params=params,
                headers={"X-API-Key": self.settings.autovideo_api_key},
            )
        return _unwrap(response)

    async def fetch_materials(self, product_code: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.settings.autovideo_base_url}/openapi/materials/{product_code}",
                headers={"X-API-Key": self.settings.autovideo_api_key},
            )
        return _unwrap(response)

    async def fetch_push_payload(self, product_code: str, lang: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.settings.autovideo_base_url}/openapi/materials/{product_code}/push-payload",
                params={"lang": lang},
                headers={"X-API-Key": self.settings.autovideo_api_key},
            )
        return _unwrap(response)

    # ----- 推送状态（/openapi/push-items） -----

    async def list_push_items(
        self,
        *,
        page: int,
        page_size: int,
        q: str,
        status: str,
        lang: str,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if q:
            params["q"] = q
        if status:
            params["status"] = status
        if lang:
            params["lang"] = lang
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.settings.autovideo_base_url}/openapi/push-items",
                params=params,
                headers={"X-API-Key": self.settings.autovideo_api_key},
            )
        return _unwrap(response)

    async def get_push_item(self, item_id: int) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.settings.autovideo_base_url}/openapi/push-items/{item_id}",
                headers={"X-API-Key": self.settings.autovideo_api_key},
            )
        return _unwrap(response)

    async def mark_pushed(self, item_id: int, body: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.settings.autovideo_base_url}/openapi/push-items/{item_id}/mark-pushed",
                json=body,
                headers={"X-API-Key": self.settings.autovideo_api_key},
            )
        return _unwrap(response)

    async def mark_failed(self, item_id: int, body: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.settings.autovideo_base_url}/openapi/push-items/{item_id}/mark-failed",
                json=body,
                headers={"X-API-Key": self.settings.autovideo_api_key},
            )
        return _unwrap(response)


def _unwrap(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {"raw": response.text}
    if response.status_code >= 400:
        detail = payload.get("error") if isinstance(payload, dict) else str(payload)
        raise UpstreamServiceError(response.status_code, detail or response.text)
    return payload
