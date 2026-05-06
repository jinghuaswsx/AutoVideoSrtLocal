from web.services.link_check import (
    build_link_check_create_success_response,
    build_link_check_delete_success_response,
    build_link_check_missing_link_url_response,
    build_link_check_rename_required_response,
    build_link_check_rename_success_response,
    build_link_check_rename_too_long_response,
    build_link_check_serialized_task_response,
    build_link_check_task_not_found_response,
    build_link_check_target_language_invalid_response,
    build_link_check_unsupported_reference_response,
)


def test_link_check_create_and_task_response_shapes_are_stable():
    created = build_link_check_create_success_response(
        task_id="lc-1",
        detail_url="/link-check/lc-1",
    )
    assert created.status_code == 202
    assert created.payload == {
        "task_id": "lc-1",
        "detail_url": "/link-check/lc-1",
    }

    task = {"id": "lc-1", "status": "done"}
    assert build_link_check_serialized_task_response(task).payload == task


def test_link_check_validation_response_shapes_are_stable():
    assert build_link_check_missing_link_url_response().payload == {"error": "link_url 必填"}
    assert build_link_check_target_language_invalid_response().payload == {
        "error": "target_language 非法"
    }
    assert build_link_check_unsupported_reference_response("bad.exe").payload == {
        "error": "不支持的参考图片格式: bad.exe"
    }
    assert build_link_check_task_not_found_response().payload == {"error": "Task not found"}
    assert build_link_check_rename_required_response().payload == {
        "error": "display_name required"
    }
    assert build_link_check_rename_too_long_response().payload == {
        "error": "名称不能超过50个字符"
    }


def test_link_check_mutation_success_response_shapes_are_stable():
    renamed = build_link_check_rename_success_response("New name")
    assert renamed.payload == {"status": "ok", "display_name": "New name"}
    assert build_link_check_delete_success_response().payload == {"status": "ok"}
