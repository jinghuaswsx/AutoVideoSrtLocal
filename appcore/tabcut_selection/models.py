from __future__ import annotations

from datetime import datetime
from typing import Any


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
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

    return {
        "video_id": video_id,
        "region": _text(row.get("region")) or "US",
        "author_name": _text(row.get("authorName") or row.get("authorNickname") or row.get("authorUniqueId")),
        "author_avatar_url": _text(row.get("authorAvatarUrl")),
        "video_cover_url": _text(row.get("videoCoverUrl")),
        "tk_video_url": _text(row.get("tkVideoUrl")),
        "video_desc": _text(row.get("videoDesc")),
        "video_duration_ms": _int_or_none(row.get("videoDuration")),
        "create_time": _parse_datetime(row.get("createTime")),
        "primary_item_id": _text(primary.get("itemId") or row.get("itemId")),
        "primary_item_name": _text(primary.get("itemName") or row.get("itemName")),
        "source_sort": source_sort,
        "rank_position": _int_or_none(row.get("rank")),
        "play_count": _int_or_none(row.get("playCount") or row.get("playCountTotal")),
        "like_count": _int_or_none(row.get("likeCount") or row.get("likeCountTotal")),
        "share_count": _int_or_none(row.get("shareCount") or row.get("shareCountTotal")),
        "comment_count": _int_or_none(row.get("commentCount") or row.get("commentCountTotal")),
        "item_sold_count": _int_or_none(row.get("itemSoldCount")),
        "video_split_sold_count": _int_or_none(row.get("videoSplitSoldCount")),
        "video_split_gmv": _first_local_amount({"gmv": row.get("videoSplitGmv")}, "gmv"),
        "related_item_id": _text(primary.get("itemId") or row.get("itemId")),
        "related_item_name": _text(primary.get("itemName") or row.get("itemName")),
        "raw": _safe_raw(row),
    }


def normalize_goods_row(row: dict[str, Any], *, source: str | None = None) -> dict[str, Any]:
    price_min, price_max = _price_bounds(row.get("priceList"))
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
        "category_l1_name": _text(row.get("categoryLv1Name") or row.get("categoryName")),
        "category_l2_id": _text(row.get("categoryLv2Id")),
        "category_l2_name": _text(row.get("categoryLv2Name")),
        "category_l3_id": _text(row.get("categoryLv3Id")),
        "category_l3_name": _text(row.get("categoryLv3Name")),
        "seller_id": _text(row.get("sellerId")),
        "seller_name": _text(row.get("sellerName")),
        "seller_type": _text(row.get("sellerType")),
        "source": source,
        "rank_position": _int_or_none(row.get("rank")),
        "price_min": price_min,
        "price_max": price_max,
        "commission_rate": _float_or_none(row.get("commissionRate")),
        "sold_count_1d": _int_or_none(row.get("soldCount1d")),
        "sold_count_7d": _int_or_none(row.get("soldCount7d")),
        "sold_count_30d": _int_or_none(row.get("soldCount30d")),
        "sold_count_total": _int_or_none(row.get("soldCountTotal") or sold_info.get("total")),
        "sold_count_period": _int_or_none(row.get("soldCountPeriod") or sold_info.get("periodCurrent")),
        "sold_growth_rate_1d": _float_or_none(row.get("soldGrowthRate1d")),
        "sold_growth_rate_7d": _float_or_none(row.get("soldGrowthRate7d")),
        "sold_growth_rate_30d": _float_or_none(row.get("soldGrowthRate30d")),
        "sold_growth_rate_period": _float_or_none(row.get("soldCountGrowthRate")),
        "gmv_1d": _first_local_amount(gmv, "period1d"),
        "gmv_7d": _first_local_amount(gmv, "period7d"),
        "gmv_30d": _first_local_amount(gmv, "period30d"),
        "gmv_total": _first_local_amount(gmv, "total"),
        "gmv_period": _first_local_amount(gmv, "periodCurrent"),
        "related_video_count": _int_or_none(row.get("relatedVideoCount") or (row.get("relatedVideoInfo") or {}).get("period90d")),
        "related_creator_count": _int_or_none(row.get("relatedCreatorCount") or (row.get("relatedCreatorInfo") or {}).get("period90d")),
        "related_live_count": _int_or_none((row.get("relatedLiveInfo") or {}).get("period90d")),
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
