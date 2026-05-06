"""Shared Flask JSON responses for translation route modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class TranslateRouteResponse:
    payload: dict[str, Any]
    status_code: int


def translate_route_flask_response(result: TranslateRouteResponse):
    return jsonify(result.payload), result.status_code


def build_translate_route_payload_response(
    payload: dict[str, Any],
    status_code: int = 200,
) -> TranslateRouteResponse:
    return TranslateRouteResponse(payload, status_code)


def build_translate_route_error_response(
    error: str,
    status_code: int,
    **extra: Any,
) -> TranslateRouteResponse:
    return TranslateRouteResponse({"error": error, **extra}, status_code)
