from __future__ import annotations

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
