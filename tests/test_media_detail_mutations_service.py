from __future__ import annotations

from web.services.media_detail_mutations import (
    build_clear_detail_images_response,
    build_delete_detail_image_response,
    build_reorder_detail_images_response,
    clear_detail_images,
    delete_detail_image,
    reorder_detail_images,
)


def test_delete_detail_image_rejects_missing_or_foreign_row_without_side_effects():
    calls = []
    outcome = delete_detail_image(
        9,
        product_id=123,
        get_detail_image=lambda image_id: {"id": image_id, "product_id": 456, "object_key": "media/a.jpg"},
        soft_delete_detail_image=lambda image_id: calls.append(("soft", image_id)),
        delete_media_object=lambda object_key: calls.append(("object", object_key)),
    )

    assert calls == []
    assert outcome.not_found is True
    assert outcome.payload is None


def test_delete_detail_image_soft_deletes_and_best_effort_deletes_object():
    calls = []

    def fail_delete_object(object_key):
        calls.append(("object", object_key))
        raise RuntimeError("storage unavailable")

    outcome = delete_detail_image(
        9,
        product_id=123,
        get_detail_image=lambda image_id: {"id": image_id, "product_id": 123, "object_key": "media/a.jpg"},
        soft_delete_detail_image=lambda image_id: calls.append(("soft", image_id)),
        delete_media_object=fail_delete_object,
    )

    assert calls == [("soft", 9), ("object", "media/a.jpg")]
    assert outcome.not_found is False
    assert outcome.payload == {"ok": True}


def test_build_delete_detail_image_response_delegates_delete():
    calls = []
    outcome = build_delete_detail_image_response(
        123,
        9,
        get_detail_image_fn=lambda image_id: {"id": image_id, "product_id": 123, "object_key": "media/a.jpg"},
        soft_delete_detail_image_fn=lambda image_id: calls.append(("soft", image_id)),
        delete_media_object_fn=lambda object_key: calls.append(("object", object_key)),
    )

    assert calls == [("soft", 9), ("object", "media/a.jpg")]
    assert outcome.not_found is False
    assert outcome.payload == {"ok": True}


def test_clear_detail_images_rejects_english_without_side_effects():
    calls = []
    outcome = clear_detail_images(
        123,
        "en",
        list_detail_images=lambda product_id, lang: calls.append(("list", product_id, lang)) or [],
        soft_delete_detail_images_by_lang=lambda product_id, lang: calls.append(("clear", product_id, lang)),
        delete_media_object=lambda object_key: calls.append(("object", object_key)),
    )

    assert calls == []
    assert outcome.status_code == 400
    assert outcome.error == "english detail images cannot be cleared via this endpoint"


def test_clear_detail_images_soft_deletes_lang_and_best_effort_deletes_objects():
    calls = []
    outcome = clear_detail_images(
        123,
        "de",
        list_detail_images=lambda product_id, lang: [
            {"object_key": "media/a.jpg"},
            {"object_key": "media/b.jpg"},
        ],
        soft_delete_detail_images_by_lang=lambda product_id, lang: calls.append(("clear", product_id, lang)) or 2,
        delete_media_object=lambda object_key: calls.append(("object", object_key)),
    )

    assert calls == [("clear", 123, "de"), ("object", "media/a.jpg"), ("object", "media/b.jpg")]
    assert outcome.payload == {"ok": True, "cleared": 2}


def test_reorder_detail_images_validates_ids_before_calling_dao():
    calls = []
    outcome = reorder_detail_images(
        123,
        "de",
        ["1", "bad"],
        reorder_detail_images=lambda product_id, lang, ids: calls.append((product_id, lang, ids)),
    )

    assert calls == []
    assert outcome.status_code == 400
    assert outcome.error == "ids must be integers"


def test_reorder_detail_images_returns_updated_count():
    calls = []
    outcome = reorder_detail_images(
        123,
        "de",
        ["1", 2],
        reorder_detail_images=lambda product_id, lang, ids: calls.append((product_id, lang, ids)) or 2,
    )

    assert calls == [(123, "de", [1, 2])]
    assert outcome.payload == {"ok": True, "updated": 2}


def test_build_clear_detail_images_response_parses_lang_and_delegates_clear():
    calls = []
    outcome = build_clear_detail_images_response(
        123,
        {"lang": " DE "},
        parse_lang_fn=lambda body, default="": (body["lang"].strip().lower(), None),
        list_detail_images_fn=lambda product_id, lang: calls.append(("list", product_id, lang)) or [
            {"object_key": "media/a.jpg"},
        ],
        soft_delete_detail_images_by_lang_fn=lambda product_id, lang: calls.append(("clear", product_id, lang)) or 1,
        delete_media_object_fn=lambda object_key: calls.append(("object", object_key)),
    )

    assert outcome.error is None
    assert outcome.payload == {"ok": True, "cleared": 1}
    assert calls == [
        ("list", 123, "de"),
        ("clear", 123, "de"),
        ("object", "media/a.jpg"),
    ]


def test_build_clear_detail_images_response_returns_parse_error_before_side_effects():
    calls = []
    outcome = build_clear_detail_images_response(
        123,
        {},
        parse_lang_fn=lambda body, default="": (None, "lang required"),
        list_detail_images_fn=lambda product_id, lang: calls.append(("list", product_id, lang)),
        soft_delete_detail_images_by_lang_fn=lambda product_id, lang: calls.append(("clear", product_id, lang)),
        delete_media_object_fn=lambda object_key: calls.append(("object", object_key)),
    )

    assert outcome.status_code == 400
    assert outcome.error == "lang required"
    assert calls == []


def test_build_reorder_detail_images_response_parses_lang_and_delegates_reorder():
    calls = []
    outcome = build_reorder_detail_images_response(
        123,
        {"lang": " DE ", "ids": ["1", 2]},
        parse_lang_fn=lambda body: (body["lang"].strip().lower(), None),
        reorder_detail_images_fn=lambda product_id, lang, ids: calls.append((product_id, lang, ids)) or 2,
    )

    assert outcome.error is None
    assert outcome.payload == {"ok": True, "updated": 2}
    assert calls == [(123, "de", [1, 2])]
