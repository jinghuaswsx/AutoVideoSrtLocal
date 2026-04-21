"""`/openapi/link-check/bootstrap` 路由测试。"""
from __future__ import annotations

import importlib
import os

import pytest

from web.app import create_app


@pytest.fixture(scope="module")
def client():
    os.environ["FLASK_SECRET_KEY"] = "demo-secret"
    os.environ["OPENAPI_MEDIA_API_KEY"] = "demo-key"
    import config as _config

    importlib.reload(_config)
    import web.app as web_app

    web_app.recover_all_interrupted_tasks = lambda: None
    app = create_app()
    return app.test_client()


def _stub_enabled_languages(monkeypatch):
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_languages",
        lambda: [
            {"code": "de", "enabled": 1},
            {"code": "en", "enabled": 1},
            {"code": "fr", "enabled": 1},
        ],
    )


def test_bootstrap_rejects_missing_api_key(client):
    response = client.post("/openapi/link-check/bootstrap", json={"target_url": "https://example.com/de/products/demo"})
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid api key"}


def test_bootstrap_rejects_invalid_target_url(client):
    response = client.post(
        "/openapi/link-check/bootstrap",
        headers={"X-API-Key": "demo-key"},
        json={"target_url": "ftp://example.com/de/products/demo"},
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid target_url"}


def test_bootstrap_returns_409_when_language_not_detected(client, monkeypatch):
    _stub_enabled_languages(monkeypatch)
    monkeypatch.setattr("web.routes.openapi_materials.detect_target_language_from_url", lambda url, enabled: "")

    response = client.post(
        "/openapi/link-check/bootstrap",
        headers={"X-API-Key": "demo-key"},
        json={"target_url": "https://example.com/de/products/demo"},
    )

    assert response.status_code == 409
    assert response.get_json() == {"error": "language not detected"}


def test_bootstrap_returns_404_when_product_not_found(client, monkeypatch):
    _stub_enabled_languages(monkeypatch)
    monkeypatch.setattr("web.routes.openapi_materials.detect_target_language_from_url", lambda url, enabled: "de")
    monkeypatch.setattr("web.routes.openapi_materials.medias.find_product_for_link_check_url", lambda url, lang: None)

    response = client.post(
        "/openapi/link-check/bootstrap",
        headers={"X-API-Key": "demo-key"},
        json={"target_url": "https://example.com/de/products/demo"},
    )

    assert response.status_code == 404
    assert response.get_json() == {"error": "product not found"}


def test_bootstrap_returns_409_when_references_not_ready(client, monkeypatch):
    _stub_enabled_languages(monkeypatch)
    monkeypatch.setattr("web.routes.openapi_materials.detect_target_language_from_url", lambda url, enabled: "de")
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.find_product_for_link_check_url",
        lambda url, lang: {"id": 7, "product_code": "demo", "name": "Demo", "_matched_by": "product_code"},
    )
    monkeypatch.setattr("web.routes.openapi_materials.medias.list_reference_images_for_lang", lambda pid, lang: [])

    response = client.post(
        "/openapi/link-check/bootstrap",
        headers={"X-API-Key": "demo-key"},
        json={"target_url": "https://example.com/de/products/demo"},
    )

    assert response.status_code == 409
    assert response.get_json() == {"error": "references not ready"}


def test_bootstrap_returns_product_reference_payload(client, monkeypatch):
    _stub_enabled_languages(monkeypatch)
    monkeypatch.setattr("web.routes.openapi_materials.detect_target_language_from_url", lambda url, enabled: "de")
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.find_product_for_link_check_url",
        lambda url, lang: {"id": 7, "product_code": "demo", "name": "Demo", "_matched_by": "localized_links_exact"},
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_reference_images_for_lang",
        lambda pid, lang: [
            {"id": "cover-de", "kind": "cover", "filename": "cover_de.jpg", "object_key": "covers/de.jpg"},
            {"id": "detail-11", "kind": "detail", "filename": "detail_11.jpg", "object_key": "details/de_11.jpg"},
        ],
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.tos_clients.generate_signed_media_download_url",
        lambda object_key: f"https://signed.example.com/{object_key}",
    )
    monkeypatch.setattr("web.routes.openapi_materials.medias.get_language_name", lambda code: "DE")

    response = client.post(
        "/openapi/link-check/bootstrap",
        headers={"X-API-Key": "demo-key"},
        json={"target_url": "https://example.com/de/products/demo?variant=1"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["product"] == {"id": 7, "product_code": "demo", "name": "Demo"}
    assert payload["target_language"] == "de"
    assert payload["target_language_name"] == "DE"
    assert payload["matched_by"] == "localized_links_exact"
    assert payload["normalized_url"] == "https://example.com/de/products/demo?variant=1"
    assert [item["kind"] for item in payload["reference_images"]] == ["cover", "detail"]
    assert payload["reference_images"][0]["download_url"] == "https://signed.example.com/covers/de.jpg"
    assert payload["reference_images"][0]["expires_in"] == 3600


def test_bootstrap_treats_plain_products_path_as_english(client, monkeypatch):
    _stub_enabled_languages(monkeypatch)
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.find_product_for_link_check_url",
        lambda url, lang: {"id": 11, "product_code": "demo-en", "name": "Demo EN", "_matched_by": "localized_links_exact"},
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_reference_images_for_lang",
        lambda pid, lang: [
            {"id": "cover-en", "kind": "cover", "filename": "cover_en.jpg", "object_key": "covers/en.jpg"},
        ],
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.tos_clients.generate_signed_media_download_url",
        lambda object_key: f"https://signed.example.com/{object_key}",
    )
    monkeypatch.setattr("web.routes.openapi_materials.medias.get_language_name", lambda code: "EN")

    response = client.post(
        "/openapi/link-check/bootstrap",
        headers={"X-API-Key": "demo-key"},
        json={"target_url": "https://example.com/products/demo-en?variant=2"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["target_language"] == "en"
    assert payload["target_language_name"] == "EN"
    assert payload["product"] == {"id": 11, "product_code": "demo-en", "name": "Demo EN"}
