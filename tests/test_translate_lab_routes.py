"""视频翻译（测试）模块骨架路由的渲染冒烟测试。

列表页与详情页直接依赖 ``appcore.db.query``/``appcore.db.query_one``，
这里用 monkeypatch 替换掉 ``web.routes.translate_lab`` 中的同名引用，
避免触达真实数据库。
"""
from __future__ import annotations

import json

from web import store


def test_translate_lab_list_page_renders(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.translate_lab.db_query",
        lambda sql, args: [],
    )

    resp = authed_client_no_db.get("/translate-lab")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "视频翻译（测试）" in body


def test_translate_lab_detail_page_renders(authed_client_no_db, monkeypatch):
    task = store.create_translate_lab(
        "lab-1",
        "uploads/lab-1.mp4",
        "output/lab-1",
        original_filename="demo.mp4",
        user_id=1,
    )
    row = {
        "id": task["id"],
        "user_id": 1,
        "type": "translate_lab",
        "display_name": "demo",
        "original_filename": "demo.mp4",
        "status": "uploaded",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "state_json": json.dumps(task, ensure_ascii=False),
    }
    monkeypatch.setattr(
        "web.routes.translate_lab.db_query_one",
        lambda sql, args: row,
    )

    resp = authed_client_no_db.get(f"/translate-lab/{task['id']}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "视频翻译（测试）" in body


def test_layout_contains_translate_lab_link(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.translate_lab.db_query",
        lambda sql, args: [],
    )

    resp = authed_client_no_db.get("/translate-lab")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "/translate-lab" in body
