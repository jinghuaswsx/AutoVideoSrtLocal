from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from appcore.meta_hot_posts import categories, product_analysis, store

MARK_STATUS_OK = "ok"
MARK_STATUS_BAD = "bad"


@dataclass(frozen=True)
class MetaHotPostsResponse:
    payload: dict[str, Any]
    status_code: int = 200


def _decode_sku_json(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, str) and value:
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [dict(item) for item in parsed if isinstance(item, dict)]
    return []


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


def _hydrate_item(row: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["sku_prices"] = _decode_sku_json(item.pop("sku_prices_json", None))
    item["sku_count"] = len(item["sku_prices"])
    item.setdefault("analysis_status", "pending")
    item["category_l1_zh"] = categories.category_label_zh(item.get("category_l1"))
    source_message = str(item.get("message_html") or "")
    translated_message = str(item.get("message_zh_html") or "").strip()
    item["message_source_html"] = source_message
    if translated_message:
        item["message_html"] = translated_message
    mark_status = _normalize_mark_status(item.get("mark_status"))
    if not mark_status and _bool_payload(item.get("is_marked")):
        mark_status = MARK_STATUS_BAD
    item["mark_status"] = mark_status
    item["is_marked"] = bool(mark_status)
    return item


def build_list_response(args: Mapping[str, Any]) -> MetaHotPostsResponse:
    payload = store.list_hot_posts(args)
    payload["items"] = [_hydrate_item(item) for item in payload.get("items") or []]
    return MetaHotPostsResponse(payload)


def category_options() -> list[dict[str, Any]]:
    dynamic = store.list_category_options()
    if dynamic:
        seen = {str(item.get("value") or "") for item in dynamic}
        hydrated = [
            categories.category_option(item.get("value") or item.get("label"))
            for item in dynamic
        ]
        return hydrated + [item for item in categories.category_options() if item["value"] not in seen]
    return categories.category_options()


def build_category_options_response() -> MetaHotPostsResponse:
    return MetaHotPostsResponse({"items": category_options()})


def build_category_prompt_response() -> MetaHotPostsResponse:
    prompt = product_analysis.build_category_prompt(
        product_title="{product_title}",
        product_url="{product_url}",
    )
    return MetaHotPostsResponse(
        {
            "prompt": prompt,
            "categories": categories.TIKTOK_SHOP_US_L1_CATEGORIES,
            "use_case": "meta_hot_posts.categorize",
            "model": product_analysis.CATEGORY_MODEL,
            "provider": product_analysis.CATEGORY_PROVIDER,
        }
    )


def build_failures_response(args: Mapping[str, Any]) -> MetaHotPostsResponse:
    try:
        limit = int(args.get("limit") or 100)
    except (TypeError, ValueError):
        limit = 100
    items = store.list_failed_product_analyses(limit=limit)
    return MetaHotPostsResponse({"items": items, "total": len(items), "limit": max(1, min(100, limit))})


def build_mark_response(
    post_id: int,
    payload: Mapping[str, Any] | None = None,
    *,
    user_id: int | None = None,
) -> MetaHotPostsResponse:
    payload = payload or {}
    if "mark_status" in payload or "status" in payload:
        mark_status = _normalize_mark_status(payload.get("mark_status", payload.get("status")))
    else:
        mark_status = MARK_STATUS_BAD if _bool_payload(payload.get("marked")) else None
    affected = store.set_hot_post_mark_status(post_id, mark_status=mark_status, user_id=user_id)
    if not affected:
        return MetaHotPostsResponse({"error": "not_found"}, 404)
    return MetaHotPostsResponse(
        {"ok": True, "id": int(post_id), "mark_status": mark_status, "is_marked": bool(mark_status)}
    )


def build_refresh_response() -> MetaHotPostsResponse:
    from appcore.meta_hot_posts import scheduler

    return MetaHotPostsResponse({"ok": True, "result": scheduler.sync_tick_once(target_count=500)}, 202)


def build_analyze_response(payload: Mapping[str, Any] | None = None) -> MetaHotPostsResponse:
    from appcore.meta_hot_posts import scheduler

    payload = payload or {}
    try:
        limit = int(payload.get("limit") or scheduler.SCHEDULED_ANALYSIS_LIMIT)
    except (TypeError, ValueError):
        limit = scheduler.SCHEDULED_ANALYSIS_LIMIT
    try:
        delay = float(
            payload.get("per_item_delay_seconds")
            if payload.get("per_item_delay_seconds") is not None
            else scheduler.SCHEDULED_ANALYSIS_DELAY_SECONDS
        )
    except (TypeError, ValueError):
        delay = scheduler.SCHEDULED_ANALYSIS_DELAY_SECONDS
    try:
        user_id = int(payload.get("user_id") or 0) or None
    except (TypeError, ValueError):
        user_id = None
    recategorize_only = bool(payload.get("recategorize_only") or payload.get("recategorize"))
    include_all_categories = bool(payload.get("include_all_categories") or payload.get("include_all"))
    return MetaHotPostsResponse(
        {
            "ok": True,
            "result": scheduler.analysis_tick_once(
                limit=limit,
                user_id=user_id,
                recategorize_only=recategorize_only,
                include_all_categories=include_all_categories,
                per_item_delay_seconds=delay,
            ),
        },
        202,
    )


def build_translate_response(payload: Mapping[str, Any] | None = None) -> MetaHotPostsResponse:
    from appcore.meta_hot_posts import scheduler

    payload = payload or {}
    try:
        limit = int(payload.get("limit") or scheduler.SCHEDULED_TRANSLATION_LIMIT)
    except (TypeError, ValueError):
        limit = scheduler.SCHEDULED_TRANSLATION_LIMIT
    try:
        delay = float(
            payload.get("per_item_delay_seconds")
            if payload.get("per_item_delay_seconds") is not None
            else scheduler.SCHEDULED_TRANSLATION_DELAY_SECONDS
        )
    except (TypeError, ValueError):
        delay = scheduler.SCHEDULED_TRANSLATION_DELAY_SECONDS
    try:
        user_id = int(payload.get("user_id") or 0) or None
    except (TypeError, ValueError):
        user_id = None
    return MetaHotPostsResponse(
        {
            "ok": True,
            "result": scheduler.translation_tick_once(
                limit=limit,
                user_id=user_id,
                per_item_delay_seconds=delay,
            ),
        },
        202,
    )
