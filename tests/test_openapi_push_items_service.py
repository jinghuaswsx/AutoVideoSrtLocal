from __future__ import annotations

from datetime import datetime


def test_serialize_push_item_includes_latest_push_and_cover_url(monkeypatch):
    from web.services import openapi_push_items

    monkeypatch.setattr(
        openapi_push_items.pushes,
        "compute_readiness",
        lambda item, product: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "lang_supported": True,
        },
    )
    monkeypatch.setattr(
        openapi_push_items.pushes,
        "compute_status",
        lambda item, product: "failed",
    )

    created_at = datetime(2026, 5, 5, 9, 30, 0)
    calls: list[tuple[str, tuple]] = []

    def fake_query_one(sql: str, args: tuple) -> dict:
        calls.append((sql, args))
        return {
            "status": "failed",
            "error_message": "HTTP 500",
            "created_at": created_at,
        }

    item = {
        "id": 456,
        "product_id": 123,
        "lang": "de",
        "filename": "demo.mp4",
        "display_name": "Demo",
        "file_size": 1234,
        "duration_seconds": 12.3,
        "cover_object_key": "covers/demo.jpg",
        "pushed_at": None,
        "latest_push_id": 88,
        "created_at": created_at,
    }
    product = {
        "product_code": "alpha",
        "name": "Alpha",
        "listing_status": "上架",
    }

    payload = openapi_push_items.serialize_push_item(
        item,
        product,
        query_one_fn=fake_query_one,
        media_download_url_fn=lambda key: f"https://local/{key}",
    )

    assert calls and calls[0][1] == (88,)
    assert payload["item_id"] == 456
    assert payload["product_code"] == "alpha"
    assert payload["listing_status"] == "上架"
    assert payload["lang"] == "de"
    assert payload["display_name"] == "Demo"
    assert payload["cover_url"] == "https://local/covers/demo.jpg"
    assert payload["status"] == "failed"
    assert payload["readiness"]["has_cover"] is True
    assert payload["latest_push"] == {
        "status": "failed",
        "error_message": "HTTP 500",
        "created_at": created_at.isoformat(),
    }
    assert payload["created_at"] == created_at.isoformat()


def test_serialize_push_item_defaults_without_latest_push(monkeypatch):
    from web.services import openapi_push_items

    monkeypatch.setattr(
        openapi_push_items.pushes,
        "compute_readiness",
        lambda item, product: {"has_object": True},
    )
    monkeypatch.setattr(
        openapi_push_items.pushes,
        "compute_status",
        lambda item, product: "pending",
    )

    def fail_query_one(sql: str, args: tuple) -> dict | None:
        raise AssertionError("latest push lookup should not run without latest_push_id")

    payload = openapi_push_items.serialize_push_item(
        {
            "id": 1,
            "product_id": 2,
            "lang": "",
            "filename": "fallback.mp4",
            "display_name": "",
            "cover_object_key": None,
            "latest_push_id": None,
        },
        {"product_code": "p", "name": "Product"},
        query_one_fn=fail_query_one,
        media_download_url_fn=lambda key: f"https://local/{key}",
    )

    assert payload["lang"] == "en"
    assert payload["display_name"] == "fallback.mp4"
    assert payload["cover_url"] is None
    assert payload["latest_push"] is None


def test_product_shape_from_push_row_projects_product_fields():
    from web.services import openapi_push_items

    shape = openapi_push_items.product_shape_from_push_row({
        "product_id": 10,
        "product_name": "Alpha",
        "product_code": "alpha",
        "ad_supported_langs": "en,de",
        "shopify_image_status_json": "{}",
        "selling_points": "point",
        "importance": 3,
        "listing_status": None,
        "ignored": "value",
    })

    assert shape == {
        "id": 10,
        "name": "Alpha",
        "product_code": "alpha",
        "ad_supported_langs": "en,de",
        "shopify_image_status_json": "{}",
        "selling_points": "point",
        "importance": 3,
        "listing_status": None,
    }


def test_serialize_push_item_rows_uses_project_shape_and_query_one(monkeypatch):
    from web.services import openapi_push_items

    monkeypatch.setattr(
        openapi_push_items.pushes,
        "compute_readiness",
        lambda item, product: {"product_code": product.get("product_code")},
    )
    monkeypatch.setattr(
        openapi_push_items.pushes,
        "compute_status",
        lambda item, product: "pending",
    )

    def fail_query_one(sql: str, args: tuple) -> dict | None:
        raise AssertionError("latest push lookup should not run for this row")

    rows = [
        {
            "id": 1,
            "product_id": 10,
            "lang": "de",
            "filename": "demo.mp4",
            "display_name": "Demo",
            "cover_object_key": None,
            "latest_push_id": None,
            "product_name": "Alpha",
            "product_code": "alpha",
            "ad_supported_langs": "de",
            "selling_points": "",
            "importance": 3,
        },
    ]

    payloads = openapi_push_items.serialize_push_item_rows(
        rows,
        query_one_fn=fail_query_one,
    )

    assert len(payloads) == 1
    assert payloads[0]["item_id"] == 1
    assert payloads[0]["product_code"] == "alpha"
    assert payloads[0]["product_name"] == "Alpha"
    assert payloads[0]["readiness"] == {"product_code": "alpha"}


def test_filter_and_paginate_push_items_by_status():
    from web.services import openapi_push_items

    items = [
        {"item_id": 1, "status": "pending"},
        {"item_id": 2, "status": "pushed"},
        {"item_id": 3, "status": "failed"},
        {"item_id": 4, "status": "pushed"},
    ]

    filtered = openapi_push_items.filter_push_items_by_status(items, ["pushed"])
    assert [item["item_id"] for item in filtered] == [2, 4]
    assert openapi_push_items.paginate_push_items(filtered, page=2, page_size=1) == [
        {"item_id": 4, "status": "pushed"},
    ]
    assert openapi_push_items.filter_push_items_by_status(items, []) == items


def test_build_push_item_payload_response_combines_payload_and_localized_text(monkeypatch):
    from web.services import openapi_push_items

    monkeypatch.setattr(
        openapi_push_items.pushes,
        "compute_readiness",
        lambda item, product: {"has_object": True},
    )
    monkeypatch.setattr(
        openapi_push_items.pushes,
        "compute_status",
        lambda item, product: "pending",
    )
    monkeypatch.setattr(
        openapi_push_items.pushes,
        "build_item_payload",
        lambda item, product: {
            "mode": "create",
            "texts": [{"title": "x", "message": "y", "description": "z"}],
        },
    )
    monkeypatch.setattr(
        openapi_push_items.pushes,
        "resolve_localized_text_payload",
        lambda item: {"title": "fr1", "message": "fr2", "description": "fr3", "lang": "法语"},
    )
    monkeypatch.setattr(
        openapi_push_items.pushes,
        "build_localized_texts_request",
        lambda item: {"texts": [{"title": "fr1", "message": "fr2"}]},
    )

    item = {
        "id": 238,
        "product_id": 10,
        "lang": "fr",
        "filename": "demo.mp4",
        "display_name": "demo.mp4",
        "object_key": "k.mp4",
        "cover_object_key": "k.jpg",
        "latest_push_id": None,
    }
    product = {
        "id": 10,
        "name": "P",
        "product_code": "p",
        "mk_id": 3725,
        "ad_supported_langs": "fr",
    }

    payload = openapi_push_items.build_push_item_payload_response(
        item,
        product,
        query_one_fn=lambda sql, args: None,
        media_download_url_fn=lambda key: f"https://local/{key}",
    )

    assert payload["item_id"] == 238
    assert payload["mk_id"] == 3725
    assert payload["payload"]["mode"] == "create"
    assert payload["localized_text"] == {
        "title": "fr1",
        "message": "fr2",
        "description": "fr3",
        "lang": "法语",
    }
    assert payload["localized_texts_request"] == {"texts": [{"title": "fr1", "message": "fr2"}]}
    assert payload["item"]["item_id"] == 238
    assert payload["item"]["cover_url"] == "https://local/k.jpg"


def test_build_mark_pushed_response_records_payload():
    from web.services import openapi_push_items

    captured: dict = {}

    def fake_record_success(**kwargs):
        captured.update(kwargs)
        return 42

    payload = openapi_push_items.build_mark_pushed_response(
        456,
        {"request_payload": {"mode": "create"}, "response_body": "ok"},
        operator_user_id=7,
        record_success_fn=fake_record_success,
    )

    assert payload == {"ok": True, "log_id": 42}
    assert captured == {
        "item_id": 456,
        "operator_user_id": 7,
        "payload": {"mode": "create"},
        "response_body": "ok",
    }


def test_build_mark_failed_response_records_error_payload():
    from web.services import openapi_push_items

    captured: dict = {}

    def fake_record_failure(**kwargs):
        captured.update(kwargs)
        return 99

    payload = openapi_push_items.build_mark_failed_response(
        456,
        {
            "request_payload": {"mode": "create"},
            "response_body": "oops",
            "error_message": "HTTP 500",
        },
        operator_user_id=8,
        record_failure_fn=fake_record_failure,
    )

    assert payload == {"ok": True, "log_id": 99}
    assert captured == {
        "item_id": 456,
        "operator_user_id": 8,
        "payload": {"mode": "create"},
        "error_message": "HTTP 500",
        "response_body": "oops",
    }
