from __future__ import annotations

from flask import Flask


def test_pushes_flask_response_returns_payload_and_status():
    from web.services.pushes_responses import PushesRouteResponse, pushes_flask_response

    app = Flask(__name__)
    result = PushesRouteResponse({"ok": True}, 202)

    with app.app_context():
        response, status_code = pushes_flask_response(result)

    assert status_code == 202
    assert response.get_json() == {"ok": True}


def test_pushes_response_builders_preserve_payloads_and_error_extras():
    from web.services.pushes_responses import (
        build_pushes_error_response,
        build_pushes_payload_response,
    )

    payload = build_pushes_payload_response({"items": []})
    error = build_pushes_error_response("not_ready", 400, missing=["has_cover"])

    assert payload.status_code == 200
    assert payload.payload == {"items": []}
    assert error.status_code == 400
    assert error.payload == {"error": "not_ready", "missing": ["has_cover"]}
