"""Service responses for order profit routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class OrderProfitResponse:
    payload: dict[str, Any]
    status_code: int


def order_profit_flask_response(result: OrderProfitResponse):
    return jsonify(result.payload), result.status_code


def build_order_profit_payload_response(
    payload: dict[str, Any],
    status_code: int = 200,
) -> OrderProfitResponse:
    return OrderProfitResponse(payload, status_code)


def build_order_profit_error_response(
    error: str,
    status_code: int,
    **extra: Any,
) -> OrderProfitResponse:
    return OrderProfitResponse({"error": error, **extra}, status_code)


def build_order_profit_ok_response(**extra: Any) -> OrderProfitResponse:
    return OrderProfitResponse({"ok": True, **extra}, 200)
