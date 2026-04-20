"""AutoPush FastAPI 路由：代理 AutoVideo OpenAPI + 代理下游推送。"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Body, HTTPException, Query

from .autovideo import AutoVideoService
from .errors import UpstreamServiceError
from .settings import get_settings


api = APIRouter(prefix="/api")


def _service() -> AutoVideoService:
    return AutoVideoService(get_settings())


def _map_upstream_error(error: Exception) -> HTTPException:
    status = getattr(error, "status_code", None)
    if isinstance(error, UpstreamServiceError) or status:
        if status == 401:
            return HTTPException(401, detail="上游认证失败（检查 AUTOVIDEO_API_KEY）")
        if status == 404:
            return HTTPException(404, detail="未找到该产品")
        return HTTPException(int(status or 502), detail=str(error))
    return HTTPException(502, detail=f"上游服务不可达：{error}")


@api.get("/materials")
async def list_materials(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = Query(""),
    archived: str = Query("0"),
) -> Any:
    try:
        return await _service().list_materials(
            page=page, page_size=page_size, q=q, archived=archived,
        )
    except Exception as exc:
        raise _map_upstream_error(exc) from exc


@api.get("/materials/{product_code}")
async def get_materials(product_code: str) -> Any:
    try:
        return await _service().fetch_materials(product_code)
    except Exception as exc:
        raise _map_upstream_error(exc) from exc


@api.get("/materials/{product_code}/push-payload")
async def get_push_payload(product_code: str, lang: str = Query(...)) -> Any:
    try:
        return await _service().fetch_push_payload(product_code, lang)
    except Exception as exc:
        raise _map_upstream_error(exc) from exc


@api.post("/push/medias")
async def push_medias(payload: dict[str, Any] = Body(...)) -> Any:
    target = get_settings().push_medias_target
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                target,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
    except Exception as exc:
        raise HTTPException(502, detail=f"下游推送服务不可达：{exc}") from exc

    try:
        content = response.json() if response.content else {}
    except ValueError:
        content = {"raw": response.text}

    if response.status_code >= 400:
        raise HTTPException(
            response.status_code,
            detail={"upstream_status": response.status_code, "body": content},
        )
    return {
        "ok": True,
        "upstream_status": response.status_code,
        "upstream": content,
    }
