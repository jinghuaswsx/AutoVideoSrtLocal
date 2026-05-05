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
    assert payload["reference_images"] == [
        {
            "id": "detail-en",
            "kind": "detail",
            "filename": "en.jpg",
            "url": "http://local.test/en.jpg",
        }
    ]
    assert payload["localized_images"][0]["url"] == "http://local.test/it.jpg"


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
