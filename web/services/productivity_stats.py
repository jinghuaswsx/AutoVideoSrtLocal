"""Service responses for productivity stats routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class ProductivityStatsResponse:
    payload: dict[str, Any]
    status_code: int


def productivity_stats_flask_response(result: ProductivityStatsResponse):
    return jsonify(result.payload), result.status_code


def build_productivity_stats_admin_required_response() -> ProductivityStatsResponse:
    return ProductivityStatsResponse({"error": "admin_required"}, 403)


def build_productivity_stats_summary_response(
    *,
    from_dt,
    to_dt,
    daily_throughput: list[dict],
    pass_rate: list[dict],
    rework_rate: list[dict],
) -> ProductivityStatsResponse:
    return ProductivityStatsResponse(
        {
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "daily_throughput": [_serialize_row(row) for row in daily_throughput],
            "pass_rate": [_serialize_row(row) for row in pass_rate],
            "rework_rate": [_serialize_row(row) for row in rework_rate],
        },
        200,
    )


def build_productivity_stats_bad_param_response(exc: Exception) -> ProductivityStatsResponse:
    return ProductivityStatsResponse({"error": "bad_param", "detail": str(exc)}, 400)


def build_productivity_stats_internal_error_response(exc: Exception) -> ProductivityStatsResponse:
    return ProductivityStatsResponse({"error": "internal", "detail": str(exc)}, 500)


def _serialize_row(row: dict) -> dict:
    out = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif hasattr(value, "__float__") and not isinstance(value, (int, float, bool)):
            out[key] = float(value)
        else:
            out[key] = value
    return out
