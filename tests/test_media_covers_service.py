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
