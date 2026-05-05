"""``/openapi/materials/<product_code>`` 路由测试。"""
from __future__ import annotations

import importlib

import pytest

from web.app import create_app


@pytest.fixture
def client(monkeypatch):
    """保证每个用例以一致的 apikey 启动应用。"""
    monkeypatch.setenv("LOCAL_SERVER_BASE_URL", "http://local.test")
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)

    class FakeProviderConfig:
        api_key = "demo-key"

    monkeypatch.setattr(
        "web.routes.openapi_materials.get_provider_config",
        lambda provider_code: FakeProviderConfig()
        if provider_code == "openapi_materials"
        else None,
    )
    import config as _config
    importlib.reload(_config)
    # create_app 内会读取 config 常量，reload 后下一次 create_app 生效
    app = create_app()
    return app.test_client()


def test_rejects_missing_api_key(client):
    response = client.get("/openapi/materials/sonic-lens-refresher")
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid api key"}


def test_list_rejects_missing_api_key(client):
    response = client.get("/openapi/materials")
    assert response.status_code in (301, 308, 401)
    # 跟随重定向后仍应被拦截
    follow = client.get("/openapi/materials", follow_redirects=True)
    assert follow.status_code == 401


def test_list_returns_paginated_products(client, monkeypatch):
    def fake_query(sql, args=None):
        sql = " ".join(sql.split())
        if sql.startswith("SELECT COUNT(*) AS c FROM media_products"):
            return [{"c": 3}]
        if sql.startswith("SELECT id, product_code, name, archived"):
            return [
                {
                    "id": 1, "product_code": "alpha", "name": "Alpha",
                    "archived": 0, "ad_supported_langs": "en,de",
                    "created_at": None, "updated_at": None,
                },
                {
                    "id": 2, "product_code": "beta", "name": "Beta",
                    "archived": 0, "ad_supported_langs": "",
                    "created_at": None, "updated_at": None,
                },
            ]
        if sql.startswith("SELECT product_id, lang, object_key FROM media_product_covers"):
            return [
                {"product_id": 1, "lang": "en", "object_key": "k1"},
                {"product_id": 1, "lang": "de", "object_key": "k2"},
            ]
        if sql.startswith("SELECT DISTINCT product_id, lang FROM media_copywritings"):
            return [
                {"product_id": 1, "lang": "en"},
                {"product_id": 2, "lang": "en"},
            ]
        if sql.startswith("SELECT product_id, lang, COUNT(*) AS c FROM media_items"):
            return [
                {"product_id": 1, "lang": "en", "c": 3},
                {"product_id": 1, "lang": "de", "c": 1},
                {"product_id": 2, "lang": "en", "c": 2},
            ]
        return []

    monkeypatch.setattr("web.routes.openapi_materials.query", fake_query)

    response = client.get(
        "/openapi/materials?page=1&page_size=10",
        headers={"X-API-Key": "demo-key"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 10
    assert len(body["items"]) == 2

    alpha = body["items"][0]
    assert alpha["product_code"] == "alpha"
    assert alpha["cover_langs"] == ["de", "en"]
    assert alpha["copywriting_langs"] == ["en"]
    assert alpha["item_langs"] == {"en": 3, "de": 1}
    assert alpha["total_items"] == 4
    assert alpha["ad_supported_langs"] == "en,de"

    beta = body["items"][1]
    assert beta["cover_langs"] == []
    assert beta["total_items"] == 2


def test_list_clamps_page_size(client, monkeypatch):
    called = {}

    def fake_query(sql, args=None):
        sql = " ".join(sql.split())
        if sql.startswith("SELECT COUNT"):
            return [{"c": 0}]
        if sql.startswith("SELECT id, product_code"):
            called["limit"] = args[-2]
            called["offset"] = args[-1]
            return []
        return []

    monkeypatch.setattr("web.routes.openapi_materials.query", fake_query)
    response = client.get(
        "/openapi/materials?page=2&page_size=999",
        headers={"X-API-Key": "demo-key"},
    )
    assert response.status_code == 200
    assert called["limit"] == 100   # 超过上限被 clamp
    assert called["offset"] == 100  # page=2 * page_size=100


def test_list_materials_route_delegates_response_building(client, monkeypatch):
    captured: dict = {}

    def fake_build_materials_list_response(**kwargs):
        captured.update(kwargs)
        return {
            "items": [{"id": 1, "product_code": "alpha"}],
            "total": 1,
            "page": 3,
            "page_size": 50,
        }

    monkeypatch.setattr(
        "web.routes.openapi_materials._build_materials_list_response",
        fake_build_materials_list_response,
    )

    response = client.get(
        "/openapi/materials?page=3&page_size=50&q=Alpha&archived=all",
        headers={"X-API-Key": "demo-key"},
    )

    assert response.status_code == 200
    assert response.get_json()["items"] == [{"id": 1, "product_code": "alpha"}]
    assert captured["page_raw"] == "3"
    assert captured["page_size_raw"] == "50"
    assert captured["q"] == "Alpha"
    assert captured["archived_raw"] == "all"


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


def test_get_material_route_delegates_response_building(client, monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_product_by_code",
        lambda code: {"id": 123, "product_code": code, "name": "Demo"},
    )

    def fake_build_material_detail_response(product):
        captured["product"] = product
        return {"product": {"product_code": product["product_code"]}, "items": []}

    monkeypatch.setattr(
        "web.routes.openapi_materials._build_material_detail_response",
        fake_build_material_detail_response,
    )

    response = client.get(
        "/openapi/materials/Demo-RJC",
        headers={"X-API-Key": "demo-key"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"product": {"product_code": "demo-rjc"}, "items": []}
    assert captured["product"]["id"] == 123


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
        "http://local.test/medias/obj/1/medias/123/cover_en.jpg"
    )
    assert payload["covers"]["de"]["download_url"] == (
        "http://local.test/medias/obj/1/medias/123/cover_de.jpg"
    )

    assert payload["copywritings"]["en"][0]["title"] == "Title"
    assert payload["copywritings"]["de"][0]["title"] == "Titel"

    assert payload["items"][0]["video_download_url"] == (
        "http://local.test/medias/obj/1/medias/123/demo.mp4"
    )
    assert payload["items"][0]["video_cover_download_url"] == (
        "http://local.test/medias/obj/1/medias/123/item_cover.jpg"
    )
    assert payload["items"][1]["video_cover_download_url"] is None

    assert payload["storage_backend"] == "local"


def test_push_payload_route_delegates_payload_building(client, monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_product_by_code",
        lambda code: {"id": 123, "product_code": code, "name": "Alpha"},
    )

    def fake_build_material_push_payload(product, *, lang, product_code):
        captured.update({
            "product": product,
            "lang": lang,
            "product_code": product_code,
        })
        return {"mode": "create", "product_name": product["name"], "videos": []}

    monkeypatch.setattr(
        "web.routes.openapi_materials._build_material_push_payload",
        fake_build_material_push_payload,
    )

    response = client.get(
        "/openapi/materials/Alpha-RJC/push-payload?lang=DE",
        headers={"X-API-Key": "demo-key"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"mode": "create", "product_name": "Alpha", "videos": []}
    assert captured["product"]["id"] == 123
    assert captured["lang"] == "de"
    assert captured["product_code"] == "alpha-rjc"


def test_shopify_localizer_bootstrap_accepts_shopify_id_override(client, monkeypatch):
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.is_valid_language",
        lambda lang: lang == "it",
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_product_by_code",
        lambda code: {"id": 123, "product_code": code, "name": "Demo Product"},
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.resolve_shopify_product_id",
        lambda product_id: "",
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_reference_images_for_lang",
        lambda product_id, lang: [
            {"id": 1, "kind": "detail", "filename": f"{lang}.jpg", "object_key": f"{lang}.jpg"}
        ],
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_language_name",
        lambda lang: "意大利语",
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials._media_download_url",
        lambda object_key: f"http://local.test/{object_key}",
    )

    response = client.post(
        "/openapi/medias/shopify-image-localizer/bootstrap",
        headers={"X-API-Key": "demo-key"},
        json={
            "product_code": "sonic-lens-refresher-rjc",
            "lang": "it",
            "shopify_product_id": "8559391932589",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["product"]["shopify_product_id"] == "8559391932589"
    assert payload["reference_images"][0]["url"] == "http://local.test/en.jpg"
    assert payload["localized_images"][0]["url"] == "http://local.test/it.jpg"


def test_shopify_localizer_bootstrap_delegates_response_building(client, monkeypatch):
    captured: dict = {}

    def fake_build_shopify_localizer_bootstrap_response(body, **kwargs):
        captured["body"] = body
        captured["kwargs"] = kwargs
        return {
            "product": {
                "id": 123,
                "product_code": "sonic-lens-refresher-rjc",
                "shopify_product_id": body["shopify_product_id"],
                "name": "Demo Product",
            },
            "language": {"code": "it"},
            "reference_images": [{"url": "http://local.test/en.jpg"}],
            "localized_images": [{"url": "http://local.test/it.jpg"}],
        }

    monkeypatch.setattr(
        "web.routes.openapi_materials._build_shopify_localizer_bootstrap_response",
        fake_build_shopify_localizer_bootstrap_response,
    )

    response = client.post(
        "/openapi/medias/shopify-image-localizer/bootstrap",
        headers={"X-API-Key": "demo-key"},
        json={
            "product_code": "sonic-lens-refresher-rjc",
            "lang": "it",
            "shopify_product_id": "8559391932589",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["product"]["shopify_product_id"] == "8559391932589"
    assert captured["body"]["lang"] == "it"
    assert "list_reference_images_for_lang_fn" in captured["kwargs"]


def test_shopify_localizer_languages_include_shopify_language_name(client, monkeypatch):
    monkeypatch.setattr("web.routes.openapi_materials._api_key_valid", lambda: True)
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_shopify_localizer_languages",
        lambda: [
            {
                "code": "nl",
                "name_zh": "Dutch",
                "shop_locale": "nl",
                "folder_code": "nl",
                "label": "Dutch (NL/nl)",
                "shopify_language_name": "Dutch",
            }
        ],
    )

    response = client.get(
        "/openapi/medias/shopify-image-localizer/languages",
        headers={"X-API-Key": "demo-key"},
    )

    assert response.status_code == 200
    assert response.get_json()["items"][0]["shopify_language_name"] == "Dutch"


# ================================================================
# /openapi/push-items 路由测试
# ================================================================


def test_push_items_list_rejects_missing_api_key(client):
    response = client.get("/openapi/push-items", follow_redirects=True)
    assert response.status_code == 401


def test_push_items_list_returns_items_with_status(client, monkeypatch):
    """列表返回 item × lang 级的扁平数据 + 状态。"""
    rows = [
        {
            "id": 456, "product_id": 123, "lang": "de",
            "filename": "demo.mp4", "display_name": "demo.mp4",
            "object_key": "k/demo.mp4", "cover_object_key": None,
            "duration_seconds": 12.3, "file_size": 1234,
            "pushed_at": None, "latest_push_id": None,
            "created_at": None,
            "product_name": "Alpha", "product_code": "alpha",
            "ad_supported_langs": "en,de",
            "selling_points": "", "importance": 3,
        },
    ]
    monkeypatch.setattr(
        "web.routes.openapi_materials.pushes.list_items_for_push",
        lambda **kw: (rows, 1),
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.pushes.compute_readiness",
        lambda item, product: {
            "has_object": True, "has_cover": True,
            "has_copywriting": True, "lang_supported": True,
        },
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.pushes.compute_status",
        lambda item, product: "pending",
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.query_one",
        lambda sql, args: None,
    )

    response = client.get(
        "/openapi/push-items?page=1&page_size=10",
        headers={"X-API-Key": "demo-key"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    row = body["items"][0]
    assert row["item_id"] == 456
    assert row["product_code"] == "alpha"
    assert row["lang"] == "de"
    assert row["status"] == "pending"
    assert row["readiness"]["has_object"] is True


def test_push_items_list_filters_by_status(client, monkeypatch):
    rows = [
        {
            "id": 1, "product_id": 10, "lang": "en",
            "filename": "f1.mp4", "display_name": "f1",
            "object_key": "k1", "cover_object_key": None,
            "duration_seconds": 1.0, "file_size": 100,
            "pushed_at": None, "latest_push_id": None, "created_at": None,
            "product_name": "P", "product_code": "p",
            "ad_supported_langs": "en", "selling_points": "", "importance": 3,
        },
        {
            "id": 2, "product_id": 10, "lang": "en",
            "filename": "f2.mp4", "display_name": "f2",
            "object_key": "k2", "cover_object_key": None,
            "duration_seconds": 1.0, "file_size": 100,
            "pushed_at": None, "latest_push_id": None, "created_at": None,
            "product_name": "P", "product_code": "p",
            "ad_supported_langs": "en", "selling_points": "", "importance": 3,
        },
    ]
    monkeypatch.setattr(
        "web.routes.openapi_materials.pushes.list_items_for_push",
        lambda **kw: (rows, 2),
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.pushes.compute_readiness",
        lambda item, product: {
            "has_object": True, "has_cover": True,
            "has_copywriting": True, "lang_supported": True,
        },
    )
    # 第一条 pending、第二条 pushed
    statuses = iter(["pending", "pushed"])
    monkeypatch.setattr(
        "web.routes.openapi_materials.pushes.compute_status",
        lambda item, product: next(statuses),
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.query_one",
        lambda sql, args: None,
    )

    response = client.get(
        "/openapi/push-items?status=pushed",
        headers={"X-API-Key": "demo-key"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert len(body["items"]) == 1
    assert body["items"][0]["item_id"] == 2


def test_mark_pushed_returns_ok(client, monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_item",
        lambda iid: {"id": iid},
    )

    def fake_build_mark_pushed_response(item_id, body, *, operator_user_id):
        captured.update({
            "item_id": item_id,
            "body": body,
            "operator_user_id": operator_user_id,
        })
        return {"ok": True, "log_id": 42}

    monkeypatch.setattr(
        "web.routes.openapi_materials._build_mark_pushed_response",
        fake_build_mark_pushed_response,
    )

    response = client.post(
        "/openapi/push-items/456/mark-pushed",
        headers={"X-API-Key": "demo-key", "Content-Type": "application/json"},
        json={"request_payload": {"mode": "create"}, "response_body": "ok"},
    )
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "log_id": 42}
    assert captured == {
        "item_id": 456,
        "body": {"request_payload": {"mode": "create"}, "response_body": "ok"},
        "operator_user_id": 0,
    }


def test_mark_failed_returns_ok(client, monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_item",
        lambda iid: {"id": iid},
    )

    def fake_build_mark_failed_response(item_id, body, *, operator_user_id):
        captured.update({
            "item_id": item_id,
            "body": body,
            "operator_user_id": operator_user_id,
        })
        return {"ok": True, "log_id": 99}

    monkeypatch.setattr(
        "web.routes.openapi_materials._build_mark_failed_response",
        fake_build_mark_failed_response,
    )

    response = client.post(
        "/openapi/push-items/456/mark-failed",
        headers={"X-API-Key": "demo-key", "Content-Type": "application/json"},
        json={
            "request_payload": {"mode": "create"},
            "response_body": "oops",
            "error_message": "HTTP 500",
        },
    )
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "log_id": 99}
    assert captured == {
        "item_id": 456,
        "body": {
            "request_payload": {"mode": "create"},
            "response_body": "oops",
            "error_message": "HTTP 500",
        },
        "operator_user_id": 0,
    }


def test_mark_pushed_item_not_found(client, monkeypatch):
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_item",
        lambda iid: None,
    )
    response = client.post(
        "/openapi/push-items/999/mark-pushed",
        headers={"X-API-Key": "demo-key"},
        json={},
    )
    assert response.status_code == 404


def test_get_push_item_returns_single(client, monkeypatch):
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_item",
        lambda iid: {
            "id": iid, "product_id": 10, "lang": "de",
            "filename": "f.mp4", "display_name": "f.mp4",
            "object_key": "k", "cover_object_key": None,
            "duration_seconds": 1.0, "file_size": 100,
            "pushed_at": None, "latest_push_id": None, "created_at": None,
        },
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_product",
        lambda pid: {
            "id": pid, "name": "P", "product_code": "p",
            "ad_supported_langs": "de", "selling_points": "", "importance": 3,
        },
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.pushes.compute_readiness",
        lambda item, product: {
            "has_object": True, "has_cover": True,
            "has_copywriting": True, "lang_supported": True,
        },
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.pushes.compute_status",
        lambda item, product: "pending",
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.query_one",
        lambda sql, args: None,
    )

    response = client.get(
        "/openapi/push-items/77",
        headers={"X-API-Key": "demo-key"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["item_id"] == 77
    assert body["product_code"] == "p"
    assert body["status"] == "pending"


def test_push_item_by_keys_returns_mk_id_and_localized_text(client, monkeypatch):
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.find_item_by_keys",
        lambda product_id, lang, filename: {
            "id": 238,
            "product_id": product_id,
            "lang": lang,
            "filename": filename,
            "display_name": filename,
            "object_key": "k.mp4",
            "cover_object_key": "k.jpg",
            "duration_seconds": 1.0,
            "file_size": 100,
            "pushed_at": None,
            "latest_push_id": None,
            "created_at": None,
        },
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.get_product",
        lambda pid: {
            "id": pid,
            "name": "P",
            "product_code": "p",
            "mk_id": 3725,
            "ad_supported_langs": "fr",
            "selling_points": "",
            "importance": 3,
        },
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.pushes.build_item_payload",
        lambda item, product: {
            "mode": "create",
            "texts": [{"title": "x", "message": "y", "description": "z"}],
        },
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.pushes.resolve_localized_text_payload",
        lambda item: {
            "title": "fr1",
            "message": "fr2",
            "description": "fr3",
            "lang": "法语",
        },
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.pushes.build_localized_texts_request",
        lambda item: {
            "texts": [
                {
                    "title": "de1",
                    "message": "de2",
                    "description": "de3",
                    "lang": "德语",
                },
                {
                    "title": "fr1",
                    "message": "fr2",
                    "description": "fr3",
                    "lang": "法语",
                },
            ],
        },
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.pushes.compute_readiness",
        lambda item, product: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "lang_supported": True,
        },
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.pushes.compute_status",
        lambda item, product: "pending",
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.query_one",
        lambda sql, args: None,
    )

    response = client.get(
        "/openapi/push-items/by-keys?product_id=10&lang=fr&filename=demo.mp4",
        headers={"X-API-Key": "demo-key"},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["item_id"] == 238
    assert body["mk_id"] == 3725
    assert body["localized_text"] == {
        "title": "fr1",
        "message": "fr2",
        "description": "fr3",
        "lang": "法语",
    }
    assert body["localized_texts_request"] == {
        "texts": [
            {
                "title": "de1",
                "message": "de2",
                "description": "de3",
                "lang": "德语",
            },
            {
                "title": "fr1",
                "message": "fr2",
                "description": "fr3",
                "lang": "法语",
            },
        ]
    }
