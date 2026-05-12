from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from . import store


@dataclass(frozen=True)
class TabcutResponse:
    payload: dict[str, Any]
    status_code: int = 200


def build_videos_response(args: Mapping[str, Any]) -> TabcutResponse:
    return TabcutResponse(_hydrate_video_items(store.list_video_candidates(args)))


def build_goods_response(args: Mapping[str, Any]) -> TabcutResponse:
    return TabcutResponse(store.list_goods(args))


def _hydrate_video_items(payload: dict[str, Any]) -> dict[str, Any]:
    hydrated = dict(payload)
    items = []
    for row in payload.get("items") or []:
        item = dict(row)
        raw = _json_dict(item.pop("video_raw_json", None))
        item["hashtags"] = _hashtag_names(raw)
        raw_item = _first_raw_item(raw)
        if raw_item:
            _fill_missing(item, "primary_item_pic_url", raw_item.get("itemCoverUrl"))
            _fill_missing(item, "primary_item_name", raw_item.get("itemName"))
            _fill_missing(item, "primary_item_price_min", raw_item.get("skuPrice"))
            _fill_missing(item, "primary_item_sold_count", raw_item.get("soldCount"))
            _fill_missing(item, "currency_symbol", raw_item.get("currencySymbol") or "$")
            _fill_missing(item, "price_currency", raw_item.get("priceCurrency"))
            _fill_missing(item, "primary_item_url", _raw_item_url(raw_item))
        _fill_missing(item, "primary_item_url", _tiktok_product_url(item.get("primary_item_id")))
        items.append(item)
    hydrated["items"] = items
    return hydrated


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
    return None


def _raw_item_url(raw_item: Mapping[str, Any]) -> str | None:
    for key in ("itemUrl", "productUrl", "tkItemUrl", "shopProductUrl", "shop_product_url", "tiktokProductUrl"):
        value = str(raw_item.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    return None


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
