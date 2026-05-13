from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Mapping


_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _nonnegative_int_or_none(value: Any) -> int | None:
    number = _int_or_none(value)
    if number is None:
        return None
    return max(number, 0)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            match = _NUMBER_RE.search(text)
            if not match:
                return None
            return float(match.group(0))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_local_amount(payload: Any, *keys: str) -> float | None:
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, dict):
        return _float_or_none(
            current.get("localAmount")
            or current.get("amount")
            or current.get("value")
            or current.get("usdAmount")
            or current.get("local")
            or current.get("region")
        )
    return _float_or_none(current)


def _price_bounds(price_list: Any) -> tuple[float | None, float | None]:
    values: list[float] = []
    if isinstance(price_list, list):
        for item in price_list:
            value = _first_local_amount({"x": item}, "x") if isinstance(item, dict) else _float_or_none(item)
            if value is not None:
                values.append(value)
    if not values:
        return None, None
    return min(values), max(values)


def _first_item(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    items = payload.get("itemList")
    if isinstance(items, list) and items and isinstance(items[0], Mapping):
        return items[0]
    return None


def _currency_from_payload(payload: Mapping[str, Any]) -> str | None:
    for key in ("currencySymbol", "priceCurrency", "currency"):
        value = _text(payload.get(key))
        if value:
            return value
    info = payload.get("currencySymbolInfo")
    if isinstance(info, Mapping):
        return _text(info.get("local") or info.get("region") or info.get("symbol"))
    return None


def _price_bounds_from_payload(payload: Mapping[str, Any]) -> tuple[float | None, float | None]:
    direct_min = _float_or_none(payload.get("price_min"))
    direct_max = _float_or_none(payload.get("price_max"))
    if direct_min is not None or direct_max is not None:
        return direct_min if direct_min is not None else direct_max, direct_max if direct_max is not None else direct_min

    price_min, price_max = _price_bounds(payload.get("priceList"))
    if price_min is not None or price_max is not None:
        return price_min, price_max

    for key in ("skuPrice", "priceAmount", "priceOrigin", "itemPrice", "price"):
        value = payload.get(key)
        price = _first_local_amount({"price": value}, "price") if isinstance(value, Mapping) else _float_or_none(value)
        if price is not None:
            return price, price
    return None, None


def extract_primary_item_price_fields(*payloads: Any) -> dict[str, Any]:
    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        sources: list[Mapping[str, Any]] = []
        primary = _first_item(payload)
        if primary:
            sources.append(primary)
        sources.append(payload)

        for source in sources:
            price_min, price_max = _price_bounds_from_payload(source)
            if price_min is None and price_max is None:
                continue
            if price_min is None:
                price_min = price_max
            if price_max is None:
                price_max = price_min
            currency = next(
                (symbol for symbol in (_currency_from_payload(item) for item in sources) if symbol),
                None,
            )
            return {
                "primary_item_price_min": price_min,
                "primary_item_price_max": price_max,
                "price_currency": currency,
            }
    return {
        "primary_item_price_min": None,
        "primary_item_price_max": None,
        "price_currency": None,
    }


def _parse_datetime(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    normalized = text.replace("T", " ")[:19]
    try:
        return datetime.fromisoformat(normalized).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return normalized


def normalize_video_row(row: dict[str, Any], *, source_sort: str | None = None) -> dict[str, Any]:
    items = row.get("itemList") if isinstance(row.get("itemList"), list) else []
    primary = items[0] if items and isinstance(items[0], dict) else {}
    video_id = _text(row.get("videoId"))
    price_fields = extract_primary_item_price_fields(primary, row)

    return {
        "video_id": video_id,
        "region": _text(row.get("region")) or "US",
        "author_name": _text(row.get("authorName") or row.get("authorNickname") or row.get("authorUniqueId")),
        "author_avatar_url": _text(row.get("authorAvatarUrl")),
        "video_cover_url": _text(row.get("videoCoverUrl")),
        "tk_video_url": _text(row.get("tkVideoUrl")),
        "video_desc": _text(row.get("videoDesc")),
        "video_duration_ms": _nonnegative_int_or_none(row.get("videoDuration")),
        "create_time": _parse_datetime(row.get("createTime")),
        "primary_item_id": _text(primary.get("itemId") or row.get("itemId")),
        "primary_item_name": _text(primary.get("itemName") or row.get("itemName")),
        "primary_item_price_min": price_fields["primary_item_price_min"],
        "primary_item_price_max": price_fields["primary_item_price_max"],
        "price_currency": price_fields["price_currency"],
        "source_sort": source_sort,
        "rank_position": _nonnegative_int_or_none(row.get("rank")),
        "play_count": _nonnegative_int_or_none(row.get("playCount") or row.get("playCountTotal")),
        "like_count": _nonnegative_int_or_none(row.get("likeCount") or row.get("likeCountTotal")),
        "share_count": _nonnegative_int_or_none(row.get("shareCount") or row.get("shareCountTotal")),
        "comment_count": _nonnegative_int_or_none(row.get("commentCount") or row.get("commentCountTotal")),
        "item_sold_count": _nonnegative_int_or_none(row.get("itemSoldCount") or row.get("videoSplitSoldCount")),
        "video_split_sold_count": _nonnegative_int_or_none(row.get("videoSplitSoldCount")),
        "video_split_gmv": _first_local_amount({"gmv": row.get("videoSplitGmv")}, "gmv"),
        "related_item_id": _text(primary.get("itemId") or row.get("itemId")),
        "related_item_name": _text(primary.get("itemName") or row.get("itemName")),
        "raw": _safe_raw(row),
    }


def normalize_goods_row(row: dict[str, Any], *, source: str | None = None) -> dict[str, Any]:
    price_min, price_max = _price_bounds(row.get("priceList"))
    if price_min is None and price_max is None:
        price = _first_local_amount({"price": row.get("priceAmount")}, "price")
        price_min, price_max = price, price
    gmv = row.get("gmvInfo") if isinstance(row.get("gmvInfo"), dict) else {}
    sold_info = row.get("soldCountInfo") if isinstance(row.get("soldCountInfo"), dict) else {}

    return {
        "item_id": _text(row.get("itemId")),
        "region": _text(row.get("region")) or "US",
        "item_name": _text(row.get("itemName")),
        "item_pic_url": _text(row.get("itemPicUrl") or row.get("itemCoverUrl")),
        "category_id": _text(row.get("categoryId")),
        "category_name": _text(row.get("categoryName")),
        "category_l1_id": _text(row.get("categoryLv1Id")),
        "category_l1_name": _text(row.get("categoryLv1Name") or row.get("itemTkLv1Name") or row.get("categoryName")),
        "category_l2_id": _text(row.get("categoryLv2Id")),
        "category_l2_name": _text(row.get("categoryLv2Name") or row.get("itemTkLv2Name")),
        "category_l3_id": _text(row.get("categoryLv3Id")),
        "category_l3_name": _text(row.get("categoryLv3Name") or row.get("itemTkLv3Name")),
        "seller_id": _text(row.get("sellerId")),
        "seller_name": _text(row.get("sellerName")),
        "seller_type": _text(row.get("sellerType")),
        "source": source,
        "rank_position": _nonnegative_int_or_none(row.get("rank")),
        "price_min": price_min,
        "price_max": price_max,
        "commission_rate": _float_or_none(row.get("commissionRate")),
        "sold_count_1d": _nonnegative_int_or_none(row.get("soldCount1d") or row.get("itemSoldCount1d")),
        "sold_count_7d": _nonnegative_int_or_none(row.get("soldCount7d") or row.get("itemSoldCount7d")),
        "sold_count_30d": _nonnegative_int_or_none(row.get("soldCount30d") or row.get("itemSoldCount30d")),
        "sold_count_total": _nonnegative_int_or_none(row.get("soldCountTotal") or row.get("itemSoldCountTotal") or sold_info.get("total")),
        "sold_count_period": _nonnegative_int_or_none(row.get("soldCountPeriod") or sold_info.get("periodCurrent")),
        "sold_growth_rate_1d": _float_or_none(row.get("soldGrowthRate1d")),
        "sold_growth_rate_7d": _float_or_none(row.get("soldGrowthRate7d")),
        "sold_growth_rate_30d": _float_or_none(row.get("soldGrowthRate30d")),
        "sold_growth_rate_period": _float_or_none(row.get("soldCountGrowthRate")),
        "gmv_1d": _first_local_amount(gmv, "period1d"),
        "gmv_7d": _first_local_amount(gmv, "period7d"),
        "gmv_30d": _first_local_amount(gmv, "period30d"),
        "gmv_total": _first_local_amount(gmv, "total"),
        "gmv_period": _first_local_amount(gmv, "periodCurrent"),
        "related_video_count": _nonnegative_int_or_none(row.get("relatedVideoCount") or (row.get("relatedVideoInfo") or {}).get("period90d")),
        "related_creator_count": _nonnegative_int_or_none(row.get("relatedCreatorCount") or (row.get("relatedCreatorInfo") or {}).get("period90d")),
        "related_live_count": _nonnegative_int_or_none((row.get("relatedLiveInfo") or {}).get("period90d")),
        "discover_time": _parse_datetime(row.get("discoverTime")),
        "raw": _safe_raw(row),
    }


def _safe_raw(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in {"videoUrl", "videoPlayUrl"}:
                continue
            out[key] = _safe_raw(item)
        return out
    if isinstance(value, list):
        return [_safe_raw(item) for item in value]
    return value
