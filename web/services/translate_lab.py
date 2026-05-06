"""Service responses for translate lab routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class TranslateLabResponse:
    payload: dict[str, Any]
    status_code: int


def translate_lab_flask_response(result: TranslateLabResponse):
    return jsonify(result.payload), result.status_code


def build_translate_lab_error_response(
    error: str,
    status_code: int,
) -> TranslateLabResponse:
    return TranslateLabResponse({"error": error}, status_code)


def build_translate_lab_created_response(
    *,
    task_id: str,
    source_language: str,
    target_language: str,
    voice_match_mode: str,
) -> TranslateLabResponse:
    return TranslateLabResponse(
        {
            "task_id": task_id,
            "source_language": source_language,
            "target_language": target_language,
            "voice_match_mode": voice_match_mode,
        },
        201,
    )


def build_translate_lab_ok_response(**extra: Any) -> TranslateLabResponse:
    return TranslateLabResponse({"ok": True, **extra}, 200)


def build_translate_lab_payload_response(
    payload: dict[str, Any],
) -> TranslateLabResponse:
    return TranslateLabResponse(payload, 200)


def build_translate_lab_voice_confirmed_response(
    chosen: dict[str, Any],
) -> TranslateLabResponse:
    return TranslateLabResponse({"ok": True, "chosen": chosen}, 200)


def build_translate_lab_sync_response(total: int) -> TranslateLabResponse:
    return TranslateLabResponse({"ok": True, "total": total}, 200)


def build_translate_lab_embed_response(count: int) -> TranslateLabResponse:
    return TranslateLabResponse({"ok": True, "count": count}, 200)
