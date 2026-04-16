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


# ── Task 13：API 路由测试 ──────────────────────────────

def test_start_task_triggers_runner(authed_client_no_db, monkeypatch):
    """POST /api/translate-lab/<id>/start 写入 options 并调用 runner.start。"""
    started: dict = {}

    def fake_start(task_id, user_id=None, **kwargs):
        started["task_id"] = task_id
        started["user_id"] = user_id

    monkeypatch.setattr(
        "web.services.translate_lab_runner.start", fake_start,
    )
    monkeypatch.setattr(
        "web.routes.translate_lab._get_lab_task",
        lambda tid, uid: {
            "id": tid,
            "_user_id": uid,
            "type": "translate_lab",
            "status": "uploaded",
        },
    )
    monkeypatch.setattr(
        "appcore.task_state.update", lambda tid, **kw: None,
    )

    resp = authed_client_no_db.post(
        "/api/translate-lab/lab-1/start",
        json={
            "source_language": "zh",
            "target_language": "en",
            "voice_match_mode": "auto",
        },
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert started["task_id"] == "lab-1"
    assert started["user_id"] == 1


def test_confirm_voice_sets_chosen(authed_client_no_db, monkeypatch):
    """POST /confirm-voice 把选中音色写入 chosen_voice。"""
    updates: dict = {}

    def fake_update(task_id, **fields):
        updates["task_id"] = task_id
        updates.update(fields)

    monkeypatch.setattr(
        "web.routes.translate_lab._get_lab_task",
        lambda tid, uid: {
            "id": tid, "_user_id": uid, "type": "translate_lab",
        },
    )
    monkeypatch.setattr(
        "appcore.task_state.get",
        lambda tid: {"pending_voice_choice": [{"voice_id": "abc",
                                                "name": "Rachel"}]},
    )
    monkeypatch.setattr("appcore.task_state.update", fake_update)

    resp = authed_client_no_db.post(
        "/api/translate-lab/lab-1/confirm-voice",
        json={"voice_id": "abc"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["chosen"]["voice_id"] == "abc"
    assert updates["chosen_voice"]["voice_id"] == "abc"
    assert updates["status"] == "running"


def test_confirm_voice_requires_voice_id(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.translate_lab._get_lab_task",
        lambda tid, uid: {
            "id": tid, "_user_id": uid, "type": "translate_lab",
        },
    )
    resp = authed_client_no_db.post(
        "/api/translate-lab/lab-1/confirm-voice",
        json={},
    )
    assert resp.status_code == 400


def test_sync_voice_library(authed_client_no_db, monkeypatch):
    """POST /voice-library/sync 调用 sync_all_shared_voices 并回传条目数。"""

    def fake_sync(api_key):
        assert api_key == "k"
        return 42

    monkeypatch.setattr(
        "web.routes.translate_lab.sync_all_shared_voices", fake_sync,
    )
    monkeypatch.setattr(
        "web.routes.translate_lab.resolve_key",
        lambda uid, service, env: "k",
    )

    resp = authed_client_no_db.post(
        "/api/translate-lab/voice-library/sync",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["total"] == 42


def test_embed_voice_library(authed_client_no_db, monkeypatch):
    """POST /voice-library/embed 调用 embed_missing_voices。"""
    seen: dict = {}

    def fake_embed(cache_dir, limit=None):
        seen["cache_dir"] = cache_dir
        seen["limit"] = limit
        return 7

    monkeypatch.setattr(
        "web.routes.translate_lab.embed_missing_voices", fake_embed,
    )

    resp = authed_client_no_db.post(
        "/api/translate-lab/voice-library/embed",
        json={"limit": 10},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["count"] == 7
    assert seen["limit"] == 10
    assert "voice_embed_cache" in seen["cache_dir"]
