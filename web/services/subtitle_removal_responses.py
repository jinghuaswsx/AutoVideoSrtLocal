"""Shared Flask JSON responses for subtitle-removal route modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class SubtitleRemovalRouteResponse:
    payload: Any
    status_code: int


def subtitle_removal_flask_response(result: SubtitleRemovalRouteResponse):
    return jsonify(result.payload), result.status_code


def build_subtitle_removal_payload_response(
    payload: Any,
    status_code: int = 200,
) -> SubtitleRemovalRouteResponse:
    return SubtitleRemovalRouteResponse(payload, status_code)


def build_subtitle_removal_error_response(
    error: str,
    status_code: int,
    **extra: Any,
) -> SubtitleRemovalRouteResponse:
    return SubtitleRemovalRouteResponse({"error": error, **extra}, status_code)
