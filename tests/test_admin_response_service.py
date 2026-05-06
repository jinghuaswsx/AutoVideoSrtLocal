from __future__ import annotations


def test_admin_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.admin import AdminResponse, admin_flask_response

    result = AdminResponse({"ok": True, "role": "admin"}, 201)

    with authed_client_no_db.application.app_context():
        response, status_code = admin_flask_response(result)

    assert status_code == 201
    assert response.get_json() == {"ok": True, "role": "admin"}


def test_admin_response_builders_are_stable():
    from web.services.admin import (
        build_admin_error_response,
        build_admin_ok_response,
        build_admin_payload_response,
    )

    payload = build_admin_payload_response({"items": [{"code": "de"}]})
    error = build_admin_error_response("bad language", 400, code="de")
    ok = build_admin_ok_response(status_code=202, sync_id="sync-1")

    assert payload.status_code == 200
    assert payload.payload == {"items": [{"code": "de"}]}
    assert error.status_code == 400
    assert error.payload == {"error": "bad language", "code": "de"}
    assert ok.status_code == 202
    assert ok.payload == {"ok": True, "sync_id": "sync-1"}
