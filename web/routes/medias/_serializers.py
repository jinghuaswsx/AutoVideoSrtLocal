from __future__ import annotations

import json

from appcore import medias, product_roas, shopify_image_tasks


def _json_number_or_none(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _int_or_none(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _serialize_product(p: dict, items_count: int | None = None,
                       cover_item_id: int | None = None,
                       items_filenames: list[str] | None = None,
                       lang_coverage: dict | None = None,
                       covers: dict[str, str] | None = None,
                       raw_sources_count: int | None = None,
                       roas_rmb_per_usd=None) -> dict:
    if covers is None:
        covers = medias.get_product_covers(p["id"])
    if roas_rmb_per_usd is None:
        roas_rmb_per_usd = product_roas.DEFAULT_RMB_PER_USD
    has_en_cover = "en" in covers
    cover_url = f"/medias/cover/{p['id']}?lang=en" if has_en_cover else (
        f"/medias/thumb/{cover_item_id}" if cover_item_id else None
    )
    # localized_links_json 鍙兘鏄?str / dict / None
    raw_links = p.get("localized_links_json")
    localized_links: dict = {}
    if isinstance(raw_links, dict):
        localized_links = raw_links
    elif isinstance(raw_links, str):
        try:
            parsed = json.loads(raw_links)
            if isinstance(parsed, dict):
                localized_links = parsed
        except (json.JSONDecodeError, ValueError):
            pass
    link_check_tasks = medias.parse_link_check_tasks_json(p.get("link_check_tasks_json"))
    shopify_image_status = shopify_image_tasks.parse_status_map(p.get("shopify_image_status_json"))
    return {
        "id": p["id"],
        "name": p["name"],
        "product_code": p.get("product_code"),
        "mk_id": p.get("mk_id"),
        "shopifyid": p.get("shopifyid"),
        "user_id": int(p["user_id"]) if p.get("user_id") is not None else None,
        "owner_name": (p.get("owner_name") or "").strip(),
        "has_en_cover": has_en_cover,
        "color_people": p.get("color_people"),
        "source": p.get("source"),
        "remark": p.get("remark") or "",
        "ai_score": _json_number_or_none(p.get("ai_score")),
        "ai_evaluation_result": p.get("ai_evaluation_result") or "",
        "ai_evaluation_detail": p.get("ai_evaluation_detail") or "",
        "listing_status": medias.normalize_listing_status(p.get("listing_status")),
        "ad_supported_langs": p.get("ad_supported_langs") or "",
        "archived": bool(p.get("archived")),
        "created_at": p["created_at"].isoformat() if p.get("created_at") else None,
        "updated_at": p["updated_at"].isoformat() if p.get("updated_at") else None,
        "items_count": items_count,
        "items_filenames": items_filenames or [],
        "cover_thumbnail_url": cover_url,
        "lang_coverage": lang_coverage or {},
        "localized_links": localized_links,
        "link_check_tasks": link_check_tasks,
        "shopify_image_status": shopify_image_status,
        "raw_sources_count": raw_sources_count or 0,
        "purchase_1688_url": p.get("purchase_1688_url") or "",
        "purchase_price": _json_number_or_none(p.get("purchase_price")),
        "packet_cost_estimated": _json_number_or_none(p.get("packet_cost_estimated")),
        "packet_cost_actual": _json_number_or_none(p.get("packet_cost_actual")),
        "package_length_cm": _json_number_or_none(p.get("package_length_cm")),
        "package_width_cm": _json_number_or_none(p.get("package_width_cm")),
        "package_height_cm": _json_number_or_none(p.get("package_height_cm")),
        "tk_sea_cost": _json_number_or_none(p.get("tk_sea_cost")),
        "tk_air_cost": _json_number_or_none(p.get("tk_air_cost")),
        "tk_sale_price": _json_number_or_none(p.get("tk_sale_price")),
        "standalone_price": _json_number_or_none(p.get("standalone_price")),
        "standalone_shipping_fee": _json_number_or_none(p.get("standalone_shipping_fee")),
        "roas_rmb_per_usd": float(product_roas.normalize_rmb_per_usd(roas_rmb_per_usd)),
        "roas_calculation": product_roas.calculate_break_even_roas(
            purchase_price=p.get("purchase_price"),
            estimated_packet_cost=p.get("packet_cost_estimated"),
            actual_packet_cost=p.get("packet_cost_actual"),
            standalone_price=p.get("standalone_price"),
            standalone_shipping_fee=p.get("standalone_shipping_fee"),
            rmb_per_usd=roas_rmb_per_usd,
        ),
    }


def _serialize_item(it: dict, raw_sources_by_id: dict[int, dict] | None = None) -> dict:
    has_user_cover = bool(it.get("cover_object_key"))
    raw_sources_by_id = raw_sources_by_id or {}
    source_raw_id = _int_or_none(it.get("source_raw_id"))
    if source_raw_id is None and it.get("auto_translated"):
        source_raw_id = _int_or_none(it.get("source_ref_id"))
    source_raw = raw_sources_by_id.get(source_raw_id or 0)
    source_raw_payload = None
    if source_raw_id is not None:
        source_raw_payload = {
            "id": source_raw_id,
            "display_name": (
                (source_raw or {}).get("display_name")
                or f"原始去字幕素材 #{source_raw_id}"
            ),
            "video_url": f"/medias/raw-sources/{source_raw_id}/video",
            "cover_url": f"/medias/raw-sources/{source_raw_id}/cover",
        }
    return {
        "id": it["id"],
        "lang": it.get("lang") or "en",
        "filename": it["filename"],
        "display_name": it.get("display_name") or it["filename"],
        "object_key": it["object_key"],
        "cover_object_key": it.get("cover_object_key"),
        "thumbnail_url": f"/medias/thumb/{it['id']}" if it.get("thumbnail_path") else None,
        "cover_url": f"/medias/item-cover/{it['id']}" if has_user_cover else None,
        "duration_seconds": it.get("duration_seconds"),
        "file_size": it.get("file_size"),
        "source_raw_id": source_raw_id,
        "source_ref_id": _int_or_none(it.get("source_ref_id")),
        "bulk_task_id": it.get("bulk_task_id") or "",
        "auto_translated": bool(it.get("auto_translated")),
        "source_raw": source_raw_payload,
        "created_at": it["created_at"].isoformat() if it.get("created_at") else None,
    }


def _serialize_raw_source(row: dict) -> dict:
    return {
        "id": row["id"],
        "product_id": row["product_id"],
        "display_name": row.get("display_name") or "",
        "video_object_key": row["video_object_key"],
        "cover_object_key": row["cover_object_key"],
        "duration_seconds": row.get("duration_seconds"),
        "file_size": row.get("file_size"),
        "width": row.get("width"),
        "height": row.get("height"),
        "sort_order": row.get("sort_order") or 0,
        "translations": row.get("translations") or {},
        "video_url": f"/medias/raw-sources/{row['id']}/video",
        "cover_url": f"/medias/raw-sources/{row['id']}/cover",
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def _serialize_link_check_task(task: dict) -> dict:
    return {
        "id": task["id"],
        "type": task["type"],
        "status": task["status"],
        "link_url": task["link_url"],
        "resolved_url": task.get("resolved_url", ""),
        "page_language": task.get("page_language", ""),
        "target_language": task["target_language"],
        "target_language_name": task["target_language_name"],
        "progress": dict(task.get("progress") or {}),
        "summary": dict(task.get("summary") or {}),
        "error": task.get("error", ""),
        "reference_images": [
            {
                "id": ref["id"],
                "filename": ref["filename"],
                "preview_url": f"/api/link-check/tasks/{task['id']}/images/reference/{ref['id']}",
            }
            for ref in task.get("reference_images", [])
        ],
        "items": [
            {
                "id": item["id"],
                "kind": item["kind"],
                "source_url": item["source_url"],
                "site_preview_url": f"/api/link-check/tasks/{task['id']}/images/site/{item['id']}",
                "analysis": dict(item.get("analysis") or {}),
                "reference_match": dict(item.get("reference_match") or {}),
                "binary_quick_check": dict(item.get("binary_quick_check") or {}),
                "same_image_llm": dict(item.get("same_image_llm") or {}),
                "status": item.get("status") or "pending",
                "error": item.get("error") or "",
            }
            for item in task.get("items", [])
        ],
    }


def _serialize_detail_image(row: dict) -> dict:
    return {
        "id": row["id"],
        "product_id": row["product_id"],
        "lang": row["lang"],
        "sort_order": int(row.get("sort_order") or 0),
        "object_key": row["object_key"],
        "content_type": row.get("content_type"),
        "file_size": row.get("file_size"),
        "width": row.get("width"),
        "height": row.get("height"),
        "origin_type": row.get("origin_type") or "manual",
        "source_detail_image_id": row.get("source_detail_image_id"),
        "image_translate_task_id": row.get("image_translate_task_id"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "thumbnail_url": f"/medias/detail-image/{row['id']}",
    }
