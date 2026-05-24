"""Tests for AI listing routes."""
from __future__ import annotations

import json


def test_ai_listing_index_page_requires_auth(authed_client_no_db):
    """未登录访问应重定向."""
    client = authed_client_no_db.application.test_client()
    resp = client.get("/ai-listing/")
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_ai_listing_index_page_renders_successfully(authed_client_no_db, monkeypatch):
    """已登录并授权的用户访问应返回200."""
    monkeypatch.setattr("appcore.db.query", lambda *args: [
        {"id": 1, "product_code": "AL_TEST1", "source_type": "manual_input", 
         "source_link": "http://x.com", "transit_link": "http://y.com", 
         "target_store_domain": "my.shop.com", "status": "completed", 
         "pricing_ratio": 1.5, "pricing_offset": 0.0, "error_message": None, 
         "created_at": "2026-05-24"}
    ])
    resp = authed_client_no_db.get("/ai-listing/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "AI 自动上品" in html
    assert "AL_TEST1" in html


def test_ai_listing_create_task(authed_client_no_db, monkeypatch):
    """测试创建上品任务并触发后台解析."""
    launched = []
    monkeypatch.setattr("web.routes.ai_listing.start_background_task", lambda fn, *args: launched.append(args))
    monkeypatch.setattr("appcore.db.execute", lambda *args: 99)

    resp = authed_client_no_db.post("/ai-listing/create", data={
        "source_link": "https://competitor.com/blog/1",
        "target_store_domain": "premium-deals.myshopify.com",
        "pricing_ratio": "1.8",
        "pricing_offset": "1.50"
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["task_id"] == 99
    assert data["product_code"].startswith("AL_")
    assert len(launched) == 1
    assert launched[0][0] == 99 # task_id passed to background task


def test_ai_listing_create_task_validation(authed_client_no_db):
    """测试输入校验."""
    resp = authed_client_no_db.post("/ai-listing/create", data={
        "source_link": "",
        "target_store_domain": ""
    })
    assert resp.status_code == 400
    assert "请填写源链接" in resp.get_json()["error"]


def test_ai_listing_task_detail(authed_client_no_db, monkeypatch):
    """测试上品工作台页面渲染."""
    dummy_task = {
        "id": 12, "product_code": "AL_XYZ", "source_type": "manual_input", 
        "source_link": "http://x.com", "transit_link": "http://y.com", 
        "target_store_domain": "my.shop.com", "status": "completed", 
        "pricing_ratio": 1.5, "pricing_offset": 0.0, "error_message": None, 
        "created_at": "2026-05-24", "generated_title": "Cool Gadget",
        "generated_html_desc": "<p>Super cool gadget!</p>",
        "generated_skus_json": json.dumps([{"sku": "SKU-RED", "price": 29.99}])
    }
    dummy_assets = [
        {"id": 1, "task_id": 12, "asset_type": "carousel", "original_url": "http://img1", 
         "transformed_url": "uploads/ai_listing/12/c1.png", "ai_classification": "showcase", 
         "is_selected": 1, "sort_order": 0}
    ]
    monkeypatch.setattr("appcore.db.query_one", lambda *args: dummy_task)
    monkeypatch.setattr("appcore.db.query", lambda *args: dummy_assets)

    resp = authed_client_no_db.get("/ai-listing/task/12")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "工作台 (AL_XYZ)" in html
    assert "Cool Gadget" in html
    assert "uploads/ai_listing/12/c1.png" in html


def test_ai_listing_task_edit(authed_client_no_db, monkeypatch):
    """测试保存文案与规格定价."""
    dummy_task = {"id": 12}
    monkeypatch.setattr("appcore.db.query_one", lambda *args: dummy_task)
    
    saved_sql = []
    def fake_execute(sql, args):
        saved_sql.append((sql, args))
        return 1
    monkeypatch.setattr("appcore.db.execute", fake_execute)

    resp = authed_client_no_db.post("/ai-listing/task/12/edit", json={
        "title": "Restructured Title",
        "html_description": "<h3>Features</h3>",
        "pricing_ratio": 1.4,
        "pricing_offset": 0.5,
        "skus": [{"sku": "SKU-BLUE", "price": 19.99}]
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert len(saved_sql) == 1
    assert "UPDATE ai_listing_tasks" in saved_sql[0][0]
    assert saved_sql[0][1][0] == "Restructured Title"


def test_ai_listing_asset_toggle(authed_client_no_db, monkeypatch):
    """测试勾选/剔除图片插图."""
    dummy_asset = {"id": 5, "is_selected": 1}
    monkeypatch.setattr("appcore.db.query_one", lambda *args: dummy_asset)
    
    executed = []
    monkeypatch.setattr("appcore.db.execute", lambda sql, args: executed.append(args))

    resp = authed_client_no_db.post("/ai-listing/task/12/asset/5/toggle")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["is_selected"] == 0 # Toggled from 1 to 0
    assert executed[0] == (0, 5)


def test_ai_listing_asset_reorder(authed_client_no_db, monkeypatch):
    """测试轮播图拖拽重排."""
    executed = []
    monkeypatch.setattr("appcore.db.execute", lambda sql, args: executed.append(args))

    resp = authed_client_no_db.post("/ai-listing/task/12/asset/reorder", json={
        "asset_ids": [4, 7, 2]
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert len(executed) == 3
    # Check sort order indices assigned sequentially
    assert executed[0] == (0, 4, 12)
    assert executed[1] == (1, 7, 12)
    assert executed[2] == (2, 2, 12)


def test_ai_listing_rerun(authed_client_no_db, monkeypatch):
    """测试重新跑上品任务."""
    dummy_task = {"id": 12}
    monkeypatch.setattr("appcore.db.query_one", lambda *args: dummy_task)
    
    launched = []
    monkeypatch.setattr("web.routes.ai_listing.start_background_task", lambda fn, *args: launched.append(args))
    
    resp = authed_client_no_db.post("/ai-listing/task/12/rerun")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert len(launched) == 1
    assert launched[0][0] == 12


