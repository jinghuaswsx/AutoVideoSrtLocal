"""Service responses for admin TTS speedup evaluation routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class TtsSpeedupEvalResponse:
    payload: dict[str, Any]
    status_code: int = 200


def tts_speedup_eval_flask_response(result: TtsSpeedupEvalResponse):
    return jsonify(result.payload), result.status_code


def build_tts_speedup_list_fallback_response(
    *,
    rows: list[dict],
    summary: dict,
) -> TtsSpeedupEvalResponse:
    return TtsSpeedupEvalResponse(
        {
            "rows_count": len(rows),
            "summary": summary,
            "rows": rows,
        },
        200,
    )


def build_tts_speedup_retry_response(*, ok: bool, eval_id: int) -> TtsSpeedupEvalResponse:
    return TtsSpeedupEvalResponse({"ok": ok, "eval_id": eval_id}, 200)
