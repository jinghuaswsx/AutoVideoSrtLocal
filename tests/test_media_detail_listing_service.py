from __future__ import annotations


def test_build_detail_images_list_response_serializes_rows():
    from web.services.media_detail_listing import build_detail_images_list_response

    calls = []

    result = build_detail_images_list_response(
        123,
        " DE ",
        is_valid_language_fn=lambda lang: lang == "de",
        list_detail_images_fn=lambda pid, lang: calls.append(("list", pid, lang)) or [
            {"id": 1, "object_key": "1/medias/1/a.jpg"},
            {"id": 2, "object_key": "1/medias/1/b.jpg"},
        ],
        serialize_detail_image_fn=lambda row: {"id": row["id"], "object_key": row["object_key"]},
    )

    assert result.status_code == 200
    assert result.payload == {
        "items": [
            {"id": 1, "object_key": "1/medias/1/a.jpg"},
            {"id": 2, "object_key": "1/medias/1/b.jpg"},
        ],
    }
    assert calls == [("list", 123, "de")]


def test_build_detail_images_list_response_rejects_invalid_language_before_listing():
    from web.services.media_detail_listing import build_detail_images_list_response

    result = build_detail_images_list_response(
        123,
        "xx",
        is_valid_language_fn=lambda lang: False,
        list_detail_images_fn=lambda pid, lang: (_ for _ in ()).throw(AssertionError("list not reached")),
        serialize_detail_image_fn=lambda row: row,
    )

    assert result.status_code == 400
    assert result.payload == {"error": "涓嶆敮鎸佺殑璇: xx"}


def test_build_detail_image_proxy_response_returns_accessible_object_key():
    from web.services.media_detail_listing import build_detail_image_proxy_response

    calls = []
    image = {"id": 77, "product_id": 123, "object_key": "1/medias/123/detail.jpg", "deleted_at": None}
    product = {"id": 123, "user_id": 1}

    result = build_detail_image_proxy_response(
        77,
        get_detail_image_fn=lambda image_id: calls.append(("image", image_id)) or image,
        get_product_fn=lambda product_id: calls.append(("product", product_id)) or product,
        can_access_product_fn=lambda value: calls.append(("access", value)) or True,
    )

    assert result.not_found is False
    assert result.object_key == "1/medias/123/detail.jpg"
    assert calls == [("image", 77), ("product", 123), ("access", product)]


def test_build_detail_image_proxy_response_hides_missing_deleted_or_inaccessible_images():
    from web.services.media_detail_listing import build_detail_image_proxy_response

    missing = build_detail_image_proxy_response(
        77,
        get_detail_image_fn=lambda image_id: None,
        get_product_fn=lambda product_id: (_ for _ in ()).throw(AssertionError("product not reached")),
        can_access_product_fn=lambda product: True,
    )
    deleted = build_detail_image_proxy_response(
        78,
        get_detail_image_fn=lambda image_id: {"id": image_id, "product_id": 123, "deleted_at": "now"},
        get_product_fn=lambda product_id: (_ for _ in ()).throw(AssertionError("product not reached")),
        can_access_product_fn=lambda product: True,
    )
    inaccessible = build_detail_image_proxy_response(
        79,
        get_detail_image_fn=lambda image_id: {
            "id": image_id,
            "product_id": 123,
            "object_key": "1/medias/123/detail.jpg",
            "deleted_at": None,
        },
        get_product_fn=lambda product_id: {"id": product_id, "user_id": 999},
        can_access_product_fn=lambda product: False,
    )

    assert missing.not_found is True
    assert missing.object_key is None
    assert deleted.not_found is True
    assert deleted.object_key is None
    assert inaccessible.not_found is True
    assert inaccessible.object_key is None
