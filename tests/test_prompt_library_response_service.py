from web.services.prompt_library import (
    build_prompt_library_admin_required_response,
    build_prompt_library_content_required_response,
    build_prompt_library_created_response,
    build_prompt_library_generate_failed_response,
    build_prompt_library_generated_response,
    build_prompt_library_item_response,
    build_prompt_library_list_response,
    build_prompt_library_name_required_response,
    build_prompt_library_name_too_long_response,
    build_prompt_library_non_json_response,
    build_prompt_library_ok_response,
    build_prompt_library_requirement_required_response,
    build_prompt_library_requirement_too_long_response,
    build_prompt_library_translation_error_response,
    build_prompt_library_translation_response,
)


def test_prompt_library_item_response_shapes_are_stable():
    assert build_prompt_library_admin_required_response().payload == {"error": "仅管理员可操作"}
    assert build_prompt_library_admin_required_response().status_code == 403
    assert build_prompt_library_list_response(
        items=[{"id": 1}],
        total=1,
        page=2,
        page_size=30,
    ).payload == {"items": [{"id": 1}], "total": 1, "page": 2, "page_size": 30}
    assert build_prompt_library_item_response({"id": 1}).payload == {"id": 1}
    assert build_prompt_library_name_required_response().payload == {"error": "名称必填"}
    assert build_prompt_library_content_required_response().payload == {
        "error": "中文或英文版本至少填一个"
    }
    assert build_prompt_library_name_too_long_response().payload == {
        "error": "名称过长（≤255）"
    }
    created = build_prompt_library_created_response(12)
    assert created.payload == {"id": 12}
    assert created.status_code == 201
    assert build_prompt_library_ok_response().payload == {"ok": True}


def test_prompt_library_generate_response_shapes_are_stable():
    assert build_prompt_library_requirement_required_response().payload == {
        "error": "请描述你的需求"
    }
    assert build_prompt_library_requirement_too_long_response().payload == {
        "error": "需求描述过长（≤2000）"
    }
    failed = build_prompt_library_generate_failed_response("boom")
    assert failed.payload == {"error": "生成失败：boom"}
    assert failed.status_code == 502
    assert build_prompt_library_non_json_response().payload == {
        "error": "模型返回不是合法 JSON，请重试"
    }
    assert build_prompt_library_generated_response(
        name="提示",
        description="描述",
        content="内容",
    ).payload == {"name": "提示", "description": "描述", "content": "内容"}


def test_prompt_library_translation_response_shapes_are_stable():
    bad_request = build_prompt_library_translation_error_response("direction 必须是 zh2en 或 en2zh", 400)
    assert bad_request.payload == {"error": "direction 必须是 zh2en 或 en2zh"}
    assert bad_request.status_code == 400
    upstream = build_prompt_library_translation_error_response("翻译失败：boom", 502)
    assert upstream.payload == {"error": "翻译失败：boom"}
    assert upstream.status_code == 502
    assert build_prompt_library_translation_response("en", "translated").payload == {
        "lang": "en",
        "content": "translated",
    }
