from __future__ import annotations

from types import SimpleNamespace

from web.services.media_detail_from_url import build_detail_images_from_url_plan


def test_from_url_plan_prefers_explicit_url_and_normalizes_lang():
    outcome = build_detail_images_from_url_plan(
        {"product_code": "handle"},
        {"lang": " DE ", "url": " https://example.com/product ", "clear_existing": 1},
        is_valid_language=lambda code: code in {"en", "de"},
    )

    assert outcome.error is None
    assert outcome.plan is not None
    assert outcome.plan.lang == "de"
    assert outcome.plan.url == "https://example.com/product"
    assert outcome.plan.clear_existing is True


def test_from_url_plan_uses_localized_link_dict():
    outcome = build_detail_images_from_url_plan(
        {"localized_links_json": {"de": " https://example.com/de-product "}, "product_code": "handle"},
        {"lang": "de"},
        is_valid_language=lambda code: code in {"en", "de"},
    )

    assert outcome.plan is not None
    assert outcome.plan.url == "https://example.com/de-product"


def test_from_url_plan_uses_localized_link_json_string():
    outcome = build_detail_images_from_url_plan(
        {"localized_links_json": '{"de": "https://example.com/de-product"}', "product_code": "handle"},
        {"lang": "de"},
        is_valid_language=lambda code: code in {"en", "de"},
    )

    assert outcome.plan is not None
    assert outcome.plan.url == "https://example.com/de-product"


def test_from_url_plan_falls_back_to_default_storefront_link():
    outcome = build_detail_images_from_url_plan(
        {"localized_links_json": "{broken", "product_code": "led-bubble-blaster"},
        {"lang": "de"},
        is_valid_language=lambda code: code in {"en", "de"},
    )

    assert outcome.plan is not None
    assert outcome.plan.url == "https://newjoyloo.com/de/products/led-bubble-blaster"


def test_from_url_plan_rejects_missing_product_code_when_default_link_needed():
    outcome = build_detail_images_from_url_plan(
        {"localized_links_json": {}},
        {"lang": "en"},
        is_valid_language=lambda code: code in {"en", "de"},
    )

    assert outcome.plan is None
    assert outcome.status_code == 400
    assert outcome.error == "product_code required before inferring a default link"


def test_from_url_plan_rejects_unsupported_language():
    outcome = build_detail_images_from_url_plan(
        {"product_code": "handle"},
        {"lang": "xx"},
        is_valid_language=lambda code: code in {"en", "de"},
    )

    assert outcome.plan is None
    assert outcome.status_code == 400
    assert outcome.error == "unsupported language: xx"


def test_from_url_response_creates_task_worker_and_persists_downloaded_images():
    from web.services.media_detail_from_url import build_detail_images_from_url_response

    calls = []
    worker_state = {}

    def create_fetch_task_fn(*, user_id, product_id, url, lang, worker):
        calls.append(("create", user_id, product_id, url, lang))

        def update(**patch):
            worker_state.update(patch)

        worker("mdf-test", update)
        return "mdf-test"

    def fetch_page_fn(url, lang):
        calls.append(("fetch", url, lang))
        return SimpleNamespace(images=[{"source_url": "https://cdn.example.test/detail-1.jpg"}])

    def add_detail_image_fn(product_id, lang, object_key, **kwargs):
        calls.append(("add", product_id, lang, object_key, kwargs))
        return 501

    result = build_detail_images_from_url_response(
        123,
        7,
        {"product_code": "handle"},
        {"lang": "en", "url": "https://store.example.test/products/handle"},
        is_valid_language_fn=lambda code: code == "en",
        create_fetch_task_fn=create_fetch_task_fn,
        fetch_page_fn=fetch_page_fn,
        download_image_to_local_media_fn=lambda url, pid, prefix, *, user_id=None: (
            calls.append(("download", url, pid, prefix, user_id))
            or ("7/medias/123/detail-1.jpg", b"image-bytes", ".jpg")
        ),
        soft_delete_detail_images_by_lang_fn=lambda product_id, lang: calls.append(("clear", product_id, lang)),
        detail_image_empty_counts_fn=lambda: calls.append(("empty-counts",)) or {"image": 0, "gif": 0},
        detail_image_existing_counts_fn=lambda product_id, lang: (
            calls.append(("existing-counts", product_id, lang)) or {"image": 0, "gif": 0}
        ),
        detail_image_kind_from_download_ext_fn=lambda ext: "gif" if ext == ".gif" else "image",
        detail_image_limits={"image": 2, "gif": 1},
        detail_image_kind_labels={"image": "image", "gif": "GIF"},
        add_detail_image_fn=add_detail_image_fn,
        get_detail_image_fn=lambda image_id: {
            "id": image_id,
            "product_id": 123,
            "lang": "en",
            "sort_order": 1,
            "object_key": "7/medias/123/detail-1.jpg",
            "content_type": "image/jpeg",
            "file_size": 11,
            "width": None,
            "height": None,
            "origin_type": "from_url",
            "source_detail_image_id": None,
            "image_translate_task_id": None,
            "created_at": None,
        },
        serialize_detail_image_fn=lambda row: {"id": row["id"], "object_key": row["object_key"]},
        max_download_candidates=5,
    )

    assert result.status_code == 202
    assert result.payload == {
        "task_id": "mdf-test",
        "url": "https://store.example.test/products/handle",
    }
    assert calls == [
        ("create", 7, 123, "https://store.example.test/products/handle", "en"),
        ("fetch", "https://store.example.test/products/handle", "en"),
        ("existing-counts", 123, "en"),
        ("download", "https://cdn.example.test/detail-1.jpg", 123, "from_url_en_00", 7),
        (
            "add",
            123,
            "en",
            "7/medias/123/detail-1.jpg",
            {"content_type": None, "file_size": 11, "origin_type": "from_url"},
        ),
    ]
    assert worker_state["status"] == "done"
    assert worker_state["inserted"] == [{"id": 501, "object_key": "7/medias/123/detail-1.jpg"}]
    assert worker_state["errors"] == []


def test_from_url_response_rejects_plan_errors_without_starting_task():
    from web.services.media_detail_from_url import build_detail_images_from_url_response

    result = build_detail_images_from_url_response(
        123,
        7,
        {"localized_links_json": {}},
        {"lang": "en"},
        is_valid_language_fn=lambda code: code == "en",
        create_fetch_task_fn=lambda **kwargs: (_ for _ in ()).throw(AssertionError("task should not start")),
        fetch_page_fn=lambda url, lang: SimpleNamespace(images=[]),
        download_image_to_local_media_fn=lambda *args, **kwargs: (None, None, "unused"),
        soft_delete_detail_images_by_lang_fn=lambda product_id, lang: 0,
        detail_image_empty_counts_fn=lambda: {"image": 0, "gif": 0},
        detail_image_existing_counts_fn=lambda product_id, lang: {"image": 0, "gif": 0},
        detail_image_kind_from_download_ext_fn=lambda ext: "image",
        detail_image_limits={"image": 2, "gif": 1},
        detail_image_kind_labels={"image": "image", "gif": "GIF"},
        add_detail_image_fn=lambda *args, **kwargs: 1,
        get_detail_image_fn=lambda image_id: None,
        serialize_detail_image_fn=lambda row: row,
        max_download_candidates=5,
    )

    assert result.status_code == 400
    assert result.payload == {"error": "product_code required before inferring a default link"}


def test_from_url_status_response_returns_matching_task_for_user_and_product():
    from web.services.media_detail_from_url import build_detail_images_from_url_status_response

    calls = []
    task = {"task_id": "mdf-1", "product_id": 123, "status": "done"}

    result = build_detail_images_from_url_status_response(
        123,
        "mdf-1",
        7,
        get_fetch_task_fn=lambda task_id, *, user_id: calls.append((task_id, user_id)) or task,
    )

    assert result.status_code == 200
    assert result.payload == task
    assert calls == [("mdf-1", 7)]


def test_from_url_status_response_hides_missing_or_foreign_task():
    from web.services.media_detail_from_url import build_detail_images_from_url_status_response

    missing = build_detail_images_from_url_status_response(
        123,
        "missing",
        7,
        get_fetch_task_fn=lambda task_id, *, user_id: None,
    )
    foreign = build_detail_images_from_url_status_response(
        123,
        "foreign",
        7,
        get_fetch_task_fn=lambda task_id, *, user_id: {
            "task_id": task_id,
            "product_id": 999,
            "status": "done",
        },
    )

    assert missing.status_code == 404
    assert missing.payload == {"error": "task not found"}
    assert foreign.status_code == 404
    assert foreign.payload == {"error": "task not found"}
