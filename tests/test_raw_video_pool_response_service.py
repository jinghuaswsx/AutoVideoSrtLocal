from web.services.raw_video_pool import (
    build_raw_video_pool_file_not_found_response,
    build_raw_video_pool_file_too_large_response,
    build_raw_video_pool_internal_error_response,
    build_raw_video_pool_list_response,
    build_raw_video_pool_no_file_response,
    build_raw_video_pool_permission_denied_response,
    build_raw_video_pool_state_error_response,
    build_raw_video_pool_unsupported_type_response,
    build_raw_video_pool_upload_success_response,
)


def test_raw_video_pool_success_response_shapes_are_stable():
    result = {"items": [{"id": 7}], "total": 1}

    assert build_raw_video_pool_list_response(result).payload == result
    upload = build_raw_video_pool_upload_success_response(123)
    assert upload.payload == {"ok": True, "new_size": 123}
    assert upload.status_code == 200


def test_raw_video_pool_error_response_shapes_are_stable():
    assert build_raw_video_pool_permission_denied_response(Exception("no")).payload == {
        "error": "forbidden",
        "detail": "no",
    }
    assert build_raw_video_pool_state_error_response(Exception("bad")).status_code == 422
    assert build_raw_video_pool_file_not_found_response("x.mp4").payload == {
        "error": "file_not_found",
        "detail": "x.mp4",
    }
    assert build_raw_video_pool_no_file_response().payload == {"error": "no_file"}
    assert build_raw_video_pool_file_too_large_response(max_mb=500).payload == {
        "error": "file_too_large",
        "max_mb": 500,
    }
    assert build_raw_video_pool_unsupported_type_response().payload == {"error": "unsupported_type"}
    assert build_raw_video_pool_internal_error_response(Exception("boom")).status_code == 500
