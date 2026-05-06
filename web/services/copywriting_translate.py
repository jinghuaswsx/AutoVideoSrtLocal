"""Service responses for copywriting translate routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class CopywritingTranslateResponse:
    payload: dict[str, Any]
    status_code: int


def copywriting_translate_flask_response(result: CopywritingTranslateResponse):
    return jsonify(result.payload), result.status_code


def build_copywriting_translate_missing_source_copy_response() -> CopywritingTranslateResponse:
    return CopywritingTranslateResponse({"error": "source_copy_id 必填且为 int"}, 400)


def build_copywriting_translate_missing_target_lang_response() -> CopywritingTranslateResponse:
    return CopywritingTranslateResponse({"error": "target_lang 必填"}, 400)


def build_copywriting_translate_already_running_response() -> CopywritingTranslateResponse:
    return CopywritingTranslateResponse({"status": "already_running"}, 409)


def build_copywriting_translate_started_response(*, task_id: str) -> CopywritingTranslateResponse:
    return CopywritingTranslateResponse({"task_id": task_id}, 202)
