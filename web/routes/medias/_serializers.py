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


def _serialize_product_skus(
    rows: list[dict] | None,
    *,
    cost_inputs: dict | None = None,
    rmb_per_usd=None,
    xmyc_index: dict[str, dict] | None = None,
) -> list[dict]:
    """xmyc_index 是 sku → {unit_price, goods_name, stock_available, ...} 字典。
    若给了 xmyc_index，每行的 dianxiaomi_sku 会去查一次 xmyc 采购价（RMB），
    并优先用 variant 级 unit_price 替换 product 级 purchase_price 算保本 ROAS。"""
    out: list[dict] = []
    for row in rows or []:
        dxm_sku = (row.get("dianxiaomi_sku") or "").strip()
        xmyc_info = (xmyc_index or {}).get(dxm_sku) if dxm_sku else None
        xmyc_unit_price = (xmyc_info or {}).get("unit_price")

        item = {
            "id": row.get("id"),
            "shopify_product_id": row.get("shopify_product_id") or "",
            "shopify_variant_id": row.get("shopify_variant_id") or "",
            "shopify_sku": row.get("shopify_sku") or "",
            "shopify_price": _json_number_or_none(row.get("shopify_price")),
            "shopify_compare_at_price": _json_number_or_none(row.get("shopify_compare_at_price")),
            "shopify_currency": row.get("shopify_currency") or "",
            "shopify_inventory_quantity": row.get("shopify_inventory_quantity"),
            "shopify_weight_grams": _json_number_or_none(row.get("shopify_weight_grams")),
            "shopify_variant_title": row.get("shopify_variant_title") or "",
            "dianxiaomi_sku": row.get("dianxiaomi_sku") or "",
            "dianxiaomi_sku_code": row.get("dianxiaomi_sku_code") or "",
            "dianxiaomi_name": row.get("dianxiaomi_name") or "",
            "source": row.get("source") or "",
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
            "xmyc_unit_price_rmb": _json_number_or_none(xmyc_unit_price),
            "xmyc_goods_name": (xmyc_info or {}).get("goods_name") or "",
            "xmyc_stock_available": (xmyc_info or {}).get("stock_available"),
            "xmyc_match_type": (xmyc_info or {}).get("match_type") or "",
            "xmyc_sku_code": (xmyc_info or {}).get("sku_code") or "",
        }

        if cost_inputs is not None and row.get("shopify_price") is not None:
            try:
                rate = product_roas.normalize_rmb_per_usd(rmb_per_usd) if rmb_per_usd is not None else product_roas.DEFAULT_RMB_PER_USD
                # variant 级采购价：优先 xmyc.unit_price，否则用产品级
                effective_purchase = (
                    xmyc_unit_price
                    if xmyc_unit_price is not None
                    else cost_inputs.get("purchase_price")
                )
                calc = product_roas.calculate_break_even_roas(
                    purchase_price=effective_purchase,
                    estimated_packet_cost=cost_inputs.get("packet_cost_estimated"),
                    actual_packet_cost=cost_inputs.get("packet_cost_actual"),
                    standalone_price=row.get("shopify_price"),
                    standalone_shipping_fee=cost_inputs.get("standalone_shipping_fee"),
                    rmb_per_usd=rate,
                )
                calc["purchase_basis"] = (
                    "xmyc_variant" if xmyc_unit_price is not None else "product_level"
                )
                item["roas_calculation"] = calc
            except Exception:
                item["roas_calculation"] = None
        else:
            item["roas_calculation"] = None
        out.append(item)
    return out


def _serialize_product(p: dict, items_count: int | None = None,
                       cover_item_id: int | None = None,
                       items_filenames: list[str] | None = None,
                       lang_coverage: dict | None = None,
                       covers: dict[str, str] | None = None,
                       raw_sources_count: int | None = None,
                       roas_rmb_per_usd=None,
                       skus: list[dict] | None = None,
                       xmyc_index: dict[str, dict] | None = None) -> dict:
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
        "shopify_title": p.get("shopify_title") or "",
        "skus": _serialize_product_skus(
            skus,
            cost_inputs={
                "purchase_price": p.get("purchase_price"),
                "packet_cost_estimated": p.get("packet_cost_estimated"),
                "packet_cost_actual": p.get("packet_cost_actual"),
                "standalone_shipping_fee": p.get("standalone_shipping_fee"),
            },
            rmb_per_usd=roas_rmb_per_usd,
            xmyc_index=xmyc_index,
        ),
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
