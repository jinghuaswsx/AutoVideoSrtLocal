"""Serialization helpers for OpenAPI push item routes."""

from __future__ import annotations

from typing import Callable

from appcore import medias, pushes
from appcore.openapi_materials import get_push_log_summary
from web.services.openapi_materials_serializers import iso_or_none, media_download_url


QueryOneFn = Callable[[str, tuple], dict | None]
MediaUrlFn = Callable[[str | None], str | None]
ListItemsFn = Callable[[int, str], list[dict]]
ResolvePushTextsFn = Callable[[int], list[dict[str, str]]]
RecordSuccessFn = Callable[..., int]
RecordFailureFn = Callable[..., int]


def serialize_push_item(
    item: dict,
    product: dict,
    *,
    query_one_fn: QueryOneFn | None = None,
    media_download_url_fn: MediaUrlFn = media_download_url,
) -> dict:
    readiness = pushes.compute_readiness(item, product)
    status = pushes.compute_status(item, product)
    latest_push = None
    latest_id = item.get("latest_push_id")
    if latest_id:
        row = (
            get_push_log_summary(latest_id, query_one_func=query_one_fn)
            if query_one_fn is not None
            else get_push_log_summary(latest_id)
        )
        if row:
            latest_push = {
                "status": row.get("status"),
                "error_message": row.get("error_message"),
                "created_at": iso_or_none(row.get("created_at")),
            }

    cover_key = item.get("cover_object_key")
    return {
        "item_id": item["id"],
        "product_id": item.get("product_id"),
        "product_code": product.get("product_code"),
        "product_name": product.get("name"),
        "listing_status": medias.normalize_listing_status(product.get("listing_status")),
        "lang": item.get("lang") or "en",
        "filename": item.get("filename"),
        "display_name": item.get("display_name") or item.get("filename"),
        "file_size": item.get("file_size"),
        "duration_seconds": item.get("duration_seconds"),
        "cover_url": media_download_url_fn(cover_key) if cover_key else None,
        "status": status,
        "readiness": readiness,
        "pushed_at": iso_or_none(item.get("pushed_at")),
        "latest_push": latest_push,
        "created_at": iso_or_none(item.get("created_at")),
    }


def product_shape_from_push_row(row: dict) -> dict:
    return {
        "id": row.get("product_id"),
        "name": row.get("product_name"),
        "product_code": row.get("product_code"),
        "ad_supported_langs": row.get("ad_supported_langs"),
        "shopify_image_status_json": row.get("shopify_image_status_json"),
        "selling_points": row.get("selling_points"),
        "importance": row.get("importance"),
        "listing_status": row.get("listing_status"),
    }


def serialize_push_item_rows(
    rows: list[dict],
    *,
    query_one_fn: QueryOneFn | None = None,
    media_download_url_fn: MediaUrlFn = media_download_url,
) -> list[dict]:
    items: list[dict] = []
    for row in rows or []:
        items.append(
            serialize_push_item(
                dict(row),
                product_shape_from_push_row(row),
                query_one_fn=query_one_fn,
                media_download_url_fn=media_download_url_fn,
            )
        )
    return items


def filter_push_items_by_status(items: list[dict], status_filter: list[str]) -> list[dict]:
    if not status_filter:
        return items
    return [item for item in items if item["status"] in status_filter]


def paginate_push_items(items: list[dict], *, page: int, page_size: int) -> list[dict]:
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end]


def build_push_item_payload_response(
    item: dict,
    product: dict,
    *,
    query_one_fn: QueryOneFn | None = None,
    media_download_url_fn: MediaUrlFn = media_download_url,
) -> dict:
    payload = pushes.build_item_payload(item, product)
    localized_text = pushes.resolve_localized_text_payload(item)
    localized_texts_request = pushes.build_localized_texts_request(item)
    return {
        "item_id": item["id"],
        "mk_id": product.get("mk_id"),
        "item": serialize_push_item(
            item,
            product,
            query_one_fn=query_one_fn,
            media_download_url_fn=media_download_url_fn,
        ),
        "payload": payload,
        "localized_text": localized_text,
        "localized_texts_request": localized_texts_request,
    }


def build_material_push_payload(
    product: dict,
    *,
    lang: str,
    product_code: str | None = None,
    list_items_fn: ListItemsFn = medias.list_items,
    resolve_push_texts_fn: ResolvePushTextsFn = pushes.resolve_push_texts,
    media_download_url_fn: MediaUrlFn = media_download_url,
) -> dict:
    if not medias.is_product_listed(product):
        raise pushes.ProductNotListedError("product_not_listed")

    product_id = int(product["id"])
    items = list_items_fn(product_id, lang)
    code = (product_code or product.get("product_code") or "").strip().lower()
    product_links = (
        [f"https://newjoyloo.com/{lang}/products/{code}"]
        if lang != "en" else []
    )
    texts = resolve_push_texts_fn(product_id)

    videos = []
    for item in items or []:
        object_key = item.get("object_key")
        cover_object_key = item.get("cover_object_key")
        videos.append({
            "name": item.get("display_name") or item.get("filename") or "",
            "size": int(item.get("file_size") or 0),
            "width": 1080,
            "height": 1920,
            "url": media_download_url_fn(object_key),
            "image_url": media_download_url_fn(cover_object_key),
        })

    return {
        "mode": "create",
        "product_name": product.get("name") or "",
        "texts": texts,
        "product_links": product_links,
        "videos": videos,
        "source": 0,
        "level": int(product.get("importance") or 3),
        "author": "\u8521\u9756\u534e",
        "push_admin": "\u8521\u9756\u534e",
        "roas": 1.6,
        "platforms": ["tiktok"],
        "selling_point": product.get("selling_points") or "",
        "tags": [],
    }


def build_mark_pushed_response(
    item_id: int,
    body: dict | None,
    *,
    operator_user_id: int,
    record_success_fn: RecordSuccessFn = pushes.record_push_success,
) -> dict:
    body = body or {}
    log_id = record_success_fn(
        item_id=item_id,
        operator_user_id=operator_user_id,
        payload=body.get("request_payload") or {},
        response_body=body.get("response_body"),
    )
    return {"ok": True, "log_id": log_id}


def build_mark_failed_response(
    item_id: int,
    body: dict | None,
    *,
    operator_user_id: int,
    record_failure_fn: RecordFailureFn = pushes.record_push_failure,
) -> dict:
    body = body or {}
    log_id = record_failure_fn(
        item_id=item_id,
        operator_user_id=operator_user_id,
        payload=body.get("request_payload") or {},
        error_message=body.get("error_message"),
        response_body=body.get("response_body"),
    )
    return {"ok": True, "log_id": log_id}
