from __future__ import annotations

from flask import Flask


def test_subtitle_removal_flask_response_returns_payload_and_status():
    from web.services.subtitle_removal_responses import (
        SubtitleRemovalRouteResponse,
        subtitle_removal_flask_response,
    )

    app = Flask(__name__)
    result = SubtitleRemovalRouteResponse({"task_id": "sr-1"}, 202)

    with app.app_context():
        response, status_code = subtitle_removal_flask_response(result)

    assert status_code == 202
    assert response.get_json() == {"task_id": "sr-1"}


def test_subtitle_removal_response_builders_preserve_payloads_and_error_extras():
    from web.services.subtitle_removal_responses import (
        build_subtitle_removal_error_response,
        build_subtitle_removal_payload_response,
    )

    payload = build_subtitle_removal_payload_response({"items": []})
    error = build_subtitle_removal_error_response("task is already running", 409, task_id="sr-1")

    assert payload.status_code == 200
    assert payload.payload == {"items": []}
    assert error.status_code == 409
    assert error.payload == {"error": "task is already running", "task_id": "sr-1"}
