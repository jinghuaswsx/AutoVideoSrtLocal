from __future__ import annotations

import requests


class FakeResponse:
    def __init__(self, *, ok=True, status_code=200, payload=None, json_error=None):
        self.ok = ok
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def test_build_mk_copywriting_response_fetches_normalized_query_and_first_matching_link():
    from web.services.media_mk_copywriting import build_mk_copywriting_response

    captured = {}

    def fake_get(url, *, params=None, headers=None, timeout=None):
        captured.update({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return FakeResponse(
            payload={
                "data": {
                    "items": [
                        {
                            "id": 1,
                            "product_links": ["https://shop.example/products/not-this-one"],
                            "texts": [{"title": "Wrong", "message": "Wrong", "description": "Wrong"}],
                        },
                        {
                            "id": 2,
                            "product_links": ["https://shop.example/products/demo-product"],
                            "texts": [{"title": "Title", "message": "Message", "description": "Desc"}],
                        },
                    ],
                },
            }
        )

    result = build_mk_copywriting_response(
        {"product_code": " Demo-Product-RJC "},
        build_headers_fn=lambda: {"Authorization": "Bearer token"},
        get_base_url_fn=lambda: "https://mk.example",
        is_login_expired_fn=lambda data: False,
        http_get_fn=fake_get,
    )

    assert result.status_code == 200
    assert result.payload["ok"] is True
    assert result.payload["query"] == "demo-product"
    assert result.payload["source_item_id"] == 2
    assert "Title" in result.payload["copywriting"]
    assert captured == {
        "url": "https://mk.example/api/marketing/medias",
        "params": {"page": 1, "q": "demo-product", "source": "", "level": "", "show_attention": 0},
        "headers": {"Authorization": "Bearer token"},
        "timeout": 15,
    }


def test_build_mk_copywriting_response_rejects_missing_query_before_request():
    from web.services.media_mk_copywriting import build_mk_copywriting_response

    called = []

    result = build_mk_copywriting_response(
        {"product_code": "   "},
        build_headers_fn=lambda: called.append("headers") or {"Authorization": "Bearer token"},
        get_base_url_fn=lambda: "https://mk.example",
        is_login_expired_fn=lambda data: False,
        http_get_fn=lambda *args, **kwargs: called.append("request"),
    )

    assert result.status_code == 400
    assert result.payload["error"] == "product_code_required"
    assert called == []


def test_build_mk_copywriting_response_requires_synced_credentials_before_request():
    from web.services.media_mk_copywriting import build_mk_copywriting_response

    called = []

    result = build_mk_copywriting_response(
        {"q": "demo"},
        build_headers_fn=lambda: {},
        get_base_url_fn=lambda: "https://mk.example",
        is_login_expired_fn=lambda data: False,
        http_get_fn=lambda *args, **kwargs: called.append("request"),
    )

    assert result.status_code == 500
    assert result.payload["error"] == "mk_credentials_missing"
    assert called == []


def test_build_mk_copywriting_response_maps_transport_and_bad_response_errors():
    from web.services.media_mk_copywriting import build_mk_copywriting_response

    common = {
        "args": {"q": "demo"},
        "build_headers_fn": lambda: {"Cookie": "token=1"},
        "get_base_url_fn": lambda: "https://mk.example",
        "is_login_expired_fn": lambda data: False,
    }

    request_failed = build_mk_copywriting_response(
        **common,
        http_get_fn=lambda *args, **kwargs: (_ for _ in ()).throw(requests.RequestException("down")),
    )
    http_failed = build_mk_copywriting_response(
        **common,
        http_get_fn=lambda *args, **kwargs: FakeResponse(ok=False, status_code=503),
    )
    invalid_json = build_mk_copywriting_response(
        **common,
        http_get_fn=lambda *args, **kwargs: FakeResponse(json_error=ValueError("bad json")),
    )

    assert (request_failed.status_code, request_failed.payload["error"]) == (502, "mk_request_failed")
    assert (http_failed.status_code, http_failed.payload["error"]) == (502, "mk_request_failed")
    assert (invalid_json.status_code, invalid_json.payload["error"]) == (502, "mk_response_invalid")


def test_build_mk_copywriting_response_maps_expired_missing_and_empty_copywriting():
    from web.services.media_mk_copywriting import build_mk_copywriting_response

    common = {
        "args": {"q": "demo"},
        "build_headers_fn": lambda: {"Authorization": "Bearer token"},
        "get_base_url_fn": lambda: "https://mk.example",
    }

    expired = build_mk_copywriting_response(
        **common,
        is_login_expired_fn=lambda data: True,
        http_get_fn=lambda *args, **kwargs: FakeResponse(payload={"is_guest": True}),
    )
    missing = build_mk_copywriting_response(
        **common,
        is_login_expired_fn=lambda data: False,
        http_get_fn=lambda *args, **kwargs: FakeResponse(payload={"data": {"items": []}}),
    )
    empty = build_mk_copywriting_response(
        **common,
        is_login_expired_fn=lambda data: False,
        http_get_fn=lambda *args, **kwargs: FakeResponse(
            payload={
                "data": {
                    "items": [
                        {
                            "id": 9,
                            "product_links": ["https://shop.example/products/demo"],
                            "texts": [{"title": "", "message": "", "description": ""}],
                        }
                    ]
                }
            }
        ),
    )

    assert (expired.status_code, expired.payload["error"]) == (401, "mk_credentials_expired")
    assert (missing.status_code, missing.payload["error"]) == (404, "mk_copywriting_not_found")
    assert (empty.status_code, empty.payload["error"], empty.payload["source_item_id"]) == (
        404,
        "mk_copywriting_empty",
        9,
    )
