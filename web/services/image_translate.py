"""Service responses for image translate routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class ImageTranslateResponse:
    payload: dict[str, Any]
    status_code: int


def image_translate_flask_response(result: ImageTranslateResponse):
    return jsonify(result.payload), result.status_code


def build_image_translate_payload_response(
    payload: dict[str, Any],
    status_code: int = 200,
) -> ImageTranslateResponse:
    return ImageTranslateResponse(payload, status_code)


def build_image_translate_error_response(
    error: str,
    status_code: int,
    **extra: Any,
) -> ImageTranslateResponse:
    return ImageTranslateResponse({"error": error, **extra}, status_code)


def build_image_translate_ok_response(
    status_code: int = 200,
    **extra: Any,
) -> ImageTranslateResponse:
    return ImageTranslateResponse({"ok": True, **extra}, status_code)
