from __future__ import annotations


def test_build_item_play_url_response_uses_media_object_url_builder():
    from web.services.media_covers import build_item_play_url_response

    result = build_item_play_url_response(
        {"id": 44, "object_key": "1/medias/123/en/video.mp4"},
        media_object_url_fn=lambda object_key: f"/medias/object?object_key={object_key}",
    )

    assert result.status_code == 200
    assert result.payload == {"url": "/medias/object?object_key=1/medias/123/en/video.mp4"}


def test_build_product_cover_file_response_uses_cached_safe_file(tmp_path):
    from pathlib import Path

    from web.services.media_covers import build_product_cover_file_response

    thumb_dir = tmp_path / "thumbs"
    cached = thumb_dir / "123" / "cover_en.webp"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"cached-cover")
    downloads = []

    def safe_thumb_cache_path(path):
        resolved = Path(path).resolve()
        resolved.relative_to(thumb_dir.resolve())
        return resolved

    result = build_product_cover_file_response(
        123,
        "de",
        resolve_cover_fn=lambda pid, lang: "1/medias/123/cover.webp",
        get_product_covers_fn=lambda pid: {"en": "1/medias/123/cover.webp"},
        thumb_dir=thumb_dir,
        safe_thumb_cache_path_fn=safe_thumb_cache_path,
        download_media_object_fn=lambda object_key, destination: downloads.append((object_key, destination)),
    )

    assert result.status_code == 200
    assert result.local_path == cached.resolve()
    assert result.mimetype == "image/webp"
    assert downloads == []


def test_build_product_cover_file_response_downloads_missing_cache_to_safe_file(tmp_path):
    from pathlib import Path

    from web.services.media_covers import build_product_cover_file_response

    thumb_dir = tmp_path / "thumbs"
    downloads = []

    def safe_thumb_cache_path(path):
        resolved = Path(path).resolve()
        resolved.relative_to(thumb_dir.resolve())
        return resolved

    def download_media_object(object_key, destination):
        downloads.append((object_key, destination))
        Path(destination).write_bytes(b"downloaded-cover")

    result = build_product_cover_file_response(
        123,
        "en",
        resolve_cover_fn=lambda pid, lang: "1/medias/123/cover.png",
        get_product_covers_fn=lambda pid: {"en": "1/medias/123/cover.png"},
        thumb_dir=thumb_dir,
        safe_thumb_cache_path_fn=safe_thumb_cache_path,
        download_media_object_fn=download_media_object,
    )

    expected = (thumb_dir / "123" / "cover_en.png").resolve()
    assert result.status_code == 200
    assert result.local_path == expected
    assert result.mimetype == "image/png"
    assert expected.read_bytes() == b"downloaded-cover"
    assert downloads == [("1/medias/123/cover.png", str(expected))]


def test_build_product_cover_file_response_rejects_unsafe_language_without_download(tmp_path):
    from pathlib import Path

    from web.services.media_covers import build_product_cover_file_response

    thumb_dir = tmp_path / "thumbs"
    downloads = []

    def safe_thumb_cache_path(path):
        resolved = Path(path).resolve()
        resolved.relative_to(thumb_dir.resolve())
        return resolved

    result = build_product_cover_file_response(
        123,
        "../../outside",
        resolve_cover_fn=lambda pid, lang: "1/medias/123/cover.jpg",
        get_product_covers_fn=lambda pid: {"../../outside": "x"},
        thumb_dir=thumb_dir,
        safe_thumb_cache_path_fn=safe_thumb_cache_path,
        download_media_object_fn=lambda object_key, destination: downloads.append((object_key, destination)),
    )

    assert result.status_code == 404
    assert result.not_found is True
    assert result.local_path is None
    assert downloads == []


def test_build_product_cover_bootstrap_response_uses_lang_and_safe_filename():
    from web.services.media_covers import build_product_cover_bootstrap_response

    reserved = []

    result = build_product_cover_bootstrap_response(
        user_id=7,
        product_id=123,
        body={"lang": "de", "filename": r"..\cover.jpg"},
        parse_lang_fn=lambda body: (body["lang"], None),
        build_media_object_key_fn=lambda user_id, pid, filename: f"{user_id}/medias/{pid}/{filename}",
        reserve_local_media_upload_fn=lambda object_key: reserved.append(object_key)
        or {"upload_url": f"/upload/{object_key}"},
    )

    assert reserved == ["7/medias/123/cover_de_cover.jpg"]
    assert result.status_code == 200
    assert result.payload == {
        "object_key": "7/medias/123/cover_de_cover.jpg",
        "upload_url": "/upload/7/medias/123/cover_de_cover.jpg",
        "storage_backend": "local",
    }


def test_build_product_cover_bootstrap_response_returns_language_error():
    from web.services.media_covers import build_product_cover_bootstrap_response

    calls = []

    result = build_product_cover_bootstrap_response(
        user_id=7,
        product_id=123,
        body={"lang": "xx", "filename": "cover.jpg"},
        parse_lang_fn=lambda body: ("", "unsupported language"),
        build_media_object_key_fn=lambda *args: calls.append(args),
        reserve_local_media_upload_fn=lambda object_key: calls.append(object_key),
    )

    assert calls == []
    assert result.status_code == 400
    assert result.payload == {"error": "unsupported language"}


def test_build_item_cover_bootstrap_response_uses_default_filename():
    from web.services.media_covers import build_item_cover_bootstrap_response

    result = build_item_cover_bootstrap_response(
        user_id=7,
        product_id=123,
        body={},
        build_media_object_key_fn=lambda user_id, pid, filename: f"{user_id}/medias/{pid}/{filename}",
        reserve_local_media_upload_fn=lambda object_key: {"upload_url": f"/upload/{object_key}"},
    )

    assert result.status_code == 200
    assert result.payload == {
        "object_key": "7/medias/123/item_cover_item_cover.jpg",
        "upload_url": "/upload/7/medias/123/item_cover_item_cover.jpg",
        "storage_backend": "local",
    }


def test_build_item_cover_update_response_updates_and_caches_cover():
    from web.services.media_covers import build_item_cover_update_response

    calls = []

    result = build_item_cover_update_response(
        701,
        {"id": 701, "product_id": 123},
        {"object_key": "new/cover.png"},
        is_media_available_fn=lambda object_key: object_key == "new/cover.png",
        update_item_cover_fn=lambda item_id, object_key: calls.append(("update", item_id, object_key)),
        cache_item_cover_fn=lambda item_id, item, object_key: calls.append(("cache", item_id, item["product_id"], object_key)),
    )

    assert result.status_code == 200
    assert result.payload == {
        "ok": True,
        "object_key": "new/cover.png",
        "cover_url": "/medias/item-cover/701",
    }
    assert calls == [
        ("update", 701, "new/cover.png"),
        ("cache", 701, 123, "new/cover.png"),
    ]


def test_build_item_cover_update_response_can_clear_cover_without_cache():
    from web.services.media_covers import build_item_cover_update_response

    calls = []

    result = build_item_cover_update_response(
        701,
        {"id": 701, "product_id": 123},
        {"object_key": ""},
        is_media_available_fn=lambda object_key: calls.append(("exists", object_key)) or True,
        update_item_cover_fn=lambda item_id, object_key: calls.append(("update", item_id, object_key)),
        cache_item_cover_fn=lambda item_id, item, object_key: calls.append(("cache", item_id, object_key)),
    )

    assert result.status_code == 200
    assert result.payload == {"ok": True, "object_key": None, "cover_url": None}
    assert calls == [("update", 701, None)]


def test_build_item_cover_update_response_rejects_missing_and_unavailable_object():
    from web.services.media_covers import build_item_cover_update_response

    calls = []
    item = {"id": 701, "product_id": 123}

    missing = build_item_cover_update_response(
        701,
        item,
        {},
        is_media_available_fn=lambda object_key: calls.append(("exists", object_key)) or True,
        update_item_cover_fn=lambda item_id, object_key: calls.append(("update", item_id, object_key)),
        cache_item_cover_fn=lambda item_id, item, object_key: calls.append(("cache", item_id, object_key)),
    )
    unavailable = build_item_cover_update_response(
        701,
        item,
        {"object_key": "missing.png"},
        is_media_available_fn=lambda object_key: False,
        update_item_cover_fn=lambda item_id, object_key: calls.append(("update", item_id, object_key)),
        cache_item_cover_fn=lambda item_id, item, object_key: calls.append(("cache", item_id, object_key)),
    )

    assert missing.status_code == 400
    assert missing.payload == {"error": "object_key required"}
    assert unavailable.status_code == 400
    assert unavailable.payload == {"error": "object not found"}
    assert calls == []


def test_build_item_cover_set_response_deletes_old_then_updates_and_caches():
    from web.services.media_covers import build_item_cover_set_response

    calls = []

    result = build_item_cover_set_response(
        701,
        {"id": 701, "product_id": 123, "cover_object_key": "old/cover.jpg"},
        {"object_key": "new/cover.png"},
        is_media_available_fn=lambda object_key: object_key == "new/cover.png",
        delete_media_object_fn=lambda object_key: calls.append(("delete", object_key)),
        update_item_cover_fn=lambda item_id, object_key: calls.append(("update", item_id, object_key)),
        cache_item_cover_fn=lambda item_id, item, object_key: calls.append(("cache", item_id, item["product_id"], object_key)),
    )

    assert result.status_code == 200
    assert result.payload == {"ok": True, "cover_url": "/medias/item-cover/701"}
    assert calls == [
        ("delete", "old/cover.jpg"),
        ("update", 701, "new/cover.png"),
        ("cache", 701, 123, "new/cover.png"),
    ]


def test_build_product_cover_complete_response_updates_cache_and_schedules_english():
    from web.services.media_covers import build_product_cover_complete_response

    calls = []

    result = build_product_cover_complete_response(
        123,
        {"lang": "en", "object_key": "new/cover.png"},
        parse_lang_fn=lambda body: (body["lang"], None),
        is_media_available_fn=lambda object_key: object_key == "new/cover.png",
        get_product_covers_fn=lambda pid: {"en": "old/cover.jpg"},
        delete_media_object_fn=lambda object_key: calls.append(("delete", object_key)),
        set_product_cover_fn=lambda pid, lang, object_key: calls.append(("set", pid, lang, object_key)),
        cache_product_cover_fn=lambda pid, lang, object_key: calls.append(("cache", pid, lang, object_key)),
        schedule_material_evaluation_fn=lambda pid, **kwargs: calls.append(("schedule", pid, kwargs)),
    )

    assert result.status_code == 200
    assert result.payload == {"ok": True, "cover_url": "/medias/cover/123?lang=en"}
    assert calls == [
        ("delete", "old/cover.jpg"),
        ("set", 123, "en", "new/cover.png"),
        ("cache", 123, "en", "new/cover.png"),
        ("schedule", 123, {"force": True}),
    ]


def test_build_product_cover_complete_response_rejects_invalid_inputs():
    from web.services.media_covers import build_product_cover_complete_response

    calls = []

    lang_error = build_product_cover_complete_response(
        123,
        {"lang": "xx", "object_key": "new/cover.png"},
        parse_lang_fn=lambda body: ("", "unsupported language"),
        is_media_available_fn=lambda object_key: calls.append(("exists", object_key)) or True,
        get_product_covers_fn=lambda pid: {},
        delete_media_object_fn=lambda object_key: calls.append(("delete", object_key)),
        set_product_cover_fn=lambda pid, lang, object_key: calls.append(("set", pid, lang, object_key)),
        cache_product_cover_fn=lambda pid, lang, object_key: calls.append(("cache", pid, lang, object_key)),
        schedule_material_evaluation_fn=lambda pid, **kwargs: calls.append(("schedule", pid, kwargs)),
    )
    missing_object = build_product_cover_complete_response(
        123,
        {"lang": "en"},
        parse_lang_fn=lambda body: (body["lang"], None),
        is_media_available_fn=lambda object_key: calls.append(("exists", object_key)) or True,
        get_product_covers_fn=lambda pid: {},
        delete_media_object_fn=lambda object_key: calls.append(("delete", object_key)),
        set_product_cover_fn=lambda pid, lang, object_key: calls.append(("set", pid, lang, object_key)),
        cache_product_cover_fn=lambda pid, lang, object_key: calls.append(("cache", pid, lang, object_key)),
        schedule_material_evaluation_fn=lambda pid, **kwargs: calls.append(("schedule", pid, kwargs)),
    )
    missing_file = build_product_cover_complete_response(
        123,
        {"lang": "en", "object_key": "missing.png"},
        parse_lang_fn=lambda body: (body["lang"], None),
        is_media_available_fn=lambda object_key: False,
        get_product_covers_fn=lambda pid: {},
        delete_media_object_fn=lambda object_key: calls.append(("delete", object_key)),
        set_product_cover_fn=lambda pid, lang, object_key: calls.append(("set", pid, lang, object_key)),
        cache_product_cover_fn=lambda pid, lang, object_key: calls.append(("cache", pid, lang, object_key)),
        schedule_material_evaluation_fn=lambda pid, **kwargs: calls.append(("schedule", pid, kwargs)),
    )

    assert lang_error.status_code == 400
    assert lang_error.payload == {"error": "unsupported language"}
    assert missing_object.status_code == 400
    assert missing_object.payload == {"error": "object_key required"}
    assert missing_file.status_code == 400
    assert missing_file.payload == {"error": "object not found"}
    assert calls == []


def test_build_product_cover_delete_response_rejects_english_and_deletes_other_lang():
    from web.services.media_covers import build_product_cover_delete_response

    calls = []
    invalid = build_product_cover_delete_response(
        123,
        "xx",
        is_valid_language_fn=lambda lang: False,
        get_product_covers_fn=lambda pid: {"en": "en/cover.jpg", "de": "de/cover.jpg"},
        delete_media_object_fn=lambda object_key: calls.append(("delete", object_key)),
        delete_product_cover_fn=lambda pid, lang: calls.append(("delete-row", pid, lang)),
    )
    english = build_product_cover_delete_response(
        123,
        "en",
        is_valid_language_fn=lambda lang: True,
        get_product_covers_fn=lambda pid: {"en": "en/cover.jpg", "de": "de/cover.jpg"},
        delete_media_object_fn=lambda object_key: calls.append(("delete", object_key)),
        delete_product_cover_fn=lambda pid, lang: calls.append(("delete-row", pid, lang)),
    )
    german = build_product_cover_delete_response(
        123,
        "de",
        is_valid_language_fn=lambda lang: True,
        get_product_covers_fn=lambda pid: {"en": "en/cover.jpg", "de": "de/cover.jpg"},
        delete_media_object_fn=lambda object_key: calls.append(("delete", object_key)),
        delete_product_cover_fn=lambda pid, lang: calls.append(("delete-row", pid, lang)),
    )

    assert invalid.status_code == 400
    assert invalid.payload == {"error": "unsupported language: xx"}
    assert english.status_code == 400
    assert english.payload == {"error": "默认语种 en 不能删除"}
    assert german.status_code == 200
    assert german.payload == {"ok": True}
    assert calls == [("delete", "de/cover.jpg"), ("delete-row", 123, "de")]


def test_build_product_cover_from_url_response_updates_cache_and_schedules_english():
    from web.services.media_covers import build_product_cover_from_url_response

    calls = []

    result = build_product_cover_from_url_response(
        123,
        7,
        {"lang": "en", "url": "https://example.test/cover.png"},
        parse_lang_fn=lambda body: (body["lang"], None),
        download_image_to_local_media_fn=lambda url, pid, prefix, *, user_id=None: (
            calls.append(("download", url, pid, prefix, user_id))
            or ("new/cover.png", b"image-bytes", ".png")
        ),
        get_product_covers_fn=lambda pid: {"en": "old/cover.jpg"},
        delete_media_object_fn=lambda object_key: calls.append(("delete", object_key)),
        set_product_cover_fn=lambda pid, lang, object_key: calls.append(("set", pid, lang, object_key)),
        cache_product_cover_bytes_fn=lambda pid, lang, ext, data: calls.append(("cache", pid, lang, ext, data)),
        schedule_material_evaluation_fn=lambda pid, **kwargs: calls.append(("schedule", pid, kwargs)),
    )

    assert result.status_code == 200
    assert result.payload == {
        "ok": True,
        "cover_url": "/medias/cover/123?lang=en",
        "object_key": "new/cover.png",
    }
    assert calls == [
        ("download", "https://example.test/cover.png", 123, "cover_en", 7),
        ("delete", "old/cover.jpg"),
        ("set", 123, "en", "new/cover.png"),
        ("cache", 123, "en", ".png", b"image-bytes"),
        ("schedule", 123, {"force": True}),
    ]


def test_build_product_cover_from_url_response_rejects_parse_and_download_errors():
    from web.services.media_covers import build_product_cover_from_url_response

    calls = []
    lang_error = build_product_cover_from_url_response(
        123,
        7,
        {"lang": "xx", "url": "https://example.test/cover.png"},
        parse_lang_fn=lambda body: ("", "unsupported language"),
        download_image_to_local_media_fn=lambda *args, **kwargs: calls.append(("download", args, kwargs)),
        get_product_covers_fn=lambda pid: {},
        delete_media_object_fn=lambda object_key: calls.append(("delete", object_key)),
        set_product_cover_fn=lambda pid, lang, object_key: calls.append(("set", pid, lang, object_key)),
        cache_product_cover_bytes_fn=lambda pid, lang, ext, data: calls.append(("cache", pid, lang, ext, data)),
        schedule_material_evaluation_fn=lambda pid, **kwargs: calls.append(("schedule", pid, kwargs)),
    )
    download_error = build_product_cover_from_url_response(
        123,
        7,
        {"lang": "de", "url": "https://example.test/cover.png"},
        parse_lang_fn=lambda body: (body["lang"], None),
        download_image_to_local_media_fn=lambda *args, **kwargs: (None, None, "download failed"),
        get_product_covers_fn=lambda pid: {},
        delete_media_object_fn=lambda object_key: calls.append(("delete", object_key)),
        set_product_cover_fn=lambda pid, lang, object_key: calls.append(("set", pid, lang, object_key)),
        cache_product_cover_bytes_fn=lambda pid, lang, ext, data: calls.append(("cache", pid, lang, ext, data)),
        schedule_material_evaluation_fn=lambda pid, **kwargs: calls.append(("schedule", pid, kwargs)),
    )

    assert lang_error.status_code == 400
    assert lang_error.payload == {"error": "unsupported language"}
    assert download_error.status_code == 400
    assert download_error.payload == {"error": "download failed"}
    assert calls == []


def test_build_item_cover_from_url_response_downloads_without_item_update():
    from web.services.media_covers import build_item_cover_from_url_response

    calls = []

    result = build_item_cover_from_url_response(
        123,
        7,
        {"url": "https://example.test/item.png"},
        download_image_to_local_media_fn=lambda url, pid, prefix, *, user_id=None: (
            calls.append(("download", url, pid, prefix, user_id))
            or ("new/item.png", b"image-bytes", ".png")
        ),
    )

    assert result.status_code == 200
    assert result.payload == {"ok": True, "object_key": "new/item.png"}
    assert calls == [("download", "https://example.test/item.png", 123, "item_cover", 7)]


def test_build_item_cover_set_from_url_response_updates_and_caches():
    from web.services.media_covers import build_item_cover_set_from_url_response

    calls = []

    result = build_item_cover_set_from_url_response(
        701,
        7,
        {"id": 701, "product_id": 123, "cover_object_key": "old/item.jpg"},
        {"url": "https://example.test/item.png"},
        download_image_to_local_media_fn=lambda url, pid, prefix, *, user_id=None: (
            calls.append(("download", url, pid, prefix, user_id))
            or ("new/item.png", b"image-bytes", ".png")
        ),
        delete_media_object_fn=lambda object_key: calls.append(("delete", object_key)),
        update_item_cover_fn=lambda item_id, object_key: calls.append(("update", item_id, object_key)),
        cache_item_cover_bytes_fn=lambda item_id, item, ext, data: calls.append(("cache", item_id, item["product_id"], ext, data)),
    )

    assert result.status_code == 200
    assert result.payload == {
        "ok": True,
        "cover_url": "/medias/item-cover/701",
        "object_key": "new/item.png",
    }
    assert calls == [
        ("download", "https://example.test/item.png", 123, "item_cover", 7),
        ("delete", "old/item.jpg"),
        ("update", 701, "new/item.png"),
        ("cache", 701, 123, ".png", b"image-bytes"),
    ]
