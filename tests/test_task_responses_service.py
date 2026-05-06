from __future__ import annotations

from flask import Flask


def test_task_not_found_response_matches_existing_json_shape():
    from web.services.task_responses import task_not_found_response

    app = Flask(__name__)

    with app.app_context():
        response, status = task_not_found_response()

    assert status == 404
    assert response.get_json() == {"error": "Task not found"}


def test_task_flask_response_returns_payload_and_status():
    from web.services.task_responses import TaskRouteResponse, task_flask_response

    app = Flask(__name__)
    result = TaskRouteResponse({"ok": True}, 202)

    with app.app_context():
        response, status = task_flask_response(result)

    assert status == 202
    assert response.get_json() == {"ok": True}


def test_task_response_builders_preserve_payloads_and_error_extras():
    from web.services.task_responses import (
        build_task_error_response,
        build_task_payload_response,
    )

    payload = build_task_payload_response({"status": "ok"})
    error = build_task_error_response("missing_voice", 400, lang="de")

    assert payload.status_code == 200
    assert payload.payload == {"status": "ok"}
    assert error.status_code == 400
    assert error.payload == {"error": "missing_voice", "lang": "de"}
