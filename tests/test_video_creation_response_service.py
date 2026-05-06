from __future__ import annotations


def test_video_creation_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.video_creation import (
        VideoCreationResponse,
        video_creation_flask_response,
    )

    result = VideoCreationResponse({"id": "vc-1"}, 201)

    with authed_client_no_db.application.app_context():
        response, status_code = video_creation_flask_response(result)

    assert status_code == 201
    assert response.get_json() == {"id": "vc-1"}


def test_video_creation_response_builders_are_stable():
    from web.services.video_creation import (
        build_video_creation_error_response,
        build_video_creation_ok_status_response,
        build_video_creation_payload_response,
    )

    payload = build_video_creation_payload_response(
        {"status": "already_running"},
        status_code=409,
    )
    error = build_video_creation_error_response("not found", 404, code="missing")
    ok = build_video_creation_ok_status_response()

    assert payload.status_code == 409
    assert payload.payload == {"status": "already_running"}
    assert error.status_code == 404
    assert error.payload == {"error": "not found", "code": "missing"}
    assert ok.status_code == 200
    assert ok.payload == {"status": "ok"}
