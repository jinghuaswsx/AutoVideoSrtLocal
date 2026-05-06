"""Shared Flask JSON responses for order analytics route modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class OrderAnalyticsRouteResponse:
    payload: Any
    status_code: int


def order_analytics_flask_response(result: OrderAnalyticsRouteResponse):
    return jsonify(result.payload), result.status_code


def build_order_analytics_payload_response(
    payload: Any,
    status_code: int = 200,
) -> OrderAnalyticsRouteResponse:
    return OrderAnalyticsRouteResponse(payload, status_code)


def build_order_analytics_error_response(
    error: str,
    status_code: int,
    **extra: Any,
) -> OrderAnalyticsRouteResponse:
    return OrderAnalyticsRouteResponse({"error": error, **extra}, status_code)
