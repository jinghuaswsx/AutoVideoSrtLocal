"""Service helpers for media parcel cost suggestion responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Type

from appcore import parcel_cost_suggest


@dataclass(frozen=True)
class ParcelCostSuggestResponse:
    payload: dict
    status_code: int


def build_parcel_cost_suggest_response(
    product_id: int,
    args: Mapping[str, str],
    *,
    default_lookback_days: int = parcel_cost_suggest.DEFAULT_LOOKBACK_DAYS,
    error_type: Type[Exception] = parcel_cost_suggest.ParcelCostSuggestError,
    suggest_parcel_cost_fn: Callable[..., dict] = parcel_cost_suggest.suggest_parcel_cost,
) -> ParcelCostSuggestResponse:
    try:
        days = int(args.get("days") or default_lookback_days)
    except (TypeError, ValueError):
        return ParcelCostSuggestResponse({"error": "invalid_days"}, 400)
    days = max(7, min(90, days))

    try:
        suggestion = suggest_parcel_cost_fn(product_id, days=days)
    except error_type as exc:
        message = str(exc)
        if message == "no_orders":
            return ParcelCostSuggestResponse(
                {
                    "error": "no_orders",
                    "message": "该产品在店小秘还没有订单数据，无法估算实际小包成本",
                },
                404,
            )
        return ParcelCostSuggestResponse({"error": "dxm_failed", "message": message}, 502)
    except Exception as exc:  # pragma: no cover - defensive route boundary
        return ParcelCostSuggestResponse({"error": "dxm_failed", "message": str(exc)}, 502)

    return ParcelCostSuggestResponse({"ok": True, "suggestion": suggestion}, 200)
