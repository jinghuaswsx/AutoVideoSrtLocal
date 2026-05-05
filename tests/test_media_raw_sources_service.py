from __future__ import annotations


def test_build_raw_sources_list_response_serializes_rows():
    from web.services.media_raw_sources import build_raw_sources_list_response

    calls = []

    result = build_raw_sources_list_response(
        123,
        list_raw_sources_fn=lambda pid: calls.append(pid) or [{"id": 7, "display_name": "raw.mp4"}],
        serialize_raw_source_fn=lambda row: {"id": row["id"], "name": row["display_name"]},
    )

    assert calls == [123]
    assert result.status_code == 200
    assert result.payload == {"items": [{"id": 7, "name": "raw.mp4"}]}


def test_build_raw_source_update_response_normalizes_fields_and_serializes_fresh_row():
    from web.services.media_raw_sources import build_raw_source_update_response

    updated = []
    rows = [{"id": 88, "display_name": "clean-name.mp4", "sort_order": 5}]

    result = build_raw_source_update_response(
        88,
        {"display_name": r"C:\uploads\clean-name.mp4", "sort_order": "5"},
        validate_video_filename_no_spaces_fn=lambda filename: [],
        update_raw_source_fn=lambda rid, **fields: updated.append((rid, fields)) or 1,
        get_raw_source_fn=lambda rid: rows.pop(0),
        serialize_raw_source_fn=lambda row: {"id": row["id"], "display_name": row["display_name"]},
    )

    assert updated == [(88, {"display_name": "clean-name.mp4", "sort_order": 5})]
    assert result.status_code == 200
    assert result.payload == {"item": {"id": 88, "display_name": "clean-name.mp4"}}


def test_build_raw_source_update_response_rejects_invalid_display_name_before_write():
    from web.services.media_raw_sources import build_raw_source_update_response

    updated = []

    result = build_raw_source_update_response(
        88,
        {"display_name": "bad name.mp4"},
        validate_video_filename_no_spaces_fn=lambda filename: ["no spaces"],
        update_raw_source_fn=lambda rid, **fields: updated.append((rid, fields)) or 1,
        get_raw_source_fn=lambda rid: {"id": rid},
        serialize_raw_source_fn=lambda row: row,
    )

    assert result.status_code == 400
    assert result.payload == {
        "error": "raw_source_filename_invalid",
        "message": "\u6587\u4ef6\u540d\u4e0d\u80fd\u5305\u542b\u7a7a\u683c",
        "details": ["no spaces"],
        "uploaded_filename": "bad name.mp4",
    }
    assert updated == []


def test_build_raw_source_update_response_rejects_invalid_sort_order():
    from web.services.media_raw_sources import build_raw_source_update_response

    result = build_raw_source_update_response(
        88,
        {"sort_order": "bad"},
        update_raw_source_fn=lambda rid, **fields: None,
        get_raw_source_fn=lambda rid: {"id": rid},
        serialize_raw_source_fn=lambda row: row,
    )

    assert result.status_code == 400
    assert result.payload == {"error": "sort_order must be int"}


def test_build_raw_source_update_response_rejects_empty_payload():
    from web.services.media_raw_sources import build_raw_source_update_response

    result = build_raw_source_update_response(
        88,
        {"ignored": "value"},
        update_raw_source_fn=lambda rid, **fields: None,
        get_raw_source_fn=lambda rid: {"id": rid},
        serialize_raw_source_fn=lambda row: row,
    )

    assert result.status_code == 400
    assert result.payload == {"error": "no valid fields"}


def test_build_raw_source_update_response_reports_missing_fresh_row():
    from web.services.media_raw_sources import build_raw_source_update_response

    result = build_raw_source_update_response(
        88,
        {"display_name": "clean.mp4"},
        validate_video_filename_no_spaces_fn=lambda filename: [],
        update_raw_source_fn=lambda rid, **fields: 1,
        get_raw_source_fn=lambda rid: None,
        serialize_raw_source_fn=lambda row: row,
    )

    assert result.not_found is True
    assert result.status_code == 404
    assert result.payload == {}


def test_build_raw_source_delete_response_soft_deletes_row():
    from web.services.media_raw_sources import build_raw_source_delete_response

    deleted = []

    result = build_raw_source_delete_response(
        55,
        soft_delete_raw_source_fn=lambda rid: deleted.append(rid) or 1,
    )

    assert deleted == [55]
    assert result.status_code == 200
    assert result.payload == {"ok": True}
