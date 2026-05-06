from __future__ import annotations


def test_copywriting_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.copywriting import CopywritingResponse, copywriting_flask_response

    result = CopywritingResponse({"ok": True, "task_id": "cw-1"}, 201)

    with authed_client_no_db.application.app_context():
        response, status_code = copywriting_flask_response(result)

    assert status_code == 201
    assert response.get_json() == {"ok": True, "task_id": "cw-1"}


def test_copywriting_response_builders_are_stable():
    from web.services.copywriting import (
        build_copywriting_error_response,
        build_copywriting_ok_response,
        build_copywriting_payload_response,
    )

    payload = build_copywriting_payload_response({"segment": {"index": 1}})
    error = build_copywriting_error_response("missing task", 404, code="not_found")
    ok = build_copywriting_ok_response(status_code=202, task_id="cw-2")

    assert payload.status_code == 200
    assert payload.payload == {"segment": {"index": 1}}
    assert error.status_code == 404
    assert error.payload == {"error": "missing task", "code": "not_found"}
    assert ok.status_code == 202
    assert ok.payload == {"ok": True, "task_id": "cw-2"}
