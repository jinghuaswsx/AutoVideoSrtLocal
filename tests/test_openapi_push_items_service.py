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
