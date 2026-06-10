from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .categories import goods_category_for_source
from . import store


MARK_STATUS_OK = "ok"
MARK_STATUS_BAD = "bad"
GOODS_RANK_KIND_LABELS = {
    "hot": "商品热销榜",
    "new": "新品榜",
}
GOODS_RANK_PERIOD_LABELS = {
    "1d": "日榜",
    "7d": "周榜",
    "30d": "月榜",
}


@dataclass(frozen=True)
class TabcutResponse:
    payload: dict[str, Any]
    status_code: int = 200


def build_videos_response(args: Mapping[str, Any]) -> TabcutResponse:
    return TabcutResponse(_hydrate_video_items(store.list_video_candidates(args)))


def build_goods_response(args: Mapping[str, Any]) -> TabcutResponse:
    return TabcutResponse(_hydrate_goods_items(store.list_goods(args)))


def build_category_options_response(args: Mapping[str, Any]) -> TabcutResponse:
    return TabcutResponse({"items": store.list_category_options(args)})


def _bool_payload(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "checked", "marked"}
    return False


def _normalize_mark_status(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    if raw in {MARK_STATUS_OK, "pass", "yes", "行"}:
        return MARK_STATUS_OK
    if raw in {MARK_STATUS_BAD, "fail", "no", "不行"}:
        return MARK_STATUS_BAD
    return None


def build_mark_response(
    entity_type: str,
    entity_id: str,
    payload: Mapping[str, Any] | None,
    *,
    user_id: Any = None,
) -> TabcutResponse:
    normalized_type = str(entity_type or "").strip().lower()
    normalized_id = str(entity_id or "").strip()
    if normalized_type not in {"video", "goods"}:
        return TabcutResponse({"ok": False, "error": "invalid_entity_type"}, 400)
    if not normalized_id:
        return TabcutResponse({"ok": False, "error": "missing_entity_id"}, 400)

    payload = payload or {}
    if "mark_status" in payload or "status" in payload:
        mark_status = _normalize_mark_status(payload.get("mark_status", payload.get("status")))
    else:
        mark_status = MARK_STATUS_BAD if _bool_payload(payload.get("marked")) else None

    if normalized_type == "video":
        store.set_video_mark_status(normalized_id, mark_status=mark_status, user_id=user_id)
    else:
        store.set_goods_mark_status(normalized_id, mark_status=mark_status, user_id=user_id)
    return TabcutResponse(
        {
            "ok": True,
            "entity_type": normalized_type,
            "entity_id": normalized_id,
            "mark_status": mark_status,
            "is_marked": bool(mark_status),
        }
    )


def _hydrate_video_items(payload: dict[str, Any]) -> dict[str, Any]:
    hydrated = dict(payload)
    items = []
    for row in payload.get("items") or []:
        item = dict(row)
        raw = _json_dict(item.pop("video_raw_json", None))
        item["hashtags"] = _hashtag_names(raw)
        _fill_missing(item, "currency_symbol", item.get("price_currency"))
        raw_item = _first_raw_item(raw)
        if raw_item:
            _fill_missing(item, "primary_item_pic_url", raw_item.get("itemCoverUrl"))
            _fill_missing(item, "primary_item_name", raw_item.get("itemName"))
            _fill_missing(item, "primary_item_price_min", _raw_item_price(raw_item))
            _fill_missing(item, "primary_item_sold_count", raw_item.get("soldCount") or raw_item.get("itemSoldCountTotal"))
            _fill_missing(item, "currency_symbol", _raw_item_currency(raw_item))
            _fill_missing(item, "price_currency", raw_item.get("priceCurrency"))
            _fill_missing(item, "primary_item_url", _raw_item_url(raw_item))
        _fill_missing(item, "primary_item_url", _tiktok_product_url(item.get("primary_item_id")))
        items.append(item)
    _tabcut_attach_fine_ai_evaluation(items)
    hydrated["items"] = items
    return hydrated


def _tabcut_attach_fine_ai_evaluation(items: list[dict[str, Any]]) -> None:
    if not items:
        return
    from appcore.db import query
    import logging

    video_ids = [str(item.get("video_id") or "").strip() for item in items if item.get("video_id")]
    local_video_paths = [str(item.get("local_video_path") or "").strip() for item in items if item.get("local_video_path")]
    
    # 1. 查找自动任务的 run_id
    auto_evals = {}
    if video_ids:
        placeholders = ",".join(["%s"] * len(video_ids))
        try:
            auto_rows = query(
                f"""
                SELECT video_id, evaluation_run_id
                FROM tabcut_fine_ai_auto_evaluations
                WHERE video_id IN ({placeholders})
                  AND status IN ('completed', 'partially_completed')
                  AND evaluation_run_id IS NOT NULL
                  AND evaluation_run_id <> ''
                """,
                tuple(video_ids)
            )
            for r in auto_rows:
                auto_evals[str(r["video_id"]).strip()] = str(r["evaluation_run_id"]).strip()
        except Exception:
            logging.getLogger("appcore.tabcut_selection").exception("failed to load tabcut fine AI auto evaluations")

    # 2. 查找 evaluation runs
    run_rows = []
    params = []
    where_clauses = []
    
    if video_ids:
        video_placeholders = ",".join(["%s"] * len(video_ids))
        where_clauses.append(f"JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.video_id')) IN ({video_placeholders})")
        params.extend(video_ids)
        
    if local_video_paths:
        path_placeholders = ",".join(["%s"] * len(local_video_paths))
        where_clauses.append(f"JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.card_video_path')) IN ({path_placeholders})")
        where_clauses.append(f"JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.external_card_video.path')) IN ({path_placeholders})")
        where_clauses.append(f"JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.video_path')) IN ({path_placeholders})")
        params.extend(local_video_paths)
        params.extend(local_video_paths)
        params.extend(local_video_paths)
        
    runs_by_video_id = {}
    runs_by_path = {}
    
    if where_clauses:
        or_sql = " OR ".join(where_clauses)
        sql = f"""
            SELECT id, evaluation_run_id, status, metadata_json, summary_json, frontend_json
            FROM ai_evaluation_runs
            WHERE ({or_sql})
              AND status IN ('completed', 'partially_completed')
            ORDER BY created_at DESC, id DESC
        """
        try:
            run_rows = query(sql, tuple(params))
            for row in run_rows:
                meta = {}
                try:
                    meta = json.loads(row["metadata_json"] or "{}")
                except Exception:
                    pass
                
                r_vid = meta.get("video_id")
                if r_vid:
                    runs_by_video_id.setdefault(str(r_vid).strip(), []).append(row)
                    
                r_path = meta.get("card_video_path") or (meta.get("external_card_video") or {}).get("path") or meta.get("video_path")
                if r_path:
                    runs_by_path.setdefault(str(r_path).strip(), []).append(row)
        except Exception:
            logging.getLogger("appcore.tabcut_selection").exception("failed to load AI evaluation runs for Tabcut")
            
    # 3. 关联回 item
    for item in items:
        vid = str(item.get("video_id") or "").strip()
        path = str(item.get("local_video_path") or "").strip()
        
        run = None
        auto_run_id = auto_evals.get(vid)
        if auto_run_id:
            for row in run_rows:
                if row["evaluation_run_id"] == auto_run_id:
                    run = row
                    break
                    
        if not run and vid in runs_by_video_id:
            run = runs_by_video_id[vid][0]
            
        if not run and path and path in runs_by_path:
            run = runs_by_path[path][0]
            
        if run:
            try:
                frontend_data = json.loads(run["frontend_json"] or "{}")
                summary_data = json.loads(run["summary_json"] or "{}")
                item["fine_ai_evaluation"] = {
                    "evaluation_run_id": run["evaluation_run_id"],
                    "run_id": run["evaluation_run_id"],
                    "status": run["status"],
                    "has_result": True,
                    "summary": summary_data,
                    "frontend": frontend_data,
                    "countries": frontend_data.get("countries", {})
                }
            except Exception:
                item["fine_ai_evaluation"] = None
        else:
            item["fine_ai_evaluation"] = None



def _hydrate_goods_items(payload: dict[str, Any]) -> dict[str, Any]:
    hydrated = dict(payload)
    items = []
    for row in payload.get("items") or []:
        item = dict(row)
        category = goods_category_for_source(item.get("source"))
        if category:
            item["source_category_id"] = category.id
            item["source_category_label"] = category.label
            item["source_category_name"] = category.name
        rank_info = _goods_rank_info(item.get("source"))
        if rank_info:
            item.update(rank_info)
        items.append(item)
    hydrated["items"] = items
    return hydrated


def _goods_rank_info(source: Any) -> dict[str, str] | None:
    parts = str(source or "").split("_")
    if len(parts) != 3 or parts[0] != "goods":
        return None
    kind, period = parts[1], parts[2]
    if kind not in GOODS_RANK_KIND_LABELS or period not in GOODS_RANK_PERIOD_LABELS:
        return None
    return {
        "goods_rank_kind": kind,
        "goods_rank_kind_label": GOODS_RANK_KIND_LABELS[kind],
        "goods_rank_period": period,
        "goods_rank_period_label": GOODS_RANK_PERIOD_LABELS[period],
    }


def _fill_missing(row: dict[str, Any], key: str, value: Any) -> None:
    if row.get(key) in (None, "") and value not in (None, ""):
        row[key] = value


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _hashtag_names(raw: Mapping[str, Any]) -> list[str]:
    tags = raw.get("hashtags")
    if not isinstance(tags, list):
        return []
    names: list[str] = []
    for tag in tags:
        if isinstance(tag, Mapping):
            name = str(tag.get("hashtagName") or "").strip()
            if name:
                names.append(name)
    return names[:4]


def _first_raw_item(raw: Mapping[str, Any]) -> Mapping[str, Any] | None:
    items = raw.get("itemList")
    if isinstance(items, list) and items and isinstance(items[0], Mapping):
        return items[0]
    if raw.get("itemId") or raw.get("itemName"):
        return raw
    return None


def _raw_item_url(raw_item: Mapping[str, Any]) -> str | None:
    for key in ("itemUrl", "productUrl", "tkItemUrl", "shopProductUrl", "shop_product_url", "tiktokProductUrl"):
        value = str(raw_item.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    return None


def _raw_item_price(raw_item: Mapping[str, Any]) -> Any:
    if raw_item.get("skuPrice") not in (None, ""):
        return raw_item.get("skuPrice")
    value = raw_item.get("priceAmount")
    if isinstance(value, Mapping):
        return value.get("local") or value.get("region")
    return value


def _raw_item_currency(raw_item: Mapping[str, Any]) -> Any:
    if raw_item.get("currencySymbol"):
        return raw_item.get("currencySymbol")
    value = raw_item.get("currencySymbolInfo")
    if isinstance(value, Mapping):
        return value.get("local") or value.get("region")
    return "$"


def _tiktok_product_url(item_id: Any) -> str | None:
    text = str(item_id or "").strip()
    if not text:
        return None
    return f"https://www.tiktok.com/shop/pdp/{text}"


def build_admin_required_response() -> TabcutResponse:
    return TabcutResponse({"error": "admin required"}, 403)


def _default_refresh_runner(*, biz_date: str | None, target_date: str | None, days: int = 30) -> dict[str, Any]:
    return {
        "ok": False,
        "message": "refresh runner is not configured in this process",
        "biz_date": biz_date,
        "target_date": target_date,
        "days": days,
    }


def build_tabcut_refresh_response(
    payload: Mapping[str, Any] | None,
    *,
    runner_fn: Callable[..., dict[str, Any]] = _default_refresh_runner,
) -> TabcutResponse:
    payload = payload or {}
    biz_date = str(payload.get("biz_date") or "").strip() or None
    target_date = str(payload.get("target_date") or "").strip() or None
    try:
        days = int(payload.get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    result = runner_fn(biz_date=biz_date, target_date=target_date, days=max(1, min(days, 30)))
    return TabcutResponse({"ok": bool(result.get("ok")), "result": result}, 202)


def get_video_candidate_detail(video_id: str) -> dict[str, Any] | None:
    row = store.get_video_candidate(video_id)
    if not row:
        return None
    hydrated = _hydrate_video_items({"items": [row]})
    items = hydrated.get("items")
    return items[0] if items else None


def import_tabcut_video(
    video_id: str,
    owner_id: int,
    actor_user_id: int,
    *,
    target_product_id: int | None = None,
) -> dict[str, Any]:
    from appcore import local_media_storage, medias, object_keys
    from appcore.db import query_one
    from appcore.tabcut_selection import video_localization

    normalized_video_id = str(video_id or "").strip()
    if not normalized_video_id:
        raise ValueError("video_id required")
    if int(owner_id or 0) <= 0:
        raise ValueError("owner_id required")

    row = store.get_video_candidate(normalized_video_id)
    if not row:
        raise ValueError(f"TABCUT video not found: {normalized_video_id}")

    target_pid = int(target_product_id or 0)
    product_code = _tabcut_product_code(normalized_video_id)
    local_product_id = row.get("local_product_id")
    local_media_item_id = row.get("local_media_item_id")
    if target_pid <= 0 and local_product_id and local_media_item_id:
        mapped_product = query_one(
            "SELECT id, product_code FROM media_products WHERE id=%s AND deleted_at IS NULL LIMIT 1",
            (int(local_product_id),),
        )
        if mapped_product and str(mapped_product.get("product_code") or "") == product_code:
            return {
                "media_product_id": int(local_product_id),
                "media_item_id": int(local_media_item_id),
                "is_new_product": False,
                "product_link": _tabcut_product_url(row),
            }

    status = str(row.get("local_video_status") or "").strip().lower()
    if status not in {"success", "downloaded"} or not row.get("local_video_path"):
        raise ValueError("TABCUT 本地视频尚未就绪，请等待视频本地化完成")

    local_path = video_localization.resolve_local_video_path(str(row.get("local_video_path") or ""))
    if local_path is None or not local_path.exists():
        raise ValueError("TABCUT 本地视频文件不存在")

    local_cover_path = None
    if row.get("local_video_cover_path"):
        cover_path = video_localization.resolve_output_relative_file_path(
            str(row.get("local_video_cover_path") or "")
        )
        if cover_path and cover_path.exists():
            local_cover_path = cover_path

    if target_pid > 0:
        product = medias.get_product(target_pid)
        if not product:
            raise ValueError("target product not found")
        product_id = int(product["id"])
        owner_uid = int(product.get("user_id") or 0)
        if owner_uid <= 0:
            raise ValueError("target product owner missing")
        is_new = False
    else:
        product = medias.get_product_by_code(product_code)
        product_name = _tabcut_product_name(row, normalized_video_id)
        product_link = _tabcut_product_url(row)
        main_image = _tabcut_product_image(row)
        if product:
            product_id = int(product["id"])
            owner_uid = int(product.get("user_id") or owner_id)
            _sync_tabcut_product_fields(product_id, product_link=product_link, main_image=main_image)
            is_new = False
        else:
            product_id = int(
                medias.create_product(
                    int(owner_id),
                    product_name[:255],
                    source="TABCUT选品",
                    product_code=product_code,
                )
            )
            owner_uid = int(owner_id)
            _sync_tabcut_product_fields(product_id, product_link=product_link, main_image=main_image)
            is_new = True

    filename = _next_tabcut_material_filename(
        product_id,
        f"tabcut_{_safe_tabcut_id(normalized_video_id)}.mp4",
    )
    object_key = object_keys.build_media_object_key(owner_uid, product_id, filename)
    with open(local_path, "rb") as handle:
        local_media_storage.write_stream(object_key, handle)
    file_size = local_path.stat().st_size

    cover_object_key = None
    if local_cover_path:
        cover_filename = f"{Path(filename).stem}_cover{local_cover_path.suffix or '.jpg'}"
        cover_object_key = object_keys.build_media_object_key(owner_uid, product_id, cover_filename)
        with open(local_cover_path, "rb") as handle:
            local_media_storage.write_stream(cover_object_key, handle)

    duration_seconds = _duration_seconds(row.get("local_video_duration_seconds"))
    if duration_seconds is None:
        duration_seconds = _duration_seconds(row.get("video_duration_ms"), milliseconds=True)

    item_id = int(
        medias.create_item(
            product_id=product_id,
            user_id=owner_uid,
            filename=filename,
            object_key=object_key,
            display_name=filename,
            duration_seconds=duration_seconds,
            file_size=file_size,
            cover_object_key=cover_object_key,
            lang="en",
            skip_push=1,
        )
    )

    try:
        _save_tabcut_item_thumbnail(
            product_id=product_id,
            item_id=item_id,
            source_video_path=local_path,
            source_cover_path=local_cover_path,
            duration_seconds=duration_seconds,
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "Save thumbnail for imported Tabcut video failed video_id=%s",
            normalized_video_id,
        )

    store.set_video_local_import_binding(
        normalized_video_id,
        product_id=product_id,
        media_item_id=item_id,
    )

    return {
        "media_product_id": int(product_id),
        "media_item_id": int(item_id),
        "is_new_product": bool(is_new),
        "product_link": _tabcut_product_url(row),
        "actor_user_id": int(actor_user_id or 0),
    }


def _safe_tabcut_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._") or "video"


def _tabcut_product_code(video_id: str) -> str:
    return f"tabcut-{_safe_tabcut_id(video_id).lower()}"


def _tabcut_product_url(row: Mapping[str, Any]) -> str:
    explicit = str(
        row.get("primary_item_url")
        or row.get("item_url")
        or row.get("product_url")
        or row.get("shop_product_url")
        or row.get("tiktok_product_url")
        or ""
    ).strip()
    if explicit.startswith(("http://", "https://")):
        return explicit
    item_id = str(row.get("primary_item_id") or row.get("item_id") or "").strip()
    return f"https://www.tiktok.com/shop/pdp/{item_id}" if item_id else ""


def _tabcut_product_name(row: Mapping[str, Any], video_id: str) -> str:
    for key in ("primary_item_name", "item_name", "video_desc"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return f"TABCUT商品 {video_id}"


def _tabcut_product_image(row: Mapping[str, Any]) -> str:
    return str(row.get("primary_item_pic_url") or row.get("video_cover_url") or "").strip()


def _sync_tabcut_product_fields(product_id: int, *, product_link: str = "", main_image: str = "") -> None:
    from appcore.db import execute

    execute(
        "UPDATE media_products SET "
        "product_link=COALESCE(NULLIF(%s, ''), product_link), "
        "main_image=COALESCE(NULLIF(%s, ''), main_image) "
        "WHERE id=%s",
        (str(product_link or "").strip(), str(main_image or "").strip(), int(product_id)),
    )


def _duration_seconds(value: Any, *, milliseconds: bool = False) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed / 1000 if milliseconds else parsed


def _next_tabcut_material_filename(product_id: int, base_filename: str) -> str:
    from appcore.db import query_one

    base = Path(str(base_filename or "tabcut_video.mp4")).name or "tabcut_video.mp4"
    stem = Path(base).stem or "tabcut_video"
    ext = Path(base).suffix or ".mp4"
    candidate = f"{stem}{ext}"
    suffix = 2
    while True:
        row = query_one(
            "SELECT COUNT(*) AS cnt FROM media_items "
            "WHERE product_id=%s AND filename=%s AND deleted_at IS NULL",
            (int(product_id), candidate),
        )
        if int((row or {}).get("cnt") or 0) <= 0:
            return candidate
        candidate = f"{stem}_{suffix}{ext}"
        suffix += 1


def _save_tabcut_item_thumbnail(
    *,
    product_id: int,
    item_id: int,
    source_video_path: Path,
    source_cover_path: Path | None,
    duration_seconds: float | None,
) -> None:
    import shutil

    from config import OUTPUT_DIR
    import appcore.medias as medias_mod

    thumb_dir = os.path.join(OUTPUT_DIR, "media_thumbs", str(product_id))
    os.makedirs(thumb_dir, exist_ok=True)
    final_thumb_path = os.path.join(thumb_dir, f"{item_id}.jpg")

    if source_cover_path and source_cover_path.exists():
        shutil.copyfile(str(source_cover_path), final_thumb_path)
    else:
        from pipeline.ffutil import extract_thumbnail

        extracted = extract_thumbnail(str(source_video_path), thumb_dir, scale="360:-1")
        if not extracted:
            return
        if os.path.exists(final_thumb_path):
            os.remove(final_thumb_path)
        os.rename(extracted, final_thumb_path)

    relative_thumb_path = os.path.relpath(final_thumb_path, OUTPUT_DIR).replace("\\", "/")
    medias_mod.update_item_thumbnail_metadata(item_id, relative_thumb_path, duration_seconds)
