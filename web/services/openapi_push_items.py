"""Serialization helpers for OpenAPI push item routes."""

from __future__ import annotations

from typing import Callable

from appcore import medias, pushes
from appcore.db import query_one as db_query_one
from web.services.openapi_materials_serializers import iso_or_none, media_download_url


QueryOneFn = Callable[[str, tuple], dict | None]
MediaUrlFn = Callable[[str | None], str | None]


def serialize_push_item(
    item: dict,
    product: dict,
    *,
    query_one_fn: QueryOneFn = db_query_one,
    media_download_url_fn: MediaUrlFn = media_download_url,
) -> dict:
    readiness = pushes.compute_readiness(item, product)
    status = pushes.compute_status(item, product)
    latest_push = None
    latest_id = item.get("latest_push_id")
    if latest_id:
        row = query_one_fn(
            "SELECT status, error_message, created_at "
            "FROM media_push_logs WHERE id=%s",
            (latest_id,),
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
    query_one_fn: QueryOneFn = db_query_one,
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
