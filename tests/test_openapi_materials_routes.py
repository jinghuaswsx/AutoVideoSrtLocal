"""``/openapi/materials/<product_code>`` 路由测试。"""
from __future__ import annotations

import importlib

import pytest

from web.app import create_app


@pytest.fixture
def client(monkeypatch):
    """保证每个用例以一致的 apikey 启动应用。"""
    monkeypatch.setenv("OPENAPI_MEDIA_API_KEY", "demo-key")
    import config as _config
    importlib.reload(_config)
    # create_app 内会读取 config 常量，reload 后下一次 create_app 生效
    app = create_app()
    return app.test_client()


def test_rejects_missing_api_key(client):
    response = client.get("/openapi/materials/sonic-lens-refresher")
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid api key"}


def test_rejects_wrong_api_key(client):
    response = client.get(
        "/openapi/materials/sonic-lens-refresher",
        headers={"X-API-Key": "wrong-key"},
    )
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid api key"}


def test_returns_404_for_missing_product(client, monkeypatch):
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_product_by_code",
        lambda code: None,
    )
    response = client.get(
        "/openapi/materials/not-found",
        headers={"X-API-Key": "demo-key"},
    )
    assert response.status_code == 404
    assert response.get_json() == {"error": "product not found"}


def test_returns_product_assets(client, monkeypatch):
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_product_by_code",
        lambda code: {
            "id": 123,
            "name": "Sonic Lens Refresher",
            "product_code": code,
            "archived": 0,
            "created_at": None,
            "updated_at": None,
        },
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_product_covers",
        lambda pid: {
            "en": "1/medias/123/cover_en.jpg",
            "de": "1/medias/123/cover_de.jpg",
        },
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_copywritings",
        lambda pid: [
            {"lang": "en", "title": "Title", "body": "Body",
             "description": "Desc", "ad_carrier": None, "ad_copy": None,
             "ad_keywords": None},
            {"lang": "de", "title": "Titel", "body": "Text",
             "description": "Beschreibung", "ad_carrier": None,
             "ad_copy": None, "ad_keywords": None},
        ],
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_items",
        lambda pid: [
            {
                "id": 456,
                "lang": "en",
                "filename": "demo.mp4",
                "display_name": "demo.mp4",
                "object_key": "1/medias/123/demo.mp4",
                "cover_object_key": "1/medias/123/item_cover.jpg",
                "duration_seconds": 12.3,
                "file_size": 1234567,
                "created_at": None,
            },
            {
                "id": 457,
                "lang": "en",
                "filename": "demo-2.mp4",
                "display_name": "demo-2.mp4",
                "object_key": "1/medias/123/demo-2.mp4",
                "cover_object_key": None,
                "duration_seconds": 8.8,
                "file_size": 7654321,
                "created_at": None,
            },
        ],
    )

    signed_calls: list[str] = []

    def fake_signed_url(object_key, expires=None):
        signed_calls.append(object_key)
        return f"https://signed.example.com/{object_key}"

    monkeypatch.setattr(
        "web.routes.openapi_materials.tos_clients."
        "generate_signed_media_download_url",
        fake_signed_url,
    )

    response = client.get(
        "/openapi/materials/sonic-lens-refresher",
        headers={"X-API-Key": "demo-key"},
    )

    assert response.status_code == 200
    payload = response.get_json()

    assert payload["product"]["product_code"] == "sonic-lens-refresher"
    assert payload["product"]["name"] == "Sonic Lens Refresher"
    assert payload["product"]["archived"] is False

    assert payload["covers"]["en"]["object_key"] == "1/medias/123/cover_en.jpg"
    assert payload["covers"]["en"]["download_url"] == (
        "https://signed.example.com/1/medias/123/cover_en.jpg"
    )
    assert payload["covers"]["de"]["download_url"] == (
        "https://signed.example.com/1/medias/123/cover_de.jpg"
    )

    assert payload["copywritings"]["en"][0]["title"] == "Title"
    assert payload["copywritings"]["de"][0]["title"] == "Titel"

    assert payload["items"][0]["video_download_url"] == (
        "https://signed.example.com/1/medias/123/demo.mp4"
    )
    assert payload["items"][0]["video_cover_download_url"] == (
        "https://signed.example.com/1/medias/123/item_cover.jpg"
    )
    assert payload["items"][1]["video_cover_download_url"] is None

    assert signed_calls == [
        "1/medias/123/cover_en.jpg",
        "1/medias/123/cover_de.jpg",
        "1/medias/123/demo.mp4",
        "1/medias/123/item_cover.jpg",
        "1/medias/123/demo-2.mp4",
    ]
    assert payload["expires_in"] == 3600
