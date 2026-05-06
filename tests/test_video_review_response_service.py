from web.services.video_review import (
    build_video_review_already_running_response,
    build_video_review_delete_success_response,
    build_video_review_empty_prompts_response,
    build_video_review_file_missing_response,
    build_video_review_file_too_large_response,
    build_video_review_forbidden_prompts_response,
    build_video_review_missing_upload_response,
    build_video_review_not_found_response,
    build_video_review_prompts_response,
    build_video_review_prompts_saved_response,
    build_video_review_started_response,
    build_video_review_unsupported_upload_response,
    build_video_review_upload_success_response,
)


def test_video_review_upload_and_start_response_shapes_are_stable():
    assert build_video_review_missing_upload_response().payload == {"error": "请上传视频"}
    assert build_video_review_unsupported_upload_response().payload == {
        "error": "不支持的视频格式"
    }
    created = build_video_review_upload_success_response("vr-1")
    assert created.payload == {"id": "vr-1"}
    assert created.status_code == 201
    assert build_video_review_not_found_response().payload == {"error": "not found"}
    assert build_video_review_file_missing_response().payload == {"error": "视频文件不存在"}
    assert build_video_review_file_too_large_response(101.2).payload == {
        "error": "视频文件过大（101.2MB），请压缩到 100MB 以内"
    }
    assert build_video_review_already_running_response().payload == {
        "status": "already_running"
    }
    assert build_video_review_started_response().payload == {"status": "started"}


def test_video_review_prompt_and_delete_response_shapes_are_stable():
    prompts = {"en": "review", "zh": "点评"}
    assert build_video_review_prompts_response(prompts).payload == prompts
    assert build_video_review_forbidden_prompts_response().payload == {
        "error": "仅管理员可修改提示词"
    }
    assert build_video_review_empty_prompts_response().payload == {
        "error": "提示词不能为空"
    }
    saved = build_video_review_prompts_saved_response("en", "zh")
    assert saved.payload == {"status": "ok", "en": "en", "zh": "zh"}
    assert build_video_review_delete_success_response().payload == {"status": "ok"}
