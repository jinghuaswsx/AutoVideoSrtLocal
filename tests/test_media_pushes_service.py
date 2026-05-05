from __future__ import annotations


def test_product_links_push_error_response_maps_business_errors():
    from appcore import pushes
    from web.services.media_pushes import build_product_links_push_error_response

    not_listed = build_product_links_push_error_response(
        pushes.ProductNotListedError("product_not_listed")
    )
    config = build_product_links_push_error_response(
        pushes.ProductLinksPushConfigError("missing_config")
    )
    payload = build_product_links_push_error_response(
        pushes.ProductLinksPayloadError("bad_payload")
    )
    unknown = build_product_links_push_error_response(RuntimeError("boom"))

    assert not_listed.status_code == 409
    assert not_listed.payload == {
        "error": "product_not_listed",
        "message": "产品已下架，不能推送投放链接",
    }
    assert config.status_code == 500
    assert config.payload == {"error": "missing_config"}
    assert payload.status_code == 400
    assert payload.payload == {"error": "bad_payload"}
    assert unknown.status_code == 500
    assert unknown.payload == {"error": "product_links_push_failed", "message": "boom"}


def test_product_localized_texts_push_error_response_maps_business_errors():
    from appcore import pushes
    from web.services.media_pushes import build_product_localized_texts_push_error_response

    not_listed = build_product_localized_texts_push_error_response(
        pushes.ProductNotListedError("product_not_listed")
    )
    config = build_product_localized_texts_push_error_response(
        pushes.ProductLocalizedTextsPushConfigError("missing_text_config")
    )
    payload = build_product_localized_texts_push_error_response(
        pushes.ProductLocalizedTextsPayloadError("bad_text_payload")
    )
    unknown = build_product_localized_texts_push_error_response(RuntimeError("boom"))

    assert not_listed.status_code == 409
    assert not_listed.payload == {
        "error": "product_not_listed",
        "message": "产品已下架，不能推送小语种文案",
    }
    assert config.status_code == 500
    assert config.payload == {"error": "missing_text_config"}
    assert payload.status_code == 400
    assert payload.payload == {"error": "bad_text_payload"}
    assert unknown.status_code == 500
    assert unknown.payload == {
        "error": "product_localized_texts_push_failed",
        "message": "boom",
    }


def test_product_unsuitable_push_error_response_maps_both_downstreams():
    from appcore import pushes
    from web.services.media_pushes import build_product_unsuitable_push_error_response

    not_listed = build_product_unsuitable_push_error_response(
        pushes.ProductNotListedError("product_not_listed")
    )
    copy_config = build_product_unsuitable_push_error_response(
        pushes.ProductLocalizedTextsPushConfigError("missing_text_config")
    )
    links_config = build_product_unsuitable_push_error_response(
        pushes.ProductLinksPushConfigError("missing_links_config")
    )
    copy_payload = build_product_unsuitable_push_error_response(
        pushes.ProductLocalizedTextsPayloadError("bad_text_payload")
    )
    links_payload = build_product_unsuitable_push_error_response(
        pushes.ProductLinksPayloadError("bad_links_payload")
    )
    unknown = build_product_unsuitable_push_error_response(RuntimeError("boom"))

    assert not_listed.status_code == 409
    assert not_listed.payload == {
        "error": "product_not_listed",
        "message": "产品已下架，不能推送不合适标注",
    }
    assert copy_config.status_code == 500
    assert copy_config.payload == {"error": "missing_text_config"}
    assert links_config.status_code == 500
    assert links_config.payload == {"error": "missing_links_config"}
    assert copy_payload.status_code == 400
    assert copy_payload.payload == {"error": "bad_text_payload"}
    assert links_payload.status_code == 400
    assert links_payload.payload == {"error": "bad_links_payload"}
    assert unknown.status_code == 500
    assert unknown.payload == {"error": "product_unsuitable_push_failed", "message": "boom"}
