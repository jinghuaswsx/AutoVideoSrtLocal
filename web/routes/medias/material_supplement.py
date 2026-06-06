"""素材补充（Supplement Materials）API。

聚合本地已同步的明空视频素材快照、素材库状态和广告投放表现，
为产品级素材补充决策提供统一视图。

数据源：mingkong_material_daily_snapshots（本地快照，不依赖外部明空 API）
"""
from __future__ import annotations

import hashlib
import logging
import re
from decimal import Decimal
from typing import Any

from flask import jsonify, request
from flask_login import login_required

from web.auth import admin_required

from . import bp
from web.routes.medias import db_query

log = logging.getLogger(__name__)

LANG_NAMES: dict[str, str] = {
    "en": "英语",
    "de": "德语",
    "fr": "法语",
    "es": "西班牙语",
    "it": "意大利语",
    "ja": "日语",
    "pt": "葡萄牙语",
}

_RJC_SUFFIX_RE = re.compile(r"[-_]?rjc$", re.IGNORECASE)


def _json(payload: dict, status: int = 200):
    return jsonify(payload), status


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _nullable_float(value: Any) -> float | None:
    if value is None:
        return None
    return _safe_float(value)


def _delivery_status(total_spend: Any, active_spend: Any) -> str:
    if _safe_float(total_spend) <= 0:
        return "never"
    if _safe_float(active_spend) > 0:
        return "active"
    return "stopped"


def _strip_rjc(product_code: str) -> str:
    """Remove trailing ``-rjc`` / ``_rjc`` suffix to get the MK search handle."""
    return _RJC_SUFFIX_RE.sub("", product_code.strip()).strip()


def _card_id(video_path: str, media_item_id: int | None = None) -> str:
    path_hash = hashlib.sha1(video_path.encode("utf-8")).hexdigest()[:12]
    if media_item_id:
        return f"lib-{media_item_id}-{path_hash}"
    return f"mk-{path_hash}"


# ------------------------------------------------------------------
# Endpoint 1: Product supplement overview (本地快照数据)
# ------------------------------------------------------------------

@bp.route("/api/product/<int:product_id>/supplement", methods=["GET"])
@login_required
@admin_required
def api_product_supplement(product_id: int):
    """聚合本地明空视频快照、素材库状态和广告投放表现。"""

    # 1. Look up product
    products = db_query(
        "SELECT id, name, product_code FROM media_products "
        "WHERE id = %s AND deleted_at IS NULL",
        [product_id],
    )
    if not products:
        return _json({"error": "product_not_found", "message": "产品不存在"}, 404)
    product = products[0]
    product_code = str(product.get("product_code") or "").strip()
    mk_search_handle = _strip_rjc(product_code) if product_code else ""

    product_info = {
        "id": product_id,
        "name": product.get("name") or "",
        "product_code": product_code,
        "mk_search_handle": mk_search_handle,
    }

    # 2. Query local mingkong_material_daily_snapshots for this product_code
    #    Get the latest snapshot per material_key (deduplicated)
    mk_videos: list[dict] = []
    if mk_search_handle:
        # Build search terms: exact match + with -rjc suffix
        search_terms = [mk_search_handle]
        rjc_handle = f"{mk_search_handle}-rjc"
        if rjc_handle.lower() != mk_search_handle.lower():
            search_terms.append(rjc_handle)

        placeholders = ",".join(["%s"] * len(search_terms))
        try:
            mk_videos = db_query(
                f"""
                SELECT s.*
                FROM mingkong_material_daily_snapshots s
                JOIN mingkong_material_sync_runs r ON r.id = s.run_id AND r.status = 'success'
                JOIN (
                    SELECT s2.material_key, MAX(s2.snapshot_at) AS latest_snapshot_at
                    FROM mingkong_material_daily_snapshots s2
                    JOIN mingkong_material_sync_runs r2 ON r2.id = s2.run_id AND r2.status = 'success'
                    WHERE LOWER(s2.product_code) IN ({placeholders})
                    GROUP BY s2.material_key
                ) latest ON latest.material_key = s.material_key
                       AND latest.latest_snapshot_at = s.snapshot_at
                ORDER BY s.cumulative_90_spend DESC, s.video_ads_count DESC
                """,
                [t.lower() for t in search_terms],
            )
        except Exception:
            log.exception("Failed to fetch local MK snapshots for handle=%s", mk_search_handle)

    # 3. Query local library items
    library_items = db_query(
        "SELECT id, lang, filename, display_name, object_key, created_at "
        "FROM media_items "
        "WHERE product_id = %s AND deleted_at IS NULL "
        "ORDER BY lang, created_at",
        [product_id],
    )

    # 4. Query MK bindings for this product's items
    item_ids = [int(item["id"]) for item in library_items]
    bindings_by_path: dict[str, int] = {}
    bindings_by_item: dict[int, str] = {}
    if item_ids:
        id_placeholders = ",".join(["%s"] * len(item_ids))
        binding_rows = db_query(
            "SELECT media_item_id, mk_video_path "
            f"FROM media_item_mk_bindings WHERE media_item_id IN ({id_placeholders})",
            item_ids,
        )
        for b in binding_rows:
            path = str(b.get("mk_video_path") or "").strip()
            mid = int(b["media_item_id"])
            if path:
                bindings_by_path[path] = mid
                bindings_by_item[mid] = path

    # 5. Query ad delivery status per language
    ad_rows = db_query(
        "SELECT lang, ad_spend_usd, active_7d_ad_spend_usd, purchase_value_usd, "
        "       ad_roas, pushed_video_count, item_count "
        "FROM media_product_lang_ad_summary_cache "
        "WHERE product_id = %s",
        [product_id],
    )
    ad_by_lang: dict[str, dict] = {}
    for row in ad_rows:
        lang = str(row.get("lang") or "").strip().lower()
        if not lang:
            continue
        spend = _safe_float(row.get("ad_spend_usd"))
        active_spend = _safe_float(row.get("active_7d_ad_spend_usd"))
        ad_by_lang[lang] = {
            "ad_spend_usd": spend,
            "active_7d_ad_spend_usd": active_spend,
            "purchase_value_usd": _safe_float(row.get("purchase_value_usd")),
            "ad_roas": _nullable_float(row.get("ad_roas")),
            "pushed_video_count": int(row.get("pushed_video_count") or 0),
            "item_count": int(row.get("item_count") or 0),
            "delivery_status": _delivery_status(spend, active_spend),
        }

    # 6. Build language coverage from library items
    lang_items: dict[str, list[dict]] = {}
    for item in library_items:
        lang = str(item.get("lang") or "en").strip().lower()
        lang_items.setdefault(lang, []).append(item)

    lang_coverage: dict[str, dict] = {}
    for lang, items_in_lang in lang_items.items():
        entry: dict[str, Any] = {"items": len(items_in_lang)}
        ad_info = ad_by_lang.get(lang)
        if ad_info:
            entry["delivery_status"] = ad_info["delivery_status"]
            entry["ad_spend_usd"] = ad_info["ad_spend_usd"]
            entry["ad_roas"] = ad_info["ad_roas"]
        lang_coverage[lang] = entry

    # 7. Build unified card list from local snapshots
    cards: list[dict] = []
    seen_paths: set[str] = set()

    for mk_row in mk_videos:
        video_path = str(mk_row.get("video_path") or "").strip()
        if not video_path or video_path in seen_paths:
            continue
        seen_paths.add(video_path)

        # Check if this MK video is bound to a library item
        bound_item_id = bindings_by_path.get(video_path)
        in_library = bound_item_id is not None

        # Build translated versions for in-library items
        translated_versions: list[dict] = []
        if in_library:
            for lang, items_in_lang in lang_items.items():
                for lib_item in items_in_lang:
                    lang_ad = ad_by_lang.get(lang, {})
                    translated_versions.append({
                        "lang": lang,
                        "lang_name": LANG_NAMES.get(lang, lang),
                        "media_item_id": int(lib_item["id"]),
                        "delivery_status": lang_ad.get("delivery_status", "never"),
                        "ad_spend": lang_ad.get("ad_spend_usd", 0.0),
                        "roas": lang_ad.get("ad_roas"),
                    })

        # Extract video info from local snapshot row
        spends = _safe_float(mk_row.get("cumulative_90_spend"))
        card = {
            "card_id": _card_id(video_path, bound_item_id),
            "in_library": in_library,
            "media_item_id": bound_item_id,
            "mk_video": {
                "name": mk_row.get("video_name") or "",
                "path": video_path,
                "image_path": mk_row.get("video_image_path") or "",
                "spends": spends,
                "ads_count": int(mk_row.get("video_ads_count") or 0),
                "author": mk_row.get("video_author") or "",
                "upload_time": mk_row.get("video_upload_time") or "",
                # Local cover URL if cached
                "local_cover_object_key": mk_row.get("local_cover_object_key") or "",
            },
            "mk_product_id": mk_row.get("mk_product_id"),
            "mk_product_name": mk_row.get("mk_product_name") or "",
            "mk_product_link": mk_row.get("mk_product_link") or "",
            "main_image": mk_row.get("main_image") or "",
            "translated_versions": translated_versions,
            # Snapshot metadata
            "snapshot_date": str(mk_row.get("snapshot_date") or ""),
            "snapshot_at": str(mk_row.get("snapshot_at") or ""),
        }
        cards.append(card)

    # Sort: in_library=True first, then by spends descending
    cards.sort(key=lambda c: (not c["in_library"], -(c["mk_video"].get("spends") or 0)))

    total_mk = len(cards)
    in_lib_count = sum(1 for c in cards if c["in_library"])

    return _json({
        "product": product_info,
        "cards": cards,
        "lang_coverage": lang_coverage,
        "summary": {
            "total_mk_videos": total_mk,
            "in_library": in_lib_count,
            "not_in_library": total_mk - in_lib_count,
        },
    })


# ------------------------------------------------------------------
# Endpoint 2: Video material ad detail (lazy-load)
# ------------------------------------------------------------------

@bp.route("/api/video-material/<int:item_id>/ad-detail", methods=["GET"])
@login_required
@admin_required
def api_video_material_ad_detail(item_id: int):
    """懒加载单个视频素材的广告投放详情。"""

    # 1. Look up the media item
    items = db_query(
        "SELECT id, product_id, lang FROM media_items "
        "WHERE id = %s AND deleted_at IS NULL",
        [item_id],
    )
    if not items:
        return _json({"error": "item_not_found", "message": "素材不存在"}, 404)
    item = items[0]
    product_id = int(item["product_id"])
    lang = str(item.get("lang") or "").strip().lower()

    # 2. Query the cache table for this product + lang
    cache_rows = db_query(
        "SELECT ad_spend_usd, active_7d_ad_spend_usd, purchase_value_usd, "
        "       ad_roas, pushed_video_count, item_count "
        "FROM media_product_lang_ad_summary_cache "
        "WHERE product_id = %s AND lang = %s",
        [product_id, lang],
    )

    if cache_rows:
        row = cache_rows[0]
        spend = _safe_float(row.get("ad_spend_usd"))
        active_spend = _safe_float(row.get("active_7d_ad_spend_usd"))
        return _json({
            "item_id": item_id,
            "lang": lang,
            "ad_spend_usd": spend,
            "purchase_value_usd": _safe_float(row.get("purchase_value_usd")),
            "ad_roas": _nullable_float(row.get("ad_roas")),
            "active_7d_ad_spend_usd": active_spend,
            "pushed_video_count": int(row.get("pushed_video_count") or 0),
            "delivery_status": _delivery_status(spend, active_spend),
        })

    # No cache data available
    return _json({
        "item_id": item_id,
        "lang": lang,
        "ad_spend_usd": 0.0,
        "purchase_value_usd": 0.0,
        "ad_roas": None,
        "active_7d_ad_spend_usd": 0.0,
        "pushed_video_count": 0,
        "delivery_status": "never",
    })
