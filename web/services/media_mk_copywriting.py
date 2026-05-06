"""Service helpers for MK copywriting lookup responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

import requests
from flask import jsonify


@dataclass(frozen=True)
class MkCopywritingResponse:
    payload: dict
    status_code: int


def mk_copywriting_flask_response(result: MkCopywritingResponse):
    return jsonify(result.payload), result.status_code


def normalize_mk_copywriting_query(product_code: str) -> str:
    code = (product_code or "").strip().lower()
    if code.endswith("-rjc"):
        code = code[:-4]
    return code


def mk_product_link_tail(item: dict) -> str:
    links = item.get("product_links") or []
    if not isinstance(links, list) or not links:
        return ""
    first_link = links[0]
    if not isinstance(first_link, str):
        return ""
    return first_link.rstrip("/").rsplit("/", 1)[-1].strip().lower()


def format_mk_copywriting_text(text: dict) -> str:
    title = str(text.get("title") or "").strip()
    message = str(text.get("message") or "").strip()
    description = str(text.get("description") or "").strip()
    if not any((title, message, description)):
        return ""
    return "\n".join((
        f"标题: {title}",
        f"文案: {message}",
        f"描述: {description}",
    ))


def extract_mk_copywriting(data: dict, product_code: str) -> tuple[int | None, str]:
    items = ((data.get("data") or {}).get("items") or [])
    if not isinstance(items, list):
        return None, ""
    for item in items:
        if not isinstance(item, dict):
            continue
        if mk_product_link_tail(item) != product_code:
            continue
        texts = item.get("texts") or []
        if not isinstance(texts, list):
            return item.get("id"), ""
        for text in texts:
            if not isinstance(text, dict):
                continue
            copywriting = format_mk_copywriting_text(text)
            if copywriting:
                return item.get("id"), copywriting
        return item.get("id"), ""
    return None, ""


def build_mk_copywriting_response(
    args: Mapping[str, str],
    *,
    build_headers_fn: Callable[[], dict],
    get_base_url_fn: Callable[[], str],
    is_login_expired_fn: Callable[[dict], bool],
    http_get_fn=requests.get,
) -> MkCopywritingResponse:
    query = normalize_mk_copywriting_query(args.get("product_code") or args.get("q") or "")
    if not query:
        return MkCopywritingResponse(
            {"error": "product_code_required", "message": "请先填写产品 ID"},
            400,
        )

    headers = build_headers_fn()
    if "Authorization" not in headers and "Cookie" not in headers:
        return MkCopywritingResponse(
            {
                "error": "mk_credentials_missing",
                "message": "明空凭据未配置，请先在设置页同步 wedev 凭据",
            },
            500,
        )

    url = f"{get_base_url_fn()}/api/marketing/medias"
    params = {"page": 1, "q": query, "source": "", "level": "", "show_attention": 0}
    try:
        resp = http_get_fn(url, params=params, headers=headers, timeout=15)
    except requests.RequestException as exc:
        return MkCopywritingResponse({"error": "mk_request_failed", "message": str(exc)}, 502)

    if not resp.ok:
        return MkCopywritingResponse(
            {
                "error": "mk_request_failed",
                "message": f"明空接口返回 HTTP {resp.status_code}",
            },
            502,
        )

    try:
        data = resp.json() or {}
    except ValueError:
        return MkCopywritingResponse(
            {"error": "mk_response_invalid", "message": "明空返回数据格式异常"},
            502,
        )

    if is_login_expired_fn(data):
        return MkCopywritingResponse(
            {"error": "mk_credentials_expired", "message": "明空登录已失效，请重新同步 wedev 凭据"},
            401,
        )

    source_item_id, copywriting = extract_mk_copywriting(data, query)
    if source_item_id is None:
        return MkCopywritingResponse(
            {
                "error": "mk_copywriting_not_found",
                "message": f"明空系统未找到产品 ID 为 {query} 的文案",
                "query": query,
            },
            404,
        )
    if not copywriting:
        return MkCopywritingResponse(
            {
                "error": "mk_copywriting_empty",
                "message": f"明空产品 {query} 没有可用文案",
                "query": query,
                "source_item_id": source_item_id,
            },
            404,
        )

    return MkCopywritingResponse(
        {
            "ok": True,
            "query": query,
            "source_item_id": source_item_id,
            "copywriting": copywriting,
        },
        200,
    )
