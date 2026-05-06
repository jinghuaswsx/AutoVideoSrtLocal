from web.services.voice_library import (
    build_voice_library_filters_response,
    build_voice_library_forbidden_upload_token_response,
    build_voice_library_invalid_gender_response,
    build_voice_library_language_not_enabled_response,
    build_voice_library_language_required_response,
    build_voice_library_list_response,
    build_voice_library_match_started_response,
    build_voice_library_match_status_response,
    build_voice_library_not_found_response,
    build_voice_library_service_error_response,
    build_voice_library_upload_token_not_found_response,
    build_voice_library_upload_url_response,
    build_voice_library_uploaded_video_missing_response,
    build_voice_library_unsupported_content_type_response,
)


def test_voice_library_browse_response_shapes_are_stable():
    filters = {
        "languages": [{"code": "de", "name_zh": "德语"}],
        "genders": ["male", "female"],
        "use_cases": [],
    }
    assert build_voice_library_filters_response(filters).payload == filters
    assert build_voice_library_language_required_response().payload == {
        "error": "language is required"
    }
    assert build_voice_library_service_error_response("boom").payload == {"error": "boom"}
    assert build_voice_library_service_error_response("boom").status_code == 400
    result = {"items": [], "total": 0}
    assert build_voice_library_list_response(result).payload == result


def test_voice_library_match_response_shapes_are_stable():
    upload = build_voice_library_upload_url_response(
        upload_url="/voice-library/api/match/upload/token-1",
        upload_token="token-1",
        filename="demo.mp4",
    )
    assert upload.payload == {
        "upload_url": "/voice-library/api/match/upload/token-1",
        "upload_token": "token-1",
        "filename": "demo.mp4",
        "expires_in": 600,
    }
    assert build_voice_library_unsupported_content_type_response().payload == {
        "error": "unsupported content_type"
    }
    assert build_voice_library_upload_token_not_found_response().payload == {
        "error": "upload token not found"
    }
    assert build_voice_library_upload_token_not_found_response().status_code == 404
    assert build_voice_library_forbidden_upload_token_response().payload == {
        "error": "forbidden upload token"
    }
    assert build_voice_library_language_not_enabled_response().payload == {
        "error": "language not enabled"
    }
    assert build_voice_library_invalid_gender_response().payload == {
        "error": "gender must be male or female"
    }
    assert build_voice_library_uploaded_video_missing_response().payload == {
        "error": "uploaded video file missing"
    }
    started = build_voice_library_match_started_response("vm_1")
    assert started.payload == {"task_id": "vm_1"}
    assert started.status_code == 202
    assert build_voice_library_not_found_response().payload == {"error": "not found"}
    assert build_voice_library_not_found_response().status_code == 404
    status_payload = {"status": "done", "result": {"candidates": []}}
    assert build_voice_library_match_status_response(status_payload).payload == status_payload
