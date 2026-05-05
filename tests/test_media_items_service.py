from __future__ import annotations


def test_build_item_update_response_updates_and_serializes_fresh_row():
    from web.services.media_items import (
        ItemFilenameValidation,
        build_item_update_response,
    )

    item = {"id": 44, "product_id": 123, "lang": "fr", "display_name": "old.mp4"}
    product = {"id": 123, "name": "Demo"}
    calls = {"validate": [], "update": []}

    result = build_item_update_response(
        44,
        item,
        product,
        {"display_name": r"C:\uploads\clean-name.mp4"},
        validate_display_name_fn=lambda filename, product, lang: calls["validate"].append(
            (filename, product["id"], lang)
        )
        or ItemFilenameValidation(ok=True),
        update_item_display_name_fn=lambda item_id, display_name: calls["update"].append(
            (item_id, display_name)
        )
        or 1,
        get_item_fn=lambda item_id: {"id": item_id, "display_name": "clean-name.mp4"},
        serialize_item_fn=lambda row: {"id": row["id"], "display_name": row["display_name"]},
    )

    assert calls == {
        "validate": [("clean-name.mp4", 123, "fr")],
        "update": [(44, "clean-name.mp4")],
    }
    assert result.status_code == 200
    assert result.payload == {"item": {"id": 44, "display_name": "clean-name.mp4"}}


def test_build_item_update_response_rejects_blank_display_name_before_validation():
    from web.services.media_items import build_item_update_response

    calls = []

    result = build_item_update_response(
        44,
        {"id": 44, "lang": "en"},
        {"id": 123},
        {"display_name": "   "},
        validate_display_name_fn=lambda *args: calls.append(("validate", args)),
        update_item_display_name_fn=lambda *args: calls.append(("update", args)),
        get_item_fn=lambda item_id: {"id": item_id},
        serialize_item_fn=lambda row: row,
    )

    assert result.status_code == 400
    assert result.payload == {"error": "display_name required"}
    assert calls == []


def test_build_item_update_response_rejects_too_long_display_name_before_write():
    from web.services.media_items import (
        ItemFilenameValidation,
        build_item_update_response,
    )

    calls = []

    result = build_item_update_response(
        44,
        {"id": 44, "lang": "en"},
        {"id": 123},
        {"display_name": f"{'a' * 256}.mp4"},
        validate_display_name_fn=lambda *args: ItemFilenameValidation(ok=True),
        update_item_display_name_fn=lambda *args: calls.append(args),
        get_item_fn=lambda item_id: {"id": item_id},
        serialize_item_fn=lambda row: row,
    )

    assert result.status_code == 400
    assert result.payload == {"error": "display_name too long"}
    assert calls == []


def test_build_item_update_response_returns_validation_payload_before_write():
    from web.services.media_items import (
        ItemFilenameValidation,
        build_item_update_response,
    )

    calls = []

    result = build_item_update_response(
        44,
        {"id": 44, "lang": "en"},
        {"id": 123},
        {"display_name": "bad name.mp4"},
        validate_display_name_fn=lambda *args: ItemFilenameValidation(
            ok=False,
            payload={"error": "filename_invalid", "details": ["no spaces"]},
            status_code=400,
        ),
        update_item_display_name_fn=lambda *args: calls.append(args),
        get_item_fn=lambda item_id: {"id": item_id},
        serialize_item_fn=lambda row: row,
    )

    assert result.status_code == 400
    assert result.payload == {"error": "filename_invalid", "details": ["no spaces"]}
    assert calls == []


def test_build_item_update_response_serializes_fallback_when_fresh_row_missing():
    from web.services.media_items import (
        ItemFilenameValidation,
        build_item_update_response,
    )

    result = build_item_update_response(
        44,
        {"id": 44, "lang": "en", "display_name": "old.mp4"},
        {"id": 123},
        {"display_name": "new.mp4"},
        validate_display_name_fn=lambda *args: ItemFilenameValidation(ok=True),
        update_item_display_name_fn=lambda *args: 1,
        get_item_fn=lambda item_id: None,
        serialize_item_fn=lambda row: {"id": row["id"], "display_name": row["display_name"]},
    )

    assert result.status_code == 200
    assert result.payload == {"item": {"id": 44, "display_name": "new.mp4"}}


def test_build_item_delete_response_soft_deletes_and_exposes_object_key():
    from web.services.media_items import build_item_delete_response

    calls = []

    result = build_item_delete_response(
        44,
        {"id": 44, "object_key": "1/medias/123/demo.mp4"},
        soft_delete_item_fn=lambda item_id: calls.append(item_id) or 1,
    )

    assert calls == [44]
    assert result.status_code == 200
    assert result.payload == {"ok": True}
    assert result.object_key == "1/medias/123/demo.mp4"
