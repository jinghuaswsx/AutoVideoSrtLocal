from __future__ import annotations


def test_openapi_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.openapi_responses import OpenAPIResponse, openapi_flask_response

    result = OpenAPIResponse({"items": [{"id": 1}]}, 202)

    with authed_client_no_db.application.app_context():
        response, status_code = openapi_flask_response(result)

    assert status_code == 202
    assert response.get_json() == {"items": [{"id": 1}]}


def test_openapi_response_builders_are_stable():
    from web.services.openapi_responses import (
        build_openapi_error_response,
        build_openapi_payload_response,
    )

    payload = build_openapi_payload_response({"ok": True})
    error = build_openapi_error_response("invalid api key", 401, code="auth")

    assert payload.status_code == 200
    assert payload.payload == {"ok": True}
    assert error.status_code == 401
    assert error.payload == {"error": "invalid api key", "code": "auth"}
