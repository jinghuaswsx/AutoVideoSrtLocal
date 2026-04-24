import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_admin_bulk_translate_sidebar_and_page(authed_client_no_db):
    resp = authed_client_no_db.get("/admin/bulk-translate/tasks")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "批量翻译任务管理" in html
    assert 'href="/admin/bulk-translate/tasks"' in html
    assert "data-admin-bulk-tasks" in html
    assert "admin_bulk_translate_tasks.js" in html
    assert "进行中" in html
    assert "卡住" in html


def test_admin_bulk_translate_tasks_api_returns_admin_overview(monkeypatch, authed_client_no_db):
    payload = {
        "stats": {"running": 1, "stuck": 1, "done": 1, "total": 3},
        "items": [{"id": "bt-stuck", "group": "stuck", "detail_url": "/tasks/bt-stuck"}],
    }
    monkeypatch.setattr(
        "web.routes.bulk_translate.list_admin_tasks",
        lambda limit=300: payload,
    )

    resp = authed_client_no_db.get("/api/bulk-translate/admin/list?limit=5")
    assert resp.status_code == 200
    assert resp.get_json() == payload


def test_admin_can_open_other_users_bulk_translate_detail(monkeypatch, authed_client_no_db):
    monkeypatch.setattr(
        "web.routes.bulk_translate.get_task",
        lambda task_id: {
            "id": task_id,
            "user_id": 99,
            "status": "running",
            "state": {"plan": []},
            "created_at": None,
            "updated_at": None,
        },
    )

    resp = authed_client_no_db.get("/api/bulk-translate/bt-other-user?scope=admin")
    assert resp.status_code == 200
    assert resp.get_json()["user_id"] == 99


def test_admin_bulk_translate_projection_sorts_intervention_first(monkeypatch):
    from appcore import bulk_translate_projection as mod

    rows = [
        {
            "id": "bt-done",
            "user_id": 3,
            "username": "done-user",
            "status": "done",
            "created_at": datetime(2026, 4, 23, 10, 0, 0),
            "state_json": json.dumps({
                "product_id": 6,
                "target_langs": ["it"],
                "content_types": ["copywriting"],
                "cost_tracking": {"actual": {"actual_cost_cny": 2.5}},
                "plan": [{"idx": 0, "kind": "copywriting", "lang": "it", "status": "done"}],
            }),
        },
        {
            "id": "bt-running",
            "user_id": 2,
            "username": "runner",
            "status": "running",
            "created_at": datetime(2026, 4, 23, 11, 0, 0),
            "state_json": json.dumps({
                "product_id": 7,
                "target_langs": ["de"],
                "content_types": ["videos"],
                "plan": [{"idx": 0, "kind": "videos", "lang": "de", "status": "running"}],
            }),
        },
        {
            "id": "bt-stuck",
            "user_id": 1,
            "username": "admin",
            "status": "running",
            "created_at": datetime(2026, 4, 23, 12, 0, 0),
            "state_json": json.dumps({
                "product_id": 8,
                "target_langs": ["fr"],
                "content_types": ["detail_images"],
                "plan": [{"idx": 0, "kind": "detail_images", "lang": "fr", "status": "failed"}],
            }),
        },
    ]

    monkeypatch.setattr(mod, "query", lambda *args, **kwargs: rows)
    monkeypatch.setattr(
        mod.medias,
        "get_product",
        lambda product_id: {"id": product_id, "name": f"商品 {product_id}", "product_code": f"P{product_id}"},
    )
    monkeypatch.setattr(
        mod.medias,
        "get_language_name",
        lambda code: {"it": "意大利语", "de": "德语", "fr": "法语"}.get(code, code),
    )

    overview = mod.list_admin_tasks(limit=20)

    assert overview["stats"] == {"running": 1, "stuck": 1, "done": 1, "total": 3}
    assert [item["id"] for item in overview["items"]] == ["bt-stuck", "bt-running", "bt-done"]
    assert overview["items"][0]["group"] == "stuck"
    assert overview["items"][0]["detail_url"] == "/tasks/bt-stuck?scope=admin"
    assert overview["items"][0]["intervention_count"] == 1
    assert overview["items"][2]["cost_actual"] == 2.5


def test_admin_bulk_translate_projection_prefers_chinese_creator_name(monkeypatch):
    from appcore import bulk_translate_projection as mod

    rows = [
        {
            "id": "bt-zqq",
            "user_id": 8,
            "username": "zqq",
            "creator_name": "张青青",
            "status": "running",
            "created_at": datetime(2026, 4, 24, 12, 0, 0),
            "state_json": json.dumps({
                "product_id": 9,
                "target_langs": ["fr"],
                "content_types": ["copywriting"],
                "plan": [{"idx": 0, "kind": "copywriting", "lang": "fr", "status": "running"}],
            }, ensure_ascii=False),
        },
    ]

    monkeypatch.setattr(mod, "query", lambda *args, **kwargs: rows)
    monkeypatch.setattr(
        mod.medias,
        "_media_product_owner_name_expr",
        lambda: "COALESCE(NULLIF(TRIM(u.xingming), ''), u.username)",
    )
    monkeypatch.setattr(
        mod.medias,
        "get_product",
        lambda product_id: {"id": product_id, "name": f"商品 {product_id}", "product_code": f"P{product_id}"},
    )
    monkeypatch.setattr(mod.medias, "get_language_name", lambda code: {"fr": "法语"}.get(code, code))

    overview = mod.list_admin_tasks(limit=20)

    assert overview["items"][0]["creator"]["name"] == "张青青"
