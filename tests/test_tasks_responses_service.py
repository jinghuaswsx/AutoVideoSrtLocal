from __future__ import annotations


def test_tasks_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.tasks_responses import (
        TasksRouteResponse,
        tasks_flask_response,
    )

    result = TasksRouteResponse({"ok": True}, 202)

    with authed_client_no_db.application.app_context():
        response, status_code = tasks_flask_response(result)

    assert status_code == 202
    assert response.get_json() == {"ok": True}


def test_tasks_response_builders_preserve_payloads_and_error_extras():
    from web.services.tasks_responses import (
        build_tasks_error_response,
        build_tasks_payload_response,
    )

    payload = build_tasks_payload_response({"items": []})
    error = build_tasks_error_response("readiness_failed", 422, missing=["video"])

    assert payload.status_code == 200
    assert payload.payload == {"items": []}
    assert error.status_code == 422
    assert error.payload == {"error": "readiness_failed", "missing": ["video"]}
