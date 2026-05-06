from __future__ import annotations

from types import SimpleNamespace


def test_detail_image_json_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.media_detail_responses import detail_image_json_flask_response

    outcome = SimpleNamespace(payload={"ok": True}, status_code=202)

    with authed_client_no_db.application.app_context():
        response, status_code = detail_image_json_flask_response(outcome)

    assert status_code == 202
    assert response.get_json() == {"ok": True}


def test_detail_image_json_flask_response_maps_error_payload(authed_client_no_db):
    from web.services.media_detail_responses import detail_image_json_flask_response

    outcome = SimpleNamespace(error="unsupported language", status_code=400)

    with authed_client_no_db.application.app_context():
        response, status_code = detail_image_json_flask_response(outcome)

    assert status_code == 400
    assert response.get_json() == {"error": "unsupported language"}
