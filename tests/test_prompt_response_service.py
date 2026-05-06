from __future__ import annotations

from web.services.prompt import (
    build_prompt_bad_create_response,
    build_prompt_created_response,
    build_prompt_default_delete_blocked_response,
    build_prompt_deleted_response,
    build_prompt_list_response,
    build_prompt_not_found_response,
    build_prompt_response,
)


def test_prompt_list_response_wraps_prompts():
    result = build_prompt_list_response([{"id": 1, "name": "Default"}])

    assert result.status_code == 200
    assert result.payload == {"prompts": [{"id": 1, "name": "Default"}]}


def test_prompt_created_and_single_response_wrap_prompt():
    row = {"id": 2, "name": "Custom"}

    created = build_prompt_created_response(row)
    single = build_prompt_response(row)

    assert created.status_code == 201
    assert created.payload == {"prompt": row}
    assert single.status_code == 200
    assert single.payload == {"prompt": row}


def test_prompt_error_and_delete_responses_are_stable():
    bad_create = build_prompt_bad_create_response()
    not_found = build_prompt_not_found_response()
    default_blocked = build_prompt_default_delete_blocked_response()
    deleted = build_prompt_deleted_response()

    assert bad_create.status_code == 400
    assert bad_create.payload == {"error": "name and prompt_text are required"}
    assert not_found.status_code == 404
    assert not_found.payload == {"error": "Prompt not found"}
    assert default_blocked.status_code == 403
    assert default_blocked.payload == {"error": "系统预设提示词不可删除"}
    assert deleted.status_code == 200
    assert deleted.payload == {"status": "ok"}
