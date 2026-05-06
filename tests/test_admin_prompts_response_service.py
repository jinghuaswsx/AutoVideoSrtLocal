from __future__ import annotations

from web.services.admin_prompts import (
    build_admin_prompts_admin_only_response,
    build_admin_prompts_bad_resolve_response,
    build_admin_prompts_bad_upsert_response,
    build_admin_prompts_list_response,
    build_admin_prompts_resolve_response,
    build_admin_prompts_slot_required_response,
    build_admin_prompts_success_response,
)


def test_admin_prompts_list_response_wraps_items():
    result = build_admin_prompts_list_response(
        [{"slot": "base_translation", "lang": "de"}]
    )

    assert result.status_code == 200
    assert result.payload == {
        "items": [{"slot": "base_translation", "lang": "de"}]
    }


def test_admin_prompts_error_responses_are_stable():
    admin_only = build_admin_prompts_admin_only_response()
    bad_upsert = build_admin_prompts_bad_upsert_response()
    slot_required = build_admin_prompts_slot_required_response()
    bad_resolve = build_admin_prompts_bad_resolve_response(ValueError("bad slot"))

    assert admin_only.status_code == 403
    assert admin_only.payload == {"error": "admin only"}
    assert bad_upsert.status_code == 400
    assert bad_upsert.payload == {"error": "slot/provider/model/content required"}
    assert slot_required.status_code == 400
    assert slot_required.payload == {"error": "slot required"}
    assert bad_resolve.status_code == 400
    assert bad_resolve.payload == {"error": "bad slot"}


def test_admin_prompts_success_and_resolve_responses_are_stable():
    success = build_admin_prompts_success_response()
    resolved = build_admin_prompts_resolve_response(
        {"slot": "base_translation", "content": "PROMPT"}
    )

    assert success.status_code == 200
    assert success.payload == {"ok": True}
    assert resolved.status_code == 200
    assert resolved.payload == {"slot": "base_translation", "content": "PROMPT"}
