from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient


AUTOPUSH_DIR = Path(__file__).resolve().parents[1] / "AutoPush"
if str(AUTOPUSH_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOPUSH_DIR))


def _load_autopush_main():
    spec = importlib.util.spec_from_file_location(
        "autopush_main_under_test",
        AUTOPUSH_DIR / "main.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _build_client(monkeypatch):
    monkeypatch.setenv("AUTOVIDEO_BASE_URL", "http://example.com")
    monkeypatch.setenv("AUTOVIDEO_API_KEY", "demo-key")
    monkeypatch.setenv("PUSH_MEDIAS_TARGET", "http://push-target.example/push")
    monkeypatch.setenv("PUSH_LOCALIZED_TEXTS_BASE_URL", "https://os.wedev.vip")
    monkeypatch.setenv("PUSH_LOCALIZED_TEXTS_AUTHORIZATION", "Bearer demo-token")

    settings = importlib.import_module("backend.settings")
    settings.get_settings.cache_clear()
    main = _load_autopush_main()
    return TestClient(main.create_app())


def test_push_localized_texts_proxies_to_marketing_api(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b'{"ok": true}'
        text = '{"ok": true}'

        def json(self):
            return {"ok": True}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return FakeResponse()

    routes = importlib.import_module("backend.routes")
    monkeypatch.setattr(
        routes,
        "resolve_chrome_auth_headers",
        lambda target: {
            "Authorization": "Bearer browser-token",
            "Cookie": "token=browser-token; x-hng=lang=zh-CN&domain=os.wedev.vip",
        },
    )
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda timeout=30.0: FakeClient())

    client = _build_client(monkeypatch)
    response = client.post(
        "/api/marketing/medias/3725/texts",
        json={
            "texts": [{
                "title": "fr1",
                "message": "fr2",
                "description": "fr3",
                "lang": "法语",
            }]
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "upstream_status": 200,
        "upstream": {"ok": True},
        "target_url": "https://os.wedev.vip/api/marketing/medias/3725/texts",
    }
    assert captured["url"] == "https://os.wedev.vip/api/marketing/medias/3725/texts"
    assert captured["json"]["texts"][0]["title"] == "fr1"
    assert captured["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer browser-token",
        "Cookie": "token=browser-token; x-hng=lang=zh-CN&domain=os.wedev.vip",
    }


def test_push_localized_texts_returns_http_error_body(monkeypatch):
    class FakeResponse:
        status_code = 400
        content = b'{"error": "bad request"}'
        text = '{"error": "bad request"}'

        def json(self):
            return {"error": "bad request"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            return FakeResponse()

    routes = importlib.import_module("backend.routes")
    monkeypatch.setattr(routes, "resolve_chrome_auth_headers", lambda target: {})
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda timeout=30.0: FakeClient())

    client = _build_client(monkeypatch)
    response = client.post(
        "/api/marketing/medias/3725/texts",
        json={"texts": []},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "upstream_status": 400,
        "body": {"error": "bad request"},
        "target_url": "https://os.wedev.vip/api/marketing/medias/3725/texts",
    }


def test_push_localized_texts_falls_back_to_env_authorization(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b'{"ok": true}'
        text = '{"ok": true}'

        def json(self):
            return {"ok": True}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            captured["headers"] = headers
            return FakeResponse()

    routes = importlib.import_module("backend.routes")
    monkeypatch.setattr(routes, "resolve_chrome_auth_headers", lambda target: {})
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda timeout=30.0: FakeClient())

    client = _build_client(monkeypatch)
    response = client.post(
        "/api/marketing/medias/3725/texts",
        json={"texts": []},
    )

    assert response.status_code == 200
    assert captured["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer demo-token",
    }


def test_push_item_rejects_oversize_before_downstream_post(monkeypatch):
    class FakeService:
        async def get_push_item(self, item_id):
            return {"item_id": item_id, "file_size": 101 * 1024 * 1024}

    class FailClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            raise AssertionError("oversize item must not post downstream")

    routes = importlib.import_module("backend.routes")
    monkeypatch.setattr(routes, "_service", lambda: FakeService())
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda timeout=30.0: FailClient())

    client = _build_client(monkeypatch)
    response = client.post(
        "/api/push-items/7/push",
        json={"videos": [{"size": 12 * 1024 * 1024}]},
    )

    body = response.json()
    assert response.status_code == 413
    assert body["detail"]["error"] == "video_too_large"
    assert body["detail"]["size_mb"] == "101.0 MB"


def test_push_medias_rejects_oversize_payload_before_downstream_post(monkeypatch):
    class FailClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            raise AssertionError("oversize payload must not post downstream")

    routes = importlib.import_module("backend.routes")
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda timeout=30.0: FailClient())

    client = _build_client(monkeypatch)
    response = client.post(
        "/api/push/medias",
        json={"videos": [{"size": 101 * 1024 * 1024}]},
    )

    body = response.json()
    assert response.status_code == 413
    assert body["detail"]["error"] == "video_too_large"
    assert body["detail"]["size_mb"] == "101.0 MB"
