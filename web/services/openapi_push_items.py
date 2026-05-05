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
