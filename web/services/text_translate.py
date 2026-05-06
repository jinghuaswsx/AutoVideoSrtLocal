"""Service responses for text translate routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class TextTranslateResponse:
    payload: dict[str, Any]
    status_code: int


def text_translate_flask_response(result: TextTranslateResponse):
    return jsonify(result.payload), result.status_code


def build_text_translate_created_response(*, task_id: str) -> TextTranslateResponse:
    return TextTranslateResponse({"id": task_id}, 201)


def build_text_translate_not_found_response() -> TextTranslateResponse:
    return TextTranslateResponse({"error": "not found"}, 404)


def build_text_translate_missing_source_response() -> TextTranslateResponse:
    return TextTranslateResponse({"error": "source_text or segments required"}, 400)


def build_text_translate_empty_segments_response() -> TextTranslateResponse:
    return TextTranslateResponse({"error": "no valid segments"}, 400)


def build_text_translate_exception_response(exc: Exception) -> TextTranslateResponse:
    return TextTranslateResponse({"error": str(exc)}, 500)


def build_text_translate_success_response(
    *,
    result: dict,
    model: str,
) -> TextTranslateResponse:
    return TextTranslateResponse({"result": result, "model": model}, 200)


def build_text_translate_delete_success_response() -> TextTranslateResponse:
    return TextTranslateResponse({"status": "ok"}, 200)
