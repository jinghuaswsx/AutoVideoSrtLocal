from __future__ import annotations

import json


def test_fetch_bootstrap_returns_json_payload(monkeypatch):
    from link_check_desktop import bootstrap_api

    calls = []

    class DummyResponse:
        status_code = 200

        def json(self):
            return {"product": {"id": 123}, "target_language": "de"}

    def fake_post(url, *, headers, json, timeout):
        calls.append({
            "url": url,
            "headers": headers,
            "json": json,
            "timeout": timeout,
        })
        return DummyResponse()

    monkeypatch.setattr(bootstrap_api.requests, "post", fake_post)

    payload = bootstrap_api.fetch_bootstrap(
        "http://127.0.0.1:5000/",
        "demo-key",
        "https://newjoyloo.com/de/products/demo-rjc",
    )

    assert payload == {"product": {"id": 123}, "target_language": "de"}
    assert calls == [{
        "url": "http://127.0.0.1:5000/openapi/link-check/bootstrap",
        "headers": {"X-API-Key": "demo-key"},
        "json": {"target_url": "https://newjoyloo.com/de/products/demo-rjc"},
        "timeout": 20,
    }]


def test_fetch_bootstrap_raises_for_conflict(monkeypatch):
    from link_check_desktop import bootstrap_api

    class DummyResponse:
        status_code = 409

        def json(self):
            return {"error": "references not ready"}

    monkeypatch.setattr(bootstrap_api.requests, "post", lambda *args, **kwargs: DummyResponse())

    try:
        bootstrap_api.fetch_bootstrap(
            "http://127.0.0.1:5000",
            "demo-key",
            "https://newjoyloo.com/de/products/demo-rjc",
        )
    except bootstrap_api.BootstrapError as exc:
        assert exc.status_code == 409
        assert exc.payload == {"error": "references not ready"}
    else:
        raise AssertionError("expected BootstrapError")


def test_fetch_bootstrap_raises_clear_error_for_non_json_response(monkeypatch):
    from link_check_desktop import bootstrap_api

    class DummyResponse:
        status_code = 404
        text = "<html><title>404 Not Found</title></html>"

        def json(self):
            raise json.JSONDecodeError("Expecting value", self.text, 0)

    monkeypatch.setattr(bootstrap_api.requests, "post", lambda *args, **kwargs: DummyResponse())

    try:
        bootstrap_api.fetch_bootstrap(
            "http://127.0.0.1:5000",
            "demo-key",
            "https://newjoyloo.com/de/products/demo-rjc",
        )
    except bootstrap_api.BootstrapError as exc:
        assert exc.status_code == 404
        assert exc.payload["error"] == "bootstrap returned non-json response"
        assert "404 Not Found" in exc.payload["raw_response"]
    else:
        raise AssertionError("expected BootstrapError")
