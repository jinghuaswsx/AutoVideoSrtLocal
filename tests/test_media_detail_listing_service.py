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
