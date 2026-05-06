from __future__ import annotations

from web.services.copywriting_translate import (
    build_copywriting_translate_already_running_response,
    build_copywriting_translate_missing_source_copy_response,
    build_copywriting_translate_missing_target_lang_response,
    build_copywriting_translate_started_response,
)


def test_copywriting_translate_validation_responses_are_stable():
    source_result = build_copywriting_translate_missing_source_copy_response()
    target_result = build_copywriting_translate_missing_target_lang_response()

    assert source_result.status_code == 400
    assert "source_copy_id" in source_result.payload["error"]
    assert target_result.status_code == 400
    assert "target_lang" in target_result.payload["error"]


def test_copywriting_translate_started_and_conflict_responses_are_stable():
    started = build_copywriting_translate_started_response(task_id="task-1")
    conflict = build_copywriting_translate_already_running_response()

    assert started.status_code == 202
    assert started.payload == {"task_id": "task-1"}
    assert conflict.status_code == 409
    assert conflict.payload == {"status": "already_running"}
