from __future__ import annotations


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
