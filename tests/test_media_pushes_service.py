from __future__ import annotations


def test_product_push_admin_required_response_is_standardized():
    from web.services.media_pushes import build_product_push_admin_required_response

    result = build_product_push_admin_required_response()

    assert result.status_code == 403
    assert result.payload == {"error": "\u4ec5\u7ba1\u7406\u5458\u53ef\u64cd\u4f5c"}


def test_product_links_push_preview_and_execute_responses_wrap_downstream():
    from web.services.media_pushes import (
        build_product_links_push_preview_response,
        build_product_links_push_response,
    )

    product = {"id": 10}
    preview = {"payload": {"links": []}}

    preview_response = build_product_links_push_preview_response(
        product,
        build_preview_fn=lambda row: preview if row is product else None,
    )
    ok_response = build_product_links_push_response(
        product,
        push_product_links_fn=lambda row: {"ok": True, "id": row["id"]},
    )
    failed_response = build_product_links_push_response(
        product,
        push_product_links_fn=lambda row: {"ok": False, "id": row["id"]},
    )

    assert preview_response.status_code == 200
    assert preview_response.payload == preview
    assert ok_response.status_code == 200
    assert ok_response.payload == {"ok": True, "id": 10}
    assert failed_response.status_code == 502
    assert failed_response.payload == {"ok": False, "id": 10}


def test_product_links_push_responses_map_downstream_errors():
    from appcore import pushes
    from web.services.media_pushes import (
        build_product_links_push_preview_response,
        build_product_links_push_response,
    )

    preview_response = build_product_links_push_preview_response(
        {"id": 10},
        build_preview_fn=lambda row: (_ for _ in ()).throw(
            pushes.ProductLinksPayloadError("bad_payload")
        ),
    )
    push_response = build_product_links_push_response(
        {"id": 10},
        push_product_links_fn=lambda row: (_ for _ in ()).throw(
            pushes.ProductLinksPushConfigError("missing_config")
        ),
    )

    assert preview_response.status_code == 400
    assert preview_response.payload == {"error": "bad_payload"}
    assert push_response.status_code == 500
    assert push_response.payload == {"error": "missing_config"}


def test_product_unsuitable_push_response_filters_single_type():
    from web.services.media_pushes import build_product_unsuitable_push_response

    calls = []

    def fake_push(product, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "kwargs": kwargs}

    copy_response = build_product_unsuitable_push_response(
        {"id": 10},
        {"type": "copy"},
        push_unsuitable_product_fn=fake_push,
    )
    all_response = build_product_unsuitable_push_response(
        {"id": 10},
        {"type": "invalid"},
        push_unsuitable_product_fn=fake_push,
    )

    assert copy_response.status_code == 200
    assert copy_response.payload == {"ok": True, "kwargs": {"only_type": "copy"}}
    assert all_response.status_code == 200
    assert all_response.payload == {"ok": True, "kwargs": {}}
    assert calls == [{"only_type": "copy"}, {}]


def test_product_localized_texts_push_preview_and_execute_responses_wrap_downstream():
    from web.services.media_pushes import (
        build_product_localized_texts_push_preview_response,
        build_product_localized_texts_push_response,
    )

    product = {"id": 10}
    preview_response = build_product_localized_texts_push_preview_response(
        product,
        build_preview_fn=lambda row: {"texts": [row["id"]]},
    )
    ok_response = build_product_localized_texts_push_response(
        product,
        push_localized_texts_fn=lambda row: {"ok": True, "id": row["id"]},
    )
    failed_response = build_product_localized_texts_push_response(
        product,
        push_localized_texts_fn=lambda row: {"ok": False, "id": row["id"]},
    )

    assert preview_response.status_code == 200
    assert preview_response.payload == {"texts": [10]}
    assert ok_response.status_code == 200
    assert ok_response.payload == {"ok": True, "id": 10}
    assert failed_response.status_code == 502
    assert failed_response.payload == {"ok": False, "id": 10}


def test_product_unsuitable_push_preview_response_wraps_downstream():
    from web.services.media_pushes import build_product_unsuitable_push_preview_response

    response = build_product_unsuitable_push_preview_response(
        {"id": 10},
        build_preview_fn=lambda row: {"types": [row["id"]]},
    )

    assert response.status_code == 200
    assert response.payload == {"types": [10]}


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
