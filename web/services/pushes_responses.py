"""Shared Flask JSON responses for push management route modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class PushesRouteResponse:
    payload: Any
    status_code: int


def pushes_flask_response(result: PushesRouteResponse):
    return jsonify(result.payload), result.status_code


def build_pushes_payload_response(
    payload: Any,
    status_code: int = 200,
) -> PushesRouteResponse:
    return PushesRouteResponse(payload, status_code)


def build_pushes_error_response(
    error: str,
    status_code: int,
    **extra: Any,
) -> PushesRouteResponse:
    return PushesRouteResponse({"error": error, **extra}, status_code)
