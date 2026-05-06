"""Service responses for translation quality routes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class TranslationQualityResponse:
    payload: dict[str, Any]
    status_code: int


def translation_quality_flask_response(result: TranslationQualityResponse):
    return jsonify(result.payload), result.status_code


def build_translation_quality_not_found_response() -> TranslationQualityResponse:
    return TranslationQualityResponse({"error": "Task not found"}, 404)


def build_translation_quality_list_response(
    *,
    rows: list[dict],
    task_evals_invalidated_at,
) -> TranslationQualityResponse:
    return TranslationQualityResponse(
        {
            "assessments": [_row_to_dict(row) for row in rows],
            "task_evals_invalidated_at": task_evals_invalidated_at,
        },
        200,
    )


def build_translation_quality_admin_only_response() -> TranslationQualityResponse:
    return TranslationQualityResponse({"error": "admin only"}, 403)


def build_translation_quality_assessment_in_progress_response(
    *,
    run_id: int,
) -> TranslationQualityResponse:
    return TranslationQualityResponse(
        {"error": "assessment_in_progress", "run_id": run_id},
        409,
    )


def build_translation_quality_started_response(*, run_id: int) -> TranslationQualityResponse:
    return TranslationQualityResponse({"ok": True, "run_id": run_id}, 200)


def _row_to_dict(row: dict) -> dict:
    out = dict(row)
    for col in (
        "translation_dimensions",
        "tts_dimensions",
        "translation_issues",
        "translation_highlights",
        "tts_issues",
        "tts_highlights",
        "prompt_input",
        "raw_response",
    ):
        value = out.get(col)
        if isinstance(value, str) and value:
            try:
                out[col] = json.loads(value)
            except Exception:
                pass
    for col in ("created_at", "completed_at"):
        if out.get(col):
            out[col] = (
                out[col].isoformat() if hasattr(out[col], "isoformat") else str(out[col])
            )
    return out
