"""AutoPush FastAPI 路由：代理 AutoVideo OpenAPI + 代理下游推送。"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Body, HTTPException, Query

from .autovideo import AutoVideoService
from .browser_auth import resolve_chrome_auth_headers
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


def _build_localized_text_push_headers(
    settings: Any, target: str,
) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.push_localized_texts_use_chrome_auth:
        headers.update(resolve_chrome_auth_headers(target))
    if (
        "Authorization" not in headers
        and settings.push_localized_texts_authorization
    ):
        headers["Authorization"] = settings.push_localized_texts_authorization
    return headers


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
    """直接向下游推送（旧接口，不写回主项目 DB）。"""
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


@api.post("/marketing/medias/{mk_id}/texts")
async def push_localized_texts(mk_id: int, payload: dict[str, Any] = Body(...)) -> Any:
    settings = get_settings()
    target = f"{settings.push_localized_texts_base_url}/api/marketing/medias/{mk_id}/texts"
    headers = _build_localized_text_push_headers(settings, target)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                target,
                json=payload,
                headers=headers,
            )
    except Exception as exc:
        raise HTTPException(
            502,
            detail={
                "message": f"小语种文案推送服务不可达：{exc}",
                "target_url": target,
            },
        ) from exc

    try:
        content = response.json() if response.content else {}
    except ValueError:
        content = {"raw": response.text}

    if response.status_code >= 400:
        raise HTTPException(
            response.status_code,
            detail={
                "upstream_status": response.status_code,
                "body": content,
                "target_url": target,
            },
        )
    return {
        "ok": True,
        "upstream_status": response.status_code,
        "upstream": content,
        "target_url": target,
    }


# ================================================================
# /api/push-items —— 主推送流程：状态列表 + 推送（含写回）
# ================================================================


@api.get("/push-items")
async def list_push_items(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = Query(""),
    status: str = Query(""),
    lang: str = Query(""),
) -> Any:
    try:
        return await _service().list_push_items(
            page=page, page_size=page_size, q=q, status=status, lang=lang,
        )
    except Exception as exc:
        raise _map_upstream_error(exc) from exc


@api.get("/push-items/by-keys")
async def push_item_by_keys(
    product_id: int = Query(...),
    lang: str = Query(...),
    filename: str = Query(...),
) -> Any:
    """三元组定位：返回 {item_id, item, payload}。"""
    try:
        return await _service().fetch_by_keys(product_id, lang, filename)
    except Exception as exc:
        raise _map_upstream_error(exc) from exc


@api.get("/push-items/{item_id}")
async def get_push_item(item_id: int) -> Any:
    try:
        return await _service().get_push_item(item_id)
    except Exception as exc:
        raise _map_upstream_error(exc) from exc


@api.post("/push-items/{item_id}/push")
async def push_item(item_id: int, payload: dict[str, Any] = Body(...)) -> Any:
    """推送一条素材：POST 下游 → 根据结果调 mark-pushed / mark-failed 写回。

    body 直接是推送 JSON（与 /push/medias 同构）。
    """
    settings = get_settings()
    target = settings.push_medias_target

    # 1. POST 下游
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                target,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
    except Exception as exc:
        # 网络失败 → 标记失败
        try:
            await _service().mark_failed(item_id, {
                "request_payload": payload,
                "error_message": f"下游不可达：{exc}",
            })
        except Exception:
            pass
        raise HTTPException(502, detail=f"下游推送服务不可达：{exc}") from exc

    try:
        content = response.json() if response.content else {}
    except ValueError:
        content = {"raw": response.text}

    response_body_str = (
        response.text if not isinstance(content, dict) else
        __import__("json").dumps(content, ensure_ascii=False)
    )

    if response.status_code >= 400:
        try:
            await _service().mark_failed(item_id, {
                "request_payload": payload,
                "response_body": response_body_str,
                "error_message": f"HTTP {response.status_code}",
            })
        except Exception:
            pass
        raise HTTPException(
            response.status_code,
            detail={"upstream_status": response.status_code, "body": content},
        )

    # 2. 成功 → mark-pushed
    try:
        await _service().mark_pushed(item_id, {
            "request_payload": payload,
            "response_body": response_body_str,
        })
    except Exception as exc:
        # 下游已推送成功但主项目写回失败——返回 200 + 警告
        return {
            "ok": True,
            "upstream_status": response.status_code,
            "upstream": content,
            "writeback_error": f"主项目写回失败：{exc}",
        }

    return {
        "ok": True,
        "upstream_status": response.status_code,
        "upstream": content,
    }


@api.post("/push-items/{item_id}/mark-failed")
async def mark_item_failed(item_id: int, body: dict[str, Any] = Body(...)) -> Any:
    """单独标记失败的直通接口（调试用）。"""
    try:
        return await _service().mark_failed(item_id, body)
    except Exception as exc:
        raise _map_upstream_error(exc) from exc
