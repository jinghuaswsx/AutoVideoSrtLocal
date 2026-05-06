from __future__ import annotations

from web.services.tts_speedup_eval import (
    build_tts_speedup_list_fallback_response,
    build_tts_speedup_retry_response,
)


def test_tts_speedup_list_fallback_response_wraps_rows_and_summary():
    rows = [{"id": 1, "task_id": "task-a"}]
    summary = {"total": 1}

    result = build_tts_speedup_list_fallback_response(rows=rows, summary=summary)

    assert result.status_code == 200
    assert result.payload == {
        "rows_count": 1,
        "summary": summary,
        "rows": rows,
    }


def test_tts_speedup_retry_response_reports_eval_id_and_status():
    result = build_tts_speedup_retry_response(ok=False, eval_id=42)

    assert result.status_code == 200
    assert result.payload == {"ok": False, "eval_id": 42}
