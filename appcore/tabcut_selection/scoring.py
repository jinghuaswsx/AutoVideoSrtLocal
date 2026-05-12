from __future__ import annotations

import math
from typing import Any


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _log_part(value: Any, weight: float) -> float:
    return math.log10(max(_num(value), 0.0) + 1.0) * weight


def score_candidate(metrics: dict[str, Any]) -> dict[str, Any]:
    parts = {
        "play_count": _log_part(metrics.get("play_count"), 4.0),
        "item_sold_count": _log_part(metrics.get("item_sold_count"), 7.0),
        "video_split_sold_count": _log_part(metrics.get("video_split_sold_count"), 5.0),
        "video_split_gmv": _log_part(metrics.get("video_split_gmv"), 3.0),
        "goods_sold_count_7d": _log_part(metrics.get("goods_sold_count_7d"), 9.0),
        "goods_gmv_7d": _log_part(metrics.get("goods_gmv_7d"), 6.0),
        "goods_sold_count_total": _log_part(metrics.get("goods_sold_count_total"), 2.0),
        "goods_gmv_total": _log_part(metrics.get("goods_gmv_total"), 2.0),
        "goods_growth_rate_7d": max(_num(metrics.get("goods_growth_rate_7d")), 0.0) * 3.0,
    }
    score = round(sum(parts.values()), 6)
    return {"score": score, "parts": parts}
