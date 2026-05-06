from __future__ import annotations

from pathlib import Path


def test_media_item_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.media_items import MediaItemResponse, media_item_flask_response

    result = MediaItemResponse({"ok": True}, 202)

    with authed_client_no_db.application.app_context():
        response, status_code = media_item_flask_response(result)

    assert status_code == 202
    assert response.get_json() == {"ok": True}


def test_build_item_filename_invalid_response_maps_rule_result():
    from web.services.media_items import build_item_filename_invalid_response

    validation = type(
        "Validation",
        (),
        {
            "errors": ["bad prefix", "bad language"],
            "effective_lang": "fr",
            "suggested_filename": "2026.05.06-Demo-video.mp4",
        },
    )()

    result = build_item_filename_invalid_response(validation)

    assert result.status_code == 400
    assert result.payload == {
        "error": "filename_invalid",
        "message": "文件名不符合命名规范",
        "details": ["bad prefix", "bad language"],
        "effective_lang": "fr",
        "suggested_filename": "2026.05.06-Demo-video.mp4",
    }


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


def test_build_item_bootstrap_response_validates_and_reserves_upload():
    from web.services.media_items import (
        ItemUploadValidation,
        build_item_bootstrap_response,
    )

    calls = []

    result = build_item_bootstrap_response(
        7,
        123,
        {"id": 123, "name": "Demo"},
        {"filename": r"C:\tmp\demo.mp4", "lang": "en"},
        parse_lang_fn=lambda body: (body["lang"], None),
        validate_upload_filename_fn=lambda filename, product, lang, **kwargs: calls.append(
            ("validate", filename, product["id"], lang, kwargs)
        )
        or ItemUploadValidation(ok=True, effective_lang="en"),
        build_media_object_key_fn=lambda user_id, pid, filename: calls.append(
            ("key", user_id, pid, filename)
        )
        or f"{user_id}/medias/{pid}/{filename}",
        reserve_local_media_upload_fn=lambda object_key: calls.append(("reserve", object_key))
        or {"upload_url": f"/upload/{object_key}"},
    )

    assert result.status_code == 200
    assert result.payload == {
        "object_key": "7/medias/123/demo.mp4",
        "effective_lang": "en",
        "upload_url": "/upload/7/medias/123/demo.mp4",
        "storage_backend": "local",
    }
    assert calls == [
        ("validate", "demo.mp4", 123, "en", {"initial_upload": False}),
        ("key", 7, 123, "demo.mp4"),
        ("reserve", "7/medias/123/demo.mp4"),
    ]


def test_build_item_bootstrap_response_rejects_unlisted_product_before_parsing():
    from web.services.media_items import build_item_bootstrap_response

    result = build_item_bootstrap_response(
        7,
        123,
        {"id": 123, "listing_status": "下架"},
        {"filename": "demo.mp4", "lang": "en"},
        is_product_listed_fn=lambda product: False,
        parse_lang_fn=lambda body: (_ for _ in ()).throw(AssertionError("parse not reached")),
        validate_upload_filename_fn=lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(AssertionError("validate not reached")),
        build_media_object_key_fn=lambda *args: (_ for _ in ()).throw(AssertionError("key not reached")),
        reserve_local_media_upload_fn=lambda object_key: (
            _ for _ in ()
        ).throw(AssertionError("reserve not reached")),
    )

    assert result.status_code == 409
    assert result.payload == {
        "error": "product_not_listed",
        "message": "产品已下架，不能执行该操作",
    }


def test_build_item_bootstrap_response_rejects_before_reserve():
    from web.services.media_items import (
        ItemUploadValidation,
        build_item_bootstrap_response,
    )

    calls = []

    missing_filename = build_item_bootstrap_response(
        7,
        123,
        {"id": 123},
        {"filename": " ", "lang": "en"},
        parse_lang_fn=lambda body: ("en", None),
        validate_upload_filename_fn=lambda *args, **kwargs: calls.append(("validate", args, kwargs)),
        build_media_object_key_fn=lambda *args: calls.append(("key", args)),
        reserve_local_media_upload_fn=lambda object_key: calls.append(("reserve", object_key)),
    )
    validation_error = build_item_bootstrap_response(
        7,
        123,
        {"id": 123},
        {"filename": "bad.mp4", "lang": "en", "skip_validation": True},
        parse_lang_fn=lambda body: ("en", None),
        validate_upload_filename_fn=lambda filename, product, lang, **kwargs: ItemUploadValidation(
            ok=False,
            effective_lang="fr",
            payload={"error": "filename_invalid", "effective_lang": "fr"},
            status_code=400,
        ),
        build_media_object_key_fn=lambda *args: calls.append(("key", args)),
        reserve_local_media_upload_fn=lambda object_key: calls.append(("reserve", object_key)),
    )

    assert missing_filename.status_code == 400
    assert missing_filename.payload == {"error": "filename required"}
    assert validation_error.status_code == 400
    assert validation_error.payload == {"error": "filename_invalid", "effective_lang": "fr"}
    assert calls == []


def test_build_item_complete_response_creates_item_and_runs_best_effort_side_effects():
    from web.services.media_items import (
        ItemUploadValidation,
        build_item_complete_response,
    )

    calls = []

    result = build_item_complete_response(
        7,
        123,
        {"id": 123, "name": "Demo"},
        {
            "object_key": "1/medias/123/video.mp4",
            "filename": "video.mp4",
            "file_size": 321,
            "cover_object_key": "1/medias/123/cover.jpg",
            "lang": "en",
        },
        parse_lang_fn=lambda body: (body["lang"], None),
        validate_upload_filename_fn=lambda filename, product, lang, **kwargs: calls.append(
            ("validate", filename, product["id"], lang, kwargs)
        )
        or ItemUploadValidation(ok=True, effective_lang="en"),
        is_media_available_fn=lambda object_key: object_key in {
            "1/medias/123/video.mp4",
            "1/medias/123/cover.jpg",
        },
        create_item_fn=lambda pid, user_id, filename, object_key, **kwargs: calls.append(
            ("create", pid, user_id, filename, object_key, kwargs)
        )
        or 44,
        cache_item_cover_fn=lambda item_id, pid, object_key: calls.append(
            ("cover-cache", item_id, pid, object_key)
        ),
        build_item_thumbnail_fn=lambda item_id, pid, filename, object_key: calls.append(
            ("thumbnail", item_id, pid, filename, object_key)
        ),
        schedule_material_evaluation_fn=lambda pid: calls.append(("schedule", pid)),
    )

    assert result.status_code == 201
    assert result.payload == {"id": 44}
    assert calls == [
        ("validate", "video.mp4", 123, "en", {"initial_upload": False}),
        (
            "create",
            123,
            7,
            "video.mp4",
            "1/medias/123/video.mp4",
            {"file_size": 321, "cover_object_key": "1/medias/123/cover.jpg", "lang": "en"},
        ),
        ("cover-cache", 44, 123, "1/medias/123/cover.jpg"),
        ("thumbnail", 44, 123, "video.mp4", "1/medias/123/video.mp4"),
        ("schedule", 123),
    ]


def test_build_item_complete_response_rejects_unlisted_product_before_parsing():
    from web.services.media_items import build_item_complete_response

    result = build_item_complete_response(
        7,
        123,
        {"id": 123, "listing_status": "下架"},
        {"object_key": "1/medias/123/video.mp4", "filename": "video.mp4", "lang": "en"},
        is_product_listed_fn=lambda product: False,
        parse_lang_fn=lambda body: (_ for _ in ()).throw(AssertionError("parse not reached")),
        validate_upload_filename_fn=lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(AssertionError("validate not reached")),
        is_media_available_fn=lambda object_key: (
            _ for _ in ()
        ).throw(AssertionError("exists not reached")),
        create_item_fn=lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(AssertionError("create not reached")),
        cache_item_cover_fn=lambda *args: None,
        build_item_thumbnail_fn=lambda *args: None,
        schedule_material_evaluation_fn=lambda pid: None,
    )

    assert result.status_code == 409
    assert result.payload == {
        "error": "product_not_listed",
        "message": "产品已下架，不能执行该操作",
    }


def test_build_item_complete_response_rejects_before_create():
    from web.services.media_items import (
        ItemUploadValidation,
        build_item_complete_response,
    )

    calls = []

    missing = build_item_complete_response(
        7,
        123,
        {"id": 123},
        {"object_key": "", "filename": "video.mp4", "lang": "en"},
        parse_lang_fn=lambda body: ("en", None),
        validate_upload_filename_fn=lambda *args, **kwargs: calls.append(("validate", args, kwargs)),
        is_media_available_fn=lambda object_key: calls.append(("exists", object_key)) or True,
        create_item_fn=lambda *args, **kwargs: calls.append(("create", args, kwargs)),
        cache_item_cover_fn=lambda *args: calls.append(("cover-cache", args)),
        build_item_thumbnail_fn=lambda *args: calls.append(("thumbnail", args)),
        schedule_material_evaluation_fn=lambda pid: calls.append(("schedule", pid)),
    )
    unavailable = build_item_complete_response(
        7,
        123,
        {"id": 123},
        {"object_key": "missing.mp4", "filename": "video.mp4", "lang": "en"},
        parse_lang_fn=lambda body: ("en", None),
        validate_upload_filename_fn=lambda filename, product, lang, **kwargs: ItemUploadValidation(
            ok=True,
            effective_lang="en",
        ),
        is_media_available_fn=lambda object_key: False,
        create_item_fn=lambda *args, **kwargs: calls.append(("create", args, kwargs)),
        cache_item_cover_fn=lambda *args: calls.append(("cover-cache", args)),
        build_item_thumbnail_fn=lambda *args: calls.append(("thumbnail", args)),
        schedule_material_evaluation_fn=lambda pid: calls.append(("schedule", pid)),
    )

    assert missing.status_code == 400
    assert missing.payload == {"error": "object_key and filename required"}
    assert unavailable.status_code == 400
    assert unavailable.payload == {"error": "object not found"}
    assert calls == []


def test_cache_item_cover_object_downloads_to_product_thumb_dir(tmp_path):
    from web.services.media_items import cache_item_cover_object

    calls = []

    cache_item_cover_object(
        44,
        123,
        "covers/demo.png",
        thumb_dir=tmp_path,
        download_media_object_fn=lambda object_key, destination: calls.append(
            (object_key, destination)
        ),
    )

    assert (tmp_path / "123").is_dir()
    assert calls == [("covers/demo.png", str(tmp_path / "123" / "item_cover_44.png"))]


def test_build_item_thumbnail_updates_metadata_and_removes_tmp_video(tmp_path):
    from web.services.media_items import build_item_thumbnail

    thumb_dir = tmp_path / "media_thumbs"
    calls = []

    def fake_download(object_key, destination):
        calls.append(("download", object_key, destination))
        Path(destination).write_bytes(b"video")

    def fake_extract(video_path, output_dir, *, scale):
        calls.append(("extract", video_path, output_dir, scale))
        thumb = Path(output_dir) / "generated.jpg"
        thumb.write_bytes(b"thumb")
        return str(thumb)

    build_item_thumbnail(
        44,
        123,
        r"C:\upload\demo.mp4",
        "objects/demo.mp4",
        thumb_dir=thumb_dir,
        output_dir=tmp_path,
        download_media_object_fn=fake_download,
        get_media_duration_fn=lambda video_path: calls.append(("duration", video_path)) or 12.5,
        extract_thumbnail_fn=fake_extract,
        execute_fn=lambda sql, args: calls.append(("execute", sql, args)) or 1,
    )

    tmp_video = thumb_dir / "123" / "tmp_44_demo.mp4"
    final_thumb = thumb_dir / "123" / "44.jpg"
    assert not tmp_video.exists()
    assert final_thumb.read_bytes() == b"thumb"
    assert calls == [
        ("download", "objects/demo.mp4", str(tmp_video)),
        ("duration", str(tmp_video)),
        ("extract", str(tmp_video), str(thumb_dir / "123"), "360:-1"),
        (
            "execute",
            "UPDATE media_items SET thumbnail_path=%s, duration_seconds=%s WHERE id=%s",
            ("media_thumbs/123/44.jpg", 12.5, 44),
        ),
    ]
