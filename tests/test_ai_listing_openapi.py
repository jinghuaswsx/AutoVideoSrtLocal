"""Tests for AI listing OpenAPI routes."""
from __future__ import annotations

import json


def test_list_ai_listing_tasks_requires_auth(authed_client_no_db):
    """未授权的 X-API-Key 应返回 401."""
    client = authed_client_no_db.application.test_client()
    resp = client.get("/openapi/medias/shopify-image-localizer/ai-listing/tasks")
    assert resp.status_code == 401


def test_list_ai_listing_tasks_success(authed_client_no_db, monkeypatch):
    """验证获取未上架 AI 上品任务列表."""
    client = authed_client_no_db.application.test_client()
    
    # Mock API key 校验
    monkeypatch.setattr("web.routes.openapi_materials._api_key_valid", lambda *args, **kwargs: True)
    
    dummy_tasks = [
        {"id": 1, "product_code": "AL_TEST1", "generated_title": "Cool Gadget", "target_store_domain": "store1.com"}
    ]
    monkeypatch.setattr("appcore.db.query", lambda sql, *args: dummy_tasks)
    
    resp = client.get(
        "/openapi/medias/shopify-image-localizer/ai-listing/tasks",
        headers={"X-API-Key": "test-key"}
    )
    assert resp.status_code == 200
    res_data = resp.get_json()
    assert "tasks" in res_data
    assert len(res_data["tasks"]) == 1
    assert res_data["tasks"][0]["product_code"] == "AL_TEST1"


def test_get_ai_listing_task_detail_success(authed_client_no_db, monkeypatch):
    """验证获取单个任务详情."""
    client = authed_client_no_db.application.test_client()
    monkeypatch.setattr("web.routes.openapi_materials._api_key_valid", lambda *args, **kwargs: True)
    
    dummy_task = {
        "id": 12,
        "product_code": "AL_XYZ",
        "generated_title": "Super Gadget",
        "generated_html_desc": "<p>Cool</p>",
        "generated_skus_json": json.dumps([{"sku": "SKU1", "price": 10.99}])
    }
    dummy_assets = [
        {"id": 4, "asset_type": "carousel", "original_url": "http://img1", "transformed_url": "uploads/a.png", "ai_classification": "showcase", "is_selected": 1, "sort_order": 0}
    ]
    
    monkeypatch.setattr("appcore.db.query_one", lambda sql, *args: dummy_task)
    monkeypatch.setattr("appcore.db.query", lambda sql, *args: dummy_assets)
    
    resp = client.get(
        "/openapi/medias/shopify-image-localizer/ai-listing/tasks/12",
        headers={"X-API-Key": "test-key"}
    )
    assert resp.status_code == 200
    res_data = resp.get_json()
    assert "task" in res_data
    assert "assets" in res_data
    assert "skus" in res_data
    assert res_data["task"]["product_code"] == "AL_XYZ"
    assert len(res_data["assets"]) == 1
    assert res_data["assets"][0]["download_url"].endswith("/medias/obj/uploads/a.png")


def test_submit_ai_listing_success(authed_client_no_db, monkeypatch):
    """验证上架成功后状态回写."""
    client = authed_client_no_db.application.test_client()
    monkeypatch.setattr("web.routes.openapi_materials._api_key_valid", lambda *args, **kwargs: True)
    
    executed_updates = []
    
    monkeypatch.setattr("appcore.db.query_one", lambda sql, *args: {"id": 12})
    monkeypatch.setattr("appcore.db.execute", lambda sql, args: executed_updates.append(args))
    
    resp = client.post(
        "/openapi/medias/shopify-image-localizer/ai-listing/tasks/12/success",
        headers={"X-API-Key": "test-key"},
        json={"shopify_product_id": "999888777"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert len(executed_updates) == 1
    assert executed_updates[0][0] == "999888777"
