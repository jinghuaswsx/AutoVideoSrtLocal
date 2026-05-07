"""Service responses for product profit report routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class ProductProfitReportResponse:
    payload: dict[str, Any]
    status_code: int


def product_profit_report_flask_response(result: ProductProfitReportResponse):
    return jsonify(result.payload), result.status_code


def build_product_profit_report_payload_response(
    payload: dict[str, Any],
    status_code: int = 200,
) -> ProductProfitReportResponse:
    return ProductProfitReportResponse(payload, status_code)


def build_product_profit_report_error_response(
    error: str,
    status_code: int,
    **extra: Any,
) -> ProductProfitReportResponse:
    return ProductProfitReportResponse({"error": error, **extra}, status_code)
