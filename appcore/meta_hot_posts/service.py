from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from appcore.meta_hot_posts import categories, product_analysis, store


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


def _hydrate_item(row: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["sku_prices"] = _decode_sku_json(item.pop("sku_prices_json", None))
    item["sku_count"] = len(item["sku_prices"])
    item.setdefault("analysis_status", "pending")
    return item


def build_list_response(args: Mapping[str, Any]) -> MetaHotPostsResponse:
    payload = store.list_hot_posts(args)
    payload["items"] = [_hydrate_item(item) for item in payload.get("items") or []]
    return MetaHotPostsResponse(payload)


def category_options() -> list[dict[str, Any]]:
    dynamic = store.list_category_options()
    if dynamic:
        seen = {str(item.get("value") or "") for item in dynamic}
        return dynamic + [item for item in categories.category_options() if item["value"] not in seen]
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
            "model": "gemini-3-flash-preview",
        }
    )


def build_failures_response(args: Mapping[str, Any]) -> MetaHotPostsResponse:
    try:
        limit = int(args.get("limit") or 100)
    except (TypeError, ValueError):
        limit = 100
    items = store.list_failed_product_analyses(limit=limit)
    return MetaHotPostsResponse({"items": items, "total": len(items), "limit": max(1, min(100, limit))})


def build_refresh_response() -> MetaHotPostsResponse:
    from appcore.meta_hot_posts import scheduler

    return MetaHotPostsResponse({"ok": True, "result": scheduler.sync_tick_once(target_count=500)}, 202)


def build_analyze_response(payload: Mapping[str, Any] | None = None) -> MetaHotPostsResponse:
    from appcore.meta_hot_posts import scheduler

    payload = payload or {}
    try:
        limit = int(payload.get("limit") or 100)
    except (TypeError, ValueError):
        limit = 100
    try:
        user_id = int(payload.get("user_id") or 0) or None
    except (TypeError, ValueError):
        user_id = None
    return MetaHotPostsResponse({"ok": True, "result": scheduler.analysis_tick_once(limit=limit, user_id=user_id)}, 202)
