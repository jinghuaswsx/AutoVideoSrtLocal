from __future__ import annotations

from datetime import datetime

from web.services.translation_quality import (
    build_translation_quality_admin_only_response,
    build_translation_quality_assessment_in_progress_response,
    build_translation_quality_list_response,
    build_translation_quality_not_found_response,
    build_translation_quality_started_response,
)


def test_translation_quality_list_response_serializes_rows_and_task_state():
    result = build_translation_quality_list_response(
        rows=[
            {
                "run_id": 2,
                "status": "completed",
                "translation_dimensions": '{"accuracy": 95}',
                "tts_issues": '["pause"]',
                "prompt_input": "",
                "created_at": datetime(2026, 5, 6, 8, 30, 0),
                "completed_at": datetime(2026, 5, 6, 8, 31, 2),
            }
        ],
        task_evals_invalidated_at="2026-05-06T08:00:00",
    )

    assert result.status_code == 200
    assert result.payload == {
        "assessments": [
            {
                "run_id": 2,
                "status": "completed",
                "translation_dimensions": {"accuracy": 95},
                "tts_issues": ["pause"],
                "prompt_input": "",
                "created_at": "2026-05-06T08:30:00",
                "completed_at": "2026-05-06T08:31:02",
            }
        ],
        "task_evals_invalidated_at": "2026-05-06T08:00:00",
    }


def test_translation_quality_list_response_includes_red_flags():
    result = build_translation_quality_list_response(
        rows=[
            {
                "run_id": 3,
                "status": "done",
                "translation_score": 68,
                "translation_dimensions": '{"ending_integrity": 80}',
                "created_at": None,
                "completed_at": None,
            },
            {
                "run_id": 4,
                "status": "done",
                "translation_score": 88,
                "translation_dimensions": '{"ending_integrity": 55}',
                "created_at": None,
                "completed_at": None,
            },
        ],
        task_evals_invalidated_at=None,
    )

    first, second = result.payload["assessments"]
    assert first["is_red"] is True
    assert first["red_flags"] == {"translation_score": True, "ending_integrity": False}
    assert second["is_red"] is True
    assert second["red_flags"] == {"translation_score": False, "ending_integrity": True}


def test_translation_quality_error_responses_are_stable():
    not_found = build_translation_quality_not_found_response()
    admin_only = build_translation_quality_admin_only_response()
    in_progress = build_translation_quality_assessment_in_progress_response(run_id=7)

    assert not_found.status_code == 404
    assert not_found.payload == {"error": "Task not found"}
    assert admin_only.status_code == 403
    assert admin_only.payload == {"error": "admin only"}
    assert in_progress.status_code == 409
    assert in_progress.payload == {"error": "assessment_in_progress", "run_id": 7}


def test_translation_quality_started_response_wraps_run_id():
    result = build_translation_quality_started_response(run_id=3)

    assert result.status_code == 200
    assert result.payload == {"ok": True, "run_id": 3}
