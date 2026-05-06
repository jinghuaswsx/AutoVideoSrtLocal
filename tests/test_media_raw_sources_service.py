from __future__ import annotations


class _Upload:
    def __init__(self, data: bytes, filename: str, mimetype: str):
        self.filename = filename
        self.mimetype = mimetype
        self.stream = _ChunkStream(data)
        self._data = data

    def read(self) -> bytes:
        return self._data


class _ChunkStream:
    def __init__(self, data: bytes):
        self._data = data
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._data):
            return b""
        if size is None or size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk


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


def test_raw_source_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.media_raw_sources import RawSourceResponse, raw_source_flask_response

    with authed_client_no_db.application.app_context():
        payload, status = raw_source_flask_response(
            RawSourceResponse({"items": [{"id": 7}]}, 206)
        )

    assert status == 206
    assert payload.get_json() == {"items": [{"id": 7}]}


def test_build_raw_source_filename_error_response_uses_filename_rules():
    from web.services.media_raw_sources import build_raw_source_filename_error_response

    result = build_raw_source_filename_error_response(
        "bad name.mp4",
        validate_video_filename_no_spaces_fn=lambda filename: ["no spaces"],
    )

    assert result.status_code == 400
    assert result.payload == {
        "error": "raw_source_filename_invalid",
        "message": "文件名不能包含空格",
        "details": ["no spaces"],
        "uploaded_filename": "bad name.mp4",
    }


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


def test_build_raw_source_create_response_writes_objects_and_serializes_row():
    from web.services.media_raw_sources import build_raw_source_create_response

    calls = []
    video = _Upload(b"video-bytes", "source.mp4", "video/mp4")
    cover = _Upload(b"cover-bytes", "cover.png", "image/png")

    result = build_raw_source_create_response(
        123,
        7,
        video,
        cover,
        {"display_name": "manual.mp4"},
        allowed_video_types={"video/mp4"},
        allowed_image_types={"image/png"},
        max_video_bytes=100,
        max_image_bytes=100,
        list_allowed_english_filenames_fn=lambda pid: ["source.mp4"],
        build_raw_source_key_fn=lambda uid, pid, *, kind, filename: f"{uid}/{pid}/{kind}/{filename}",
        write_media_object_fn=lambda key, data: calls.append(("write", key, data)),
        delete_media_object_fn=lambda key: calls.append(("delete", key)),
        inspect_video_fn=lambda data: calls.append(("inspect", data)) or (12.5, 1280, 720),
        create_raw_source_fn=lambda pid, uid, **kwargs: calls.append(("create", pid, uid, kwargs)) or 55,
        get_raw_source_fn=lambda rid: {"id": rid, "display_name": "manual.mp4"},
        serialize_raw_source_fn=lambda row: {"id": row["id"], "display_name": row["display_name"]},
    )

    assert result.status_code == 201
    assert result.payload == {"item": {"id": 55, "display_name": "manual.mp4"}}
    assert calls == [
        ("write", "7/123/video/source.mp4", b"video-bytes"),
        ("write", "7/123/cover/cover.png", b"cover-bytes"),
        ("inspect", b"video-bytes"),
        (
            "create",
            123,
            7,
            {
                "display_name": "manual.mp4",
                "video_object_key": "7/123/video/source.mp4",
                "cover_object_key": "7/123/cover/cover.png",
                "duration_seconds": 12.5,
                "file_size": len(b"video-bytes"),
                "width": 1280,
                "height": 720,
            },
        ),
    ]


def test_build_raw_source_create_response_rejects_before_storage():
    from web.services.media_raw_sources import build_raw_source_create_response

    calls = []
    result = build_raw_source_create_response(
        123,
        7,
        _Upload(b"video-bytes", "source.mp4", "video/mp4"),
        _Upload(b"cover-bytes", "cover.png", "image/png"),
        {},
        allowed_video_types={"video/mp4"},
        allowed_image_types={"image/png"},
        max_video_bytes=100,
        max_image_bytes=100,
        list_allowed_english_filenames_fn=lambda pid: [],
        build_raw_source_key_fn=lambda *args, **kwargs: calls.append(("key", args, kwargs)),
        write_media_object_fn=lambda *args: calls.append(("write", args)),
        delete_media_object_fn=lambda key: calls.append(("delete", key)),
        inspect_video_fn=lambda data: (None, None, None),
        create_raw_source_fn=lambda *args, **kwargs: calls.append(("create", args, kwargs)),
        get_raw_source_fn=lambda rid: {"id": rid},
        serialize_raw_source_fn=lambda row: row,
    )

    assert result.status_code == 400
    assert result.payload == {
        "error": "english_video_required",
        "message": "请先上传至少一条英语视频后，再提交原始视频",
        "uploaded_filename": "source.mp4",
        "english_filenames": [],
    }
    assert calls == []


def test_build_raw_source_create_response_rolls_back_video_when_cover_write_fails():
    from web.services.media_raw_sources import build_raw_source_create_response

    calls = []

    def write(key, data):
        calls.append(("write", key, data))
        if "/cover/" in key:
            raise RuntimeError("cover boom")

    result = build_raw_source_create_response(
        123,
        7,
        _Upload(b"video-bytes", "source.mp4", "video/mp4"),
        _Upload(b"cover-bytes", "cover.png", "image/png"),
        {},
        allowed_video_types={"video/mp4"},
        allowed_image_types={"image/png"},
        max_video_bytes=100,
        max_image_bytes=100,
        list_allowed_english_filenames_fn=lambda pid: ["source.mp4"],
        build_raw_source_key_fn=lambda uid, pid, *, kind, filename: f"{uid}/{pid}/{kind}/{filename}",
        write_media_object_fn=write,
        delete_media_object_fn=lambda key: calls.append(("delete", key)),
        inspect_video_fn=lambda data: (None, None, None),
        create_raw_source_fn=lambda *args, **kwargs: calls.append(("create", args, kwargs)),
        get_raw_source_fn=lambda rid: {"id": rid},
        serialize_raw_source_fn=lambda row: row,
    )

    assert result.status_code == 500
    assert result.payload == {"error": "upload cover failed: cover boom"}
    assert calls == [
        ("write", "7/123/video/source.mp4", b"video-bytes"),
        ("write", "7/123/cover/cover.png", b"cover-bytes"),
        ("delete", "7/123/video/source.mp4"),
    ]


def test_build_raw_source_create_response_rolls_back_objects_when_insert_fails():
    from web.services.media_raw_sources import build_raw_source_create_response

    calls = []

    result = build_raw_source_create_response(
        123,
        7,
        _Upload(b"video-bytes", "source.mp4", "video/mp4"),
        _Upload(b"cover-bytes", "cover.png", "image/png"),
        {},
        allowed_video_types={"video/mp4"},
        allowed_image_types={"image/png"},
        max_video_bytes=100,
        max_image_bytes=100,
        list_allowed_english_filenames_fn=lambda pid: ["source.mp4"],
        build_raw_source_key_fn=lambda uid, pid, *, kind, filename: f"{uid}/{pid}/{kind}/{filename}",
        write_media_object_fn=lambda key, data: calls.append(("write", key, data)),
        delete_media_object_fn=lambda key: calls.append(("delete", key)),
        inspect_video_fn=lambda data: (None, None, None),
        create_raw_source_fn=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db boom")),
        get_raw_source_fn=lambda rid: {"id": rid},
        serialize_raw_source_fn=lambda row: row,
    )

    assert result.status_code == 500
    assert result.payload == {"error": "db insert failed: db boom"}
    assert calls == [
        ("write", "7/123/video/source.mp4", b"video-bytes"),
        ("write", "7/123/cover/cover.png", b"cover-bytes"),
        ("delete", "7/123/video/source.mp4"),
        ("delete", "7/123/cover/cover.png"),
    ]
