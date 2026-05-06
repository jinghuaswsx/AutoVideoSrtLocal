from __future__ import annotations

from web.services.text_translate import (
    build_text_translate_created_response,
    build_text_translate_delete_success_response,
    build_text_translate_empty_segments_response,
    build_text_translate_exception_response,
    build_text_translate_missing_source_response,
    build_text_translate_not_found_response,
    build_text_translate_success_response,
)


def test_text_translate_created_response_wraps_task_id():
    result = build_text_translate_created_response(task_id="task-1")

    assert result.status_code == 201
    assert result.payload == {"id": "task-1"}


def test_text_translate_error_responses_are_stable():
    not_found = build_text_translate_not_found_response()
    missing_source = build_text_translate_missing_source_response()
    empty_segments = build_text_translate_empty_segments_response()
    exception = build_text_translate_exception_response(RuntimeError("boom"))

    assert not_found.status_code == 404
    assert not_found.payload == {"error": "not found"}
    assert missing_source.status_code == 400
    assert missing_source.payload == {"error": "source_text or segments required"}
    assert empty_segments.status_code == 400
    assert empty_segments.payload == {"error": "no valid segments"}
    assert exception.status_code == 500
    assert exception.payload == {"error": "boom"}


def test_text_translate_success_and_delete_responses_are_stable():
    success = build_text_translate_success_response(
        result={"full_text": "Hello world"},
        model="doubao-1-5-pro-32k",
    )
    deleted = build_text_translate_delete_success_response()

    assert success.status_code == 200
    assert success.payload == {
        "result": {"full_text": "Hello world"},
        "model": "doubao-1-5-pro-32k",
    }
    assert deleted.status_code == 200
    assert deleted.payload == {"status": "ok"}
