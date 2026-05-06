"""Service responses for settings AI pricing routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class AiPricingResponse:
    payload: dict[str, Any]
    status_code: int


_MISSING = object()


def settings_ai_pricing_flask_response(result: AiPricingResponse):
    return jsonify(result.payload), result.status_code


def build_ai_pricing_list_response(rows: list[dict[str, Any]]) -> AiPricingResponse:
    return AiPricingResponse({"items": rows}, 200)


def build_ai_pricing_success_response(
    item: Any = _MISSING,
    *,
    status_code: int = 200,
) -> AiPricingResponse:
    payload: dict[str, Any] = {"ok": True}
    if item is not _MISSING:
        payload["item"] = item
    return AiPricingResponse(payload, status_code)


def build_ai_pricing_error_response(exc: Exception) -> AiPricingResponse:
    return AiPricingResponse({"error": str(exc)}, 400)


def build_ai_pricing_not_found_response() -> AiPricingResponse:
    return AiPricingResponse({"error": "not found"}, 404)
