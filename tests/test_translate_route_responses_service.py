from __future__ import annotations


def test_translate_route_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.translate_route_responses import (
        TranslateRouteResponse,
        translate_route_flask_response,
    )

    result = TranslateRouteResponse({"status": "started"}, 202)

    with authed_client_no_db.application.app_context():
        response, status_code = translate_route_flask_response(result)

    assert status_code == 202
    assert response.get_json() == {"status": "started"}


def test_translate_route_response_builders_are_stable():
    from web.services.translate_route_responses import (
        build_translate_route_error_response,
        build_translate_route_payload_response,
    )

    payload = build_translate_route_payload_response({"task_id": "task-1"}, status_code=201)
    error = build_translate_route_error_response("Task not found", 404, code="missing")

    assert payload.status_code == 201
    assert payload.payload == {"task_id": "task-1"}
    assert error.status_code == 404
    assert error.payload == {"error": "Task not found", "code": "missing"}
