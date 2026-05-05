"""Serialization helpers for OpenAPI materials routes."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from appcore import medias, pushes


GetProductCoversFn = Callable[[int], dict]
ListCopywritingsFn = Callable[[int], list[dict]]
ListItemsFn = Callable[[int], list[dict]]
MediaDownloadUrlFn = Callable[[str | None], str | None]


def media_download_url(object_key: str | None) -> str | None:
    # 所有 openapi 返回的媒体 URL 统一走内网本地 serve（/medias/obj/<key>），不再用 TOS 预签链接
    if not object_key:
        return None
    return pushes.build_media_public_url(object_key)


def iso_or_none(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def number_or_none(value: Any) -> Any:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def serialize_product(product: dict) -> dict:
    return {
        "id": product.get("id"),
        "product_code": product.get("product_code"),
        "name": product.get("name"),
        "remark": product.get("remark") or "",
        "ai_score": number_or_none(product.get("ai_score")),
        "ai_evaluation_result": product.get("ai_evaluation_result") or "",
        "ai_evaluation_detail": product.get("ai_evaluation_detail") or "",
        "listing_status": medias.normalize_listing_status(product.get("listing_status")),
        "archived": bool(product.get("archived")),
        "created_at": iso_or_none(product.get("created_at")),
        "updated_at": iso_or_none(product.get("updated_at")),
    }


def serialize_cover_map(
    covers: dict,
    *,
    media_download_url: Callable[[str | None], str | None] = media_download_url,
) -> dict:
    payload: dict = {}
    for lang, object_key in (covers or {}).items():
        if not object_key:
            continue
        payload[lang] = {
            "object_key": object_key,
            "download_url": media_download_url(object_key),
            "storage_backend": "local",
        }
    return payload


def group_copywritings(rows: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows or []:
        lang = row.get("lang") or "en"
        grouped[lang].append({
            "title": row.get("title"),
            "body": row.get("body"),
            "description": row.get("description"),
            "ad_carrier": row.get("ad_carrier"),
            "ad_copy": row.get("ad_copy"),
            "ad_keywords": row.get("ad_keywords"),
        })
    return dict(grouped)


def serialize_shopify_image_task(task: dict | None) -> dict | None:
    if not task:
        return None
    return {
        "id": task.get("id"),
        "product_id": task.get("product_id"),
        "product_code": task.get("product_code"),
        "lang": task.get("lang"),
        "shopify_product_id": task.get("shopify_product_id"),
        "link_url": task.get("link_url"),
    }


def serialize_items(
    rows: list[dict],
    *,
    media_download_url: MediaDownloadUrlFn = media_download_url,
) -> list[dict]:
    items: list[dict] = []
    for row in rows or []:
        object_key = row.get("object_key")
        cover_object_key = row.get("cover_object_key")
        items.append({
            "id": row.get("id"),
            "lang": row.get("lang") or "en",
            "filename": row.get("filename"),
            "display_name": row.get("display_name") or row.get("filename"),
            "object_key": object_key,
            "video_download_url": media_download_url(object_key),
            "cover_object_key": cover_object_key,
            "video_cover_download_url": media_download_url(cover_object_key),
            "duration_seconds": row.get("duration_seconds"),
            "file_size": row.get("file_size"),
            "created_at": iso_or_none(row.get("created_at")),
        })
    return items


def build_material_detail_response(
    product: dict,
    *,
    get_product_covers_fn: GetProductCoversFn | None = None,
    list_copywritings_fn: ListCopywritingsFn | None = None,
    list_items_fn: ListItemsFn | None = None,
    media_download_url_fn: MediaDownloadUrlFn = media_download_url,
) -> dict:
    get_product_covers_fn = get_product_covers_fn or medias.get_product_covers
    list_copywritings_fn = list_copywritings_fn or medias.list_copywritings
    list_items_fn = list_items_fn or medias.list_items

    product_id = int(product["id"])
    covers = get_product_covers_fn(product_id)
    copywritings = list_copywritings_fn(product_id)
    items = list_items_fn(product_id)

    return {
        "product": serialize_product(product),
        "covers": serialize_cover_map(covers, media_download_url=media_download_url_fn),
        "copywritings": group_copywritings(copywritings),
        "items": serialize_items(items, media_download_url=media_download_url_fn),
        "storage_backend": "local",
    }


def normalize_target_url(target_url: str) -> str:
    parsed = urlparse((target_url or "").strip())
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    normalized_query = urlencode(query_pairs, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, normalized_query, ""))
