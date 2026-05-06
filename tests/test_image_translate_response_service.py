from __future__ import annotations


def test_image_translate_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.image_translate import ImageTranslateResponse, image_translate_flask_response

    result = ImageTranslateResponse({"task_id": "img-1"}, 202)

    with authed_client_no_db.application.app_context():
        response, status_code = image_translate_flask_response(result)

    assert status_code == 202
    assert response.get_json() == {"task_id": "img-1"}


def test_image_translate_response_builders_are_stable():
    from web.services.image_translate import (
        build_image_translate_error_response,
        build_image_translate_ok_response,
        build_image_translate_payload_response,
    )

    payload = build_image_translate_payload_response({"items": [{"id": "model-1"}]})
    error = build_image_translate_error_response("files required", 400, code="missing_files")
    ok = build_image_translate_ok_response(status_code=201, task_id="img-2")

    assert payload.status_code == 200
    assert payload.payload == {"items": [{"id": "model-1"}]}
    assert error.status_code == 400
    assert error.payload == {"error": "files required", "code": "missing_files"}
    assert ok.status_code == 201
    assert ok.payload == {"ok": True, "task_id": "img-2"}
