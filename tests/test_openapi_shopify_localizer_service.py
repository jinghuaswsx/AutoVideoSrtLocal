from __future__ import annotations

import pytest


def test_shopify_localizer_builds_bootstrap_response():
    from web.services.openapi_shopify_localizer import build_shopify_localizer_bootstrap_response

    captured: dict = {}

    def fake_is_valid_language(lang):
        captured["valid_lang"] = lang
        return lang == "it"

    def fake_get_product_by_code(code):
        captured["product_code"] = code
        return {"id": 123, "product_code": code, "name": "Demo Product"}

    def fake_resolve_shopify_product_id(product_id):
        captured["resolve_shopify_product_id"] = product_id
        return "from-product"

    def fake_list_reference_images(product_id, lang):
        captured.setdefault("references", []).append((product_id, lang))
        return [
            {"id": f"cover-{lang}", "kind": "cover", "filename": f"{lang}-cover.jpg", "object_key": f"{lang}-cover.jpg"},
            {"id": f"empty-{lang}", "kind": "detail", "filename": f"{lang}-empty.jpg", "object_key": ""},
            {"id": f"detail-{lang}", "kind": "detail", "filename": f"{lang}.jpg", "object_key": f"{lang}.jpg"},
        ]

    payload = build_shopify_localizer_bootstrap_response(
        {
            "product_code": " Sonic-Lens-Refresher-RJC ",
            "lang": "IT",
            "shopify_product_id": "8559391932589",
        },
        is_valid_language_fn=fake_is_valid_language,
        get_product_by_code_fn=fake_get_product_by_code,
        resolve_shopify_product_id_fn=fake_resolve_shopify_product_id,
        list_reference_images_for_lang_fn=fake_list_reference_images,
        get_language_name_fn=lambda lang: "Italian",
        resolve_link_urls_fn=lambda product, lang: [
            {
                "domain": "newjoyloo.com",
                "lang": lang,
                "status_key": f"newjoyloo.com:{lang}",
                "url": f"https://newjoyloo.com/{lang}/products/{product['product_code']}",
            },
            {
                "domain": "omurio.com",
                "lang": lang,
                "status_key": f"omurio.com:{lang}",
                "url": f"https://omurio.com/{lang}/products/{product['product_code']}",
            },
        ],
        media_download_url_fn=lambda object_key: f"http://local.test/{object_key}",
    )

    assert captured["valid_lang"] == "it"
    assert captured["product_code"] == "sonic-lens-refresher-rjc"
    assert "resolve_shopify_product_id" not in captured
    assert captured["references"] == [(123, "en"), (123, "it")]
    assert payload["product"] == {
        "id": 123,
        "product_code": "sonic-lens-refresher-rjc",
        "shopify_product_id": "8559391932589",
        "name": "Demo Product",
    }
    assert payload["language"] == {
        "code": "it",
        "name_zh": "Italian",
        "shop_locale": "it",
        "folder_code": "it",
    }
    assert [
        {key: row[key] for key in ("id", "kind", "filename", "url")}
        for row in payload["reference_images"]
    ] == [
        {
            "id": "detail-en",
            "kind": "detail",
            "filename": "en.jpg",
            "url": "http://local.test/en.jpg",
        }
    ]
    assert payload["reference_images"][0]["source_index"] is None
    assert payload["reference_images"][0]["source_name_key"] == "name:en"
    assert payload["reference_images"][0]["source_token"] is None
    assert payload["localized_images"][0]["url"] == "http://local.test/it.jpg"
    assert payload["link_url"] == "https://newjoyloo.com/it/products/sonic-lens-refresher-rjc"
    assert payload["link_urls"] == [
        {
            "domain": "newjoyloo.com",
            "lang": "it",
            "status_key": "newjoyloo.com:it",
            "url": "https://newjoyloo.com/it/products/sonic-lens-refresher-rjc",
        },
        {
            "domain": "omurio.com",
            "lang": "it",
            "status_key": "omurio.com:it",
            "url": "https://omurio.com/it/products/sonic-lens-refresher-rjc",
        },
    ]


def test_shopify_localizer_bootstrap_adds_source_metadata_and_keeps_duplicate_rows():
    from web.services.openapi_shopify_localizer import build_shopify_localizer_bootstrap_response

    duplicate_token = "b0d7cac6bbce4313a7ff2883a7818803d"
    unique_token = "e62650881d57eb4a90def4702e2f9072"

    def fake_list_reference_images(product_id, lang):
        assert product_id == 123
        if lang == "en":
            return [
                {
                    "id": "en-0",
                    "kind": "detail",
                    "filename": f"en_from_url_en_00_{duplicate_token}.webp.jpg",
                    "object_key": "en-0.jpg",
                }
            ]
        return [
            {
                "id": "loc-0",
                "kind": "detail",
                "filename": f"loc_from_url_en_00_{duplicate_token}.webp.jpg",
                "object_key": "loc-0.jpg",
            },
            {
                "id": "loc-1",
                "kind": "detail",
                "filename": f"loc_from_url_en_01_{duplicate_token}.webp.jpg",
                "object_key": "loc-1.jpg",
            },
            {
                "id": "loc-8",
                "kind": "detail",
                "filename": f"loc_from_url_en_08_{unique_token}.webp.jpg",
                "object_key": "loc-8.jpg",
            },
            {
                "id": "loc-gif",
                "kind": "detail",
                "filename": "loc_from_url_en_09_spinner.gif",
                "object_key": "loc-spinner.gif",
            },
        ]

    payload = build_shopify_localizer_bootstrap_response(
        {"product_code": "pet-bath-brush-rjc", "lang": "de", "shopify_product_id": "8602533626029"},
        is_valid_language_fn=lambda lang: lang == "de",
        get_product_by_code_fn=lambda code: {"id": 123, "product_code": code, "name": "Pet Bath Brush"},
        list_reference_images_for_lang_fn=fake_list_reference_images,
        get_language_name_fn=lambda lang: "German",
        resolve_link_urls_fn=lambda _product, _lang: [],
        media_download_url_fn=lambda object_key: f"http://local.test/{object_key}",
    )

    localized = payload["localized_images"]
    assert [row["id"] for row in localized] == ["loc-0", "loc-1", "loc-8"]
    assert [row["source_index"] for row in localized] == [0, 1, 8]
    assert [row["source_token"] for row in localized] == [duplicate_token, duplicate_token, unique_token]
    assert [row["source_duplicate_count"] for row in localized] == [2, 2, 1]
    assert [row["source_duplicate"] for row in localized] == [True, True, False]
    assert localized[0]["source_name_key"] == f"name:{duplicate_token}"
    assert localized[0]["url"] == "http://local.test/loc-0.jpg"


def test_shopify_localizer_bootstrap_defaults_to_shopify_image_list(monkeypatch):
    from web.services import openapi_shopify_localizer as service

    captured: list[tuple[int, str]] = []

    def fake_shopify_images(product_id, lang):
        captured.append((product_id, lang))
        return [
            {
                "id": f"detail-{lang}",
                "kind": "detail",
                "filename": f"from_url_en_00_{lang}aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
                "object_key": f"{lang}.jpg",
            }
        ]

    def fail_reference_images(*_args, **_kwargs):
        raise AssertionError("bootstrap should use list_shopify_localizer_images by default")

    monkeypatch.setattr(service.medias, "list_shopify_localizer_images", fake_shopify_images)
    monkeypatch.setattr(service.medias, "list_reference_images_for_lang", fail_reference_images)

    payload = service.build_shopify_localizer_bootstrap_response(
        {"product_code": "pet-bath-brush-rjc", "lang": "fr", "shopify_product_id": "8602533626029"},
        is_valid_language_fn=lambda lang: lang == "fr",
        get_product_by_code_fn=lambda code: {"id": 123, "product_code": code, "name": "Pet Bath Brush"},
        get_language_name_fn=lambda lang: "French",
        resolve_link_urls_fn=lambda _product, _lang: [],
        media_download_url_fn=lambda object_key: f"http://local.test/{object_key}",
    )

    assert captured == [(123, "en"), (123, "fr")]
    assert payload["localized_images"][0]["url"] == "http://local.test/fr.jpg"


def test_shopify_localizer_rejects_english_target_language():
    from web.services.openapi_shopify_localizer import (
        ShopifyLocalizerBootstrapError,
        build_shopify_localizer_bootstrap_response,
    )

    with pytest.raises(ShopifyLocalizerBootstrapError) as exc:
        build_shopify_localizer_bootstrap_response(
            {"product_code": "demo", "lang": "en"},
            is_valid_language_fn=lambda lang: True,
        )

    assert exc.value.error == "invalid_target_lang"
    assert exc.value.status_code == 400


def test_shopify_localizer_domains_response_returns_all_configured_domains():
    from web.services.openapi_shopify_localizer import build_shopify_localizer_domains_response

    captured: dict = {}

    def fake_list_domains(*, include_disabled: bool = False):
        captured["include_disabled"] = include_disabled
        return [
            {"id": 1, "domain": "newjoyloo.com", "enabled": True},
            {"id": 2, "domain": "omurio.com", "enabled": True},
            {"id": 3, "domain": "disabled.test", "enabled": False},
        ]

    payload = build_shopify_localizer_domains_response(list_domains_fn=fake_list_domains)

    assert captured["include_disabled"] is True
    assert payload == {
        "items": [
            {"id": 1, "domain": "newjoyloo.com", "enabled": True},
            {"id": 2, "domain": "omurio.com", "enabled": True},
            {"id": 3, "domain": "disabled.test", "enabled": False},
        ]
    }


def test_shopify_localizer_product_link_save_updates_target_domain_only():
    from web.services.openapi_shopify_localizer import build_shopify_localizer_product_link_save_response

    captured: dict = {}

    payload = build_shopify_localizer_product_link_save_response(
        {
            "product_code": " Instant-Snap-Iodine-Swabs-RJC ",
            "lang": "IT",
            "domain": "NewJoyloo.com",
            "link_url": "https://newjoyloo.com/it/products/instant-snap-iodine-swabs-rjc?variant=46081369309357",
        },
        is_valid_language_fn=lambda lang: lang == "it",
        get_product_by_code_fn=lambda code: {
            "id": 704,
            "product_code": code,
            "localized_links_json": {
                "de": "https://newjoyloo.com/de/products/demo-rjc",
                "it": {"omurio.com": "https://omurio.com/it/products/demo-rjc"},
            },
        },
        update_product_fn=lambda product_id, **fields: captured.update({"product_id": product_id, "fields": fields}) or 1,
    )

    assert payload["ok"] is True
    assert payload["product_id"] == 704
    assert payload["domain"] == "newjoyloo.com"
    assert payload["link_url"].endswith("?variant=46081369309357")
    assert captured["product_id"] == 704
    assert captured["fields"]["localized_links_json"] == {
        "de": "https://newjoyloo.com/de/products/demo-rjc",
        "it": {
            "omurio.com": "https://omurio.com/it/products/demo-rjc",
            "newjoyloo.com": "https://newjoyloo.com/it/products/instant-snap-iodine-swabs-rjc?variant=46081369309357",
        },
    }


def test_shopify_localizer_task_claim_builds_response():
    from web.services.openapi_shopify_localizer import build_shopify_localizer_task_claim_response

    captured: dict = {}

    def fake_claim(worker_id, lock_seconds=900):
        captured["claim"] = (worker_id, lock_seconds)
        return {"id": 9, "product_code": "demo-rjc"}

    payload = build_shopify_localizer_task_claim_response(
        {"worker_id": " worker-1 ", "lock_seconds": "300"},
        claim_next_task_fn=fake_claim,
        serialize_shopify_image_task_fn=lambda task: {"id": task["id"], "code": task["product_code"]},
    )

    assert captured["claim"] == ("worker-1", 300)
    assert payload == {"task": {"id": 9, "code": "demo-rjc"}}


def test_shopify_localizer_task_heartbeat_uses_safe_defaults():
    from web.services.openapi_shopify_localizer import build_shopify_localizer_task_heartbeat_response

    captured: dict = {}

    def fake_heartbeat(task_id, worker_id, lock_seconds):
        captured["heartbeat"] = (task_id, worker_id, lock_seconds)
        return 1

    payload = build_shopify_localizer_task_heartbeat_response(
        9,
        {"worker_id": "worker-1", "lock_seconds": "bad"},
        heartbeat_task_fn=fake_heartbeat,
    )

    assert captured["heartbeat"] == (9, "worker-1", 900)
    assert payload == {"ok": True}


def test_shopify_localizer_task_complete_and_fail_build_responses():
    from web.services.openapi_shopify_localizer import (
        build_shopify_localizer_task_complete_response,
        build_shopify_localizer_task_fail_response,
    )

    captured: dict = {}

    complete_payload = build_shopify_localizer_task_complete_response(
        9,
        {"result": {"carousel": {"ok": 11}}},
        complete_task_fn=lambda task_id, result: captured.update({"complete": (task_id, result)})
        or {"replace_status": "auto_done"},
    )
    fail_payload = build_shopify_localizer_task_fail_response(
        9,
        {"error_code": "boom", "error_message": "failed", "result": {"x": 1}},
        fail_task_fn=lambda task_id, error_code, error_message, result: captured.update({
            "fail": (task_id, error_code, error_message, result),
        }) or {"replace_status": "failed"},
    )

    assert captured["complete"] == (9, {"carousel": {"ok": 11}})
    assert complete_payload == {"ok": True, "status": {"replace_status": "auto_done"}}
    assert captured["fail"] == (9, "boom", "failed", {"x": 1})
    assert fail_payload == {"ok": True, "status": {"replace_status": "failed"}}
