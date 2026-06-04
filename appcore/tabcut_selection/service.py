from __future__ import annotations

import json
from dataclasses import dataclass
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
