from __future__ import annotations


def test_tos_upload_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.tos_upload import TosUploadResponse, tos_upload_flask_response

    result = TosUploadResponse({"error": "disabled"}, 410)

    with authed_client_no_db.application.app_context():
        response, status_code = tos_upload_flask_response(result)

    assert status_code == 410
    assert response.get_json() == {"error": "disabled"}


def test_build_tos_upload_disabled_responses():
    from web.services.tos_upload import (
        build_tos_upload_bootstrap_disabled_response,
        build_tos_upload_complete_disabled_response,
    )

    bootstrap = build_tos_upload_bootstrap_disabled_response()
    complete = build_tos_upload_complete_disabled_response()

    assert bootstrap.status_code == 410
    assert "本地上传" in bootstrap.payload["error"]
    assert "通用 TOS 直传入口" in bootstrap.payload["error"]
    assert complete.status_code == 410
    assert "本地上传" in complete.payload["error"]
    assert "TOS complete" in complete.payload["error"]
