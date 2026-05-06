from __future__ import annotations

from web.services.mk_import import (
    build_mk_import_admin_required_response,
    build_mk_import_bad_payload_response,
    build_mk_import_check_empty_response,
    build_mk_import_check_response,
    build_mk_import_db_failed_response,
    build_mk_import_download_failed_response,
    build_mk_import_duplicate_response,
    build_mk_import_storage_failed_response,
    build_mk_import_success_response,
    build_mk_import_too_many_filenames_response,
)


def test_mk_import_check_responses_are_stable():
    empty = build_mk_import_check_empty_response()
    too_many = build_mk_import_too_many_filenames_response(max_filenames=100)
    check = build_mk_import_check_response(
        filenames=["b.mp4", "a.mp4", "b.mp4"],
        imported={"a.mp4"},
    )

    assert empty.status_code == 200
    assert empty.payload == {"imported": [], "missing": []}
    assert too_many.status_code == 400
    assert too_many.payload == {"error": "too_many_filenames", "max": 100}
    assert check.status_code == 200
    assert check.payload == {"imported": ["a.mp4"], "missing": ["b.mp4"]}


def test_mk_import_video_error_responses_are_stable():
    admin = build_mk_import_admin_required_response()
    bad = build_mk_import_bad_payload_response()
    duplicate = build_mk_import_duplicate_response(RuntimeError("dupe"))
    download = build_mk_import_download_failed_response(RuntimeError("404"))
    storage = build_mk_import_storage_failed_response(RuntimeError("tos"))
    db = build_mk_import_db_failed_response(RuntimeError("sql"))

    assert admin.status_code == 403
    assert admin.payload == {"error": "admin_required"}
    assert bad.status_code == 400
    assert bad.payload == {"error": "bad_payload"}
    assert duplicate.status_code == 422
    assert duplicate.payload == {"error": "duplicate_filename", "detail": "dupe"}
    assert download.status_code == 502
    assert download.payload == {"error": "download_failed", "detail": "404"}
    assert storage.status_code == 500
    assert storage.payload == {"error": "storage_failed", "detail": "tos"}
    assert db.status_code == 500
    assert db.payload == {"error": "db_failed", "detail": "sql"}


def test_mk_import_success_response_returns_service_result():
    result = build_mk_import_success_response({"ok": True, "media_item_id": 12})

    assert result.status_code == 200
    assert result.payload == {"ok": True, "media_item_id": 12}
