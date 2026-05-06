from __future__ import annotations


def test_bulk_translate_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.bulk_translate_responses import (
        BulkTranslateResponse,
        bulk_translate_flask_response,
    )

    result = BulkTranslateResponse({"ok": True}, 202)

    with authed_client_no_db.application.app_context():
        response, status_code = bulk_translate_flask_response(result)

    assert status_code == 202
    assert response.get_json() == {"ok": True}


def test_bulk_translate_response_builders_support_dicts_and_lists():
    from web.services.bulk_translate_responses import (
        build_bulk_translate_error_response,
        build_bulk_translate_payload_response,
    )

    payload = build_bulk_translate_payload_response([{"id": "task-1"}], status_code=200)
    error = build_bulk_translate_error_response("Task not found", 404, code="missing")

    assert payload.status_code == 200
    assert payload.payload == [{"id": "task-1"}]
    assert error.status_code == 404
    assert error.payload == {"error": "Task not found", "code": "missing"}
