from __future__ import annotations


def test_build_detail_images_bootstrap_response_reserves_local_uploads():
    from web.services.media_detail_uploads import build_detail_images_bootstrap_response

    calls = []

    result = build_detail_images_bootstrap_response(
        123,
        7,
        {
            "lang": "de",
            "files": [
                {"filename": "../detail one.jpg", "content_type": "image/jpeg", "size": 12},
                {"filename": "anim.gif", "content_type": "image/gif", "size": 34},
            ],
        },
        parse_lang_fn=lambda body: (body["lang"], None),
        detail_image_limit_error_fn=lambda pid, lang, files: calls.append(("limit", pid, lang, files)) or None,
        reserve_local_media_upload_fn=lambda object_key: (
            calls.append(("reserve", object_key)) or {"upload_url": f"/upload/{object_key}"}
        ),
        build_media_object_key_fn=lambda user_id, pid, filename: (
            calls.append(("key", user_id, pid, filename)) or f"{user_id}/medias/{pid}/{filename}"
        ),
    )

    assert result.status_code == 200
    assert result.payload == {
        "uploads": [
            {
                "idx": 0,
                "object_key": "7/medias/123/detail_de_00_detail one.jpg",
                "upload_url": "/upload/7/medias/123/detail_de_00_detail one.jpg",
            },
            {
                "idx": 1,
                "object_key": "7/medias/123/detail_de_01_anim.gif",
                "upload_url": "/upload/7/medias/123/detail_de_01_anim.gif",
            },
        ],
        "storage_backend": "local",
    }
    assert calls == [
        (
            "limit",
            123,
            "de",
            [
                {"filename": "detail one.jpg", "content_type": "image/jpeg", "size": 12},
                {"filename": "anim.gif", "content_type": "image/gif", "size": 34},
            ],
        ),
        ("key", 7, 123, "detail_de_00_detail one.jpg"),
        ("reserve", "7/medias/123/detail_de_00_detail one.jpg"),
        ("key", 7, 123, "detail_de_01_anim.gif"),
        ("reserve", "7/medias/123/detail_de_01_anim.gif"),
    ]


def test_build_detail_images_bootstrap_response_rejects_invalid_files_before_reserve():
    from web.services.media_detail_uploads import build_detail_images_bootstrap_response

    result = build_detail_images_bootstrap_response(
        123,
        7,
        {"lang": "de", "files": []},
        parse_lang_fn=lambda body: (body["lang"], None),
        detail_image_limit_error_fn=lambda *args: (_ for _ in ()).throw(AssertionError("limit not reached")),
        reserve_local_media_upload_fn=lambda object_key: (_ for _ in ()).throw(AssertionError("reserve not reached")),
        build_media_object_key_fn=lambda *args: (_ for _ in ()).throw(AssertionError("key not reached")),
    )

    assert result.status_code == 400
    assert result.payload == {"error": "files required"}


def test_build_detail_images_complete_response_persists_serialized_rows():
    from web.services.media_detail_uploads import build_detail_images_complete_response

    calls = []

    result = build_detail_images_complete_response(
        123,
        {
            "lang": "de",
            "images": [
                {
                    "object_key": "7/medias/123/detail.jpg",
                    "content_type": "image/jpeg",
                    "file_size": "42",
                    "width": "640",
                    "height": "480",
                }
            ],
        },
        parse_lang_fn=lambda body: (body["lang"], None),
        is_media_available_fn=lambda object_key: calls.append(("exists", object_key)) or True,
        detail_image_limit_error_fn=lambda pid, lang, images: calls.append(("limit", pid, lang, images)) or None,
        add_detail_image_fn=lambda pid, lang, object_key, **kwargs: (
            calls.append(("add", pid, lang, object_key, kwargs)) or 601
        ),
        get_detail_image_fn=lambda image_id: {
            "id": image_id,
            "product_id": 123,
            "lang": "de",
            "sort_order": 1,
            "object_key": "7/medias/123/detail.jpg",
            "content_type": "image/jpeg",
            "file_size": 42,
            "width": 640,
            "height": 480,
            "origin_type": "manual",
            "source_detail_image_id": None,
            "image_translate_task_id": None,
            "created_at": None,
        },
        serialize_detail_image_fn=lambda row: {"id": row["id"], "object_key": row["object_key"]},
    )

    assert result.status_code == 201
    assert result.payload == {
        "items": [{"id": 601, "object_key": "7/medias/123/detail.jpg"}],
    }
    assert calls == [
        ("exists", "7/medias/123/detail.jpg"),
        (
            "limit",
            123,
            "de",
            [
                {
                    "object_key": "7/medias/123/detail.jpg",
                    "content_type": "image/jpeg",
                    "file_size": "42",
                    "width": "640",
                    "height": "480",
                }
            ],
        ),
        (
            "add",
            123,
            "de",
            "7/medias/123/detail.jpg",
            {
                "content_type": "image/jpeg",
                "file_size": 42,
                "width": 640,
                "height": 480,
                "origin_type": "manual",
            },
        ),
    ]


def test_build_detail_images_complete_response_rejects_missing_object_before_insert():
    from web.services.media_detail_uploads import build_detail_images_complete_response

    result = build_detail_images_complete_response(
        123,
        {"lang": "de", "images": [{"object_key": "missing.jpg"}]},
        parse_lang_fn=lambda body: (body["lang"], None),
        is_media_available_fn=lambda object_key: False,
        detail_image_limit_error_fn=lambda *args: (_ for _ in ()).throw(AssertionError("limit not reached")),
        add_detail_image_fn=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("insert not reached")),
        get_detail_image_fn=lambda image_id: None,
        serialize_detail_image_fn=lambda row: row,
    )

    assert result.status_code == 400
    assert result.payload == {"error": "images[0] object missing: missing.jpg"}
