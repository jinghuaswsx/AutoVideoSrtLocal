"""Service responses for video creation routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class VideoCreationResponse:
    payload: dict[str, Any]
    status_code: int


def video_creation_flask_response(result: VideoCreationResponse):
    return jsonify(result.payload), result.status_code


def build_video_creation_payload_response(
    payload: dict[str, Any],
    status_code: int = 200,
) -> VideoCreationResponse:
    return VideoCreationResponse(payload, status_code)


def build_video_creation_error_response(
    error: str,
    status_code: int,
    **extra: Any,
) -> VideoCreationResponse:
    return VideoCreationResponse({"error": error, **extra}, status_code)


def build_video_creation_ok_status_response(
    status_code: int = 200,
    **extra: Any,
) -> VideoCreationResponse:
    return VideoCreationResponse({"status": "ok", **extra}, status_code)
