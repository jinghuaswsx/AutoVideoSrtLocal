"""Task 16: /settings 路由增补 Bindings Tab。"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _neutralize_db(monkeypatch):
    """无本地 DB 环境：所有 appcore.db 出口返回空，不真正连 MySQL。"""
    monkeypatch.setattr("appcore.db.query", lambda *a, **k: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *a, **k: None)
    monkeypatch.setattr("appcore.db.execute", lambda *a, **k: 0)
    # _get_pool 被任何地方触发都返回 MagicMock，避免初始化连接
    monkeypatch.setattr("appcore.db._get_pool", lambda: MagicMock())


@pytest.fixture
def admin_no_db_client(monkeypatch):
    """Admin Flask client with app-startup DB touches neutralized."""
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    from web.app import create_app

    fake_user = {"id": 1, "username": "test-admin", "role": "admin", "is_active": 1}
    monkeypatch.setattr(
        "web.auth.get_by_id",
        lambda user_id: fake_user if int(user_id) == 1 else None,
    )

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True
    return client


def test_settings_get_renders_tabs_and_bindings(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings.llm_bindings.list_all",
               return_value=[{
                   "code": "video_score.run", "module": "video_analysis",
                   "label": "视频评分", "description": "...",
                   "provider": "gemini_aistudio", "model": "gemini-3.1-pro-preview",
                   "extra": {}, "enabled": True, "is_custom": False,
                   "updated_at": None, "updated_by": None,
               }]), \
         patch("web.routes.settings.get_image_translate_channel",
               return_value="aistudio"):
        resp = admin_no_db_client.get("/settings")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # 不硬断言具体文案（模板演进后可能变），只确认 binding 数据被渲染
    assert "video_score.run" in body or "视频评分" in body


def test_settings_post_bindings_tab_calls_upsert(admin_no_db_client):
    with patch("web.routes.settings.llm_bindings.upsert") as m_upsert, \
         patch("web.routes.settings.llm_bindings.delete"):
        resp = admin_no_db_client.post("/settings", data={
            "tab": "bindings",
            "binding_video_score.run_provider": "gemini_aistudio",
            "binding_video_score.run_model": "gemini-3.1-pro-preview",
        })
    assert resp.status_code in (302, 303)
    m_upsert.assert_any_call(
        "video_score.run",
        provider="gemini_aistudio",
        model="gemini-3.1-pro-preview",
        updated_by=1,
    )


def test_settings_post_bindings_restore_default_calls_delete(admin_no_db_client):
    with patch("web.routes.settings.llm_bindings.upsert") as m_upsert, \
         patch("web.routes.settings.llm_bindings.delete") as m_delete:
        resp = admin_no_db_client.post("/settings", data={
            "tab": "bindings",
            "restore_default": "video_score.run",
        })
    assert resp.status_code in (302, 303)
    m_delete.assert_called_once_with("video_score.run")
    m_upsert.assert_not_called()


def test_settings_post_bindings_rejects_unknown_provider(admin_no_db_client):
    with patch("web.routes.settings.llm_bindings.upsert") as m_upsert:
        resp = admin_no_db_client.post("/settings", data={
            "tab": "bindings",
            "binding_video_score.run_provider": "hacker_provider",
            "binding_video_score.run_model": "x",
        })
    assert resp.status_code in (302, 303)
    m_upsert.assert_not_called()


def test_settings_post_bindings_ignores_incomplete_rows(admin_no_db_client):
    with patch("web.routes.settings.llm_bindings.upsert") as m_upsert:
        admin_no_db_client.post("/settings", data={
            "tab": "bindings",
            "binding_video_score.run_provider": "gemini_aistudio",
            # 故意不传 model
        })
    m_upsert.assert_not_called()


def test_settings_post_without_tab_still_handles_providers(admin_no_db_client):
    """向后兼容：老表单不带 tab，当作 providers Tab 处理。"""
    with patch("web.routes.settings.set_key") as m_set_key, \
         patch("web.routes.settings.set_image_translate_channel"):
        resp = admin_no_db_client.post("/settings", data={
            "openrouter_key": "new-key",
            "translate_pref": "vertex_gemini_31_flash_lite",
            "jianying_project_root": "/custom/path",
        })
    assert resp.status_code in (302, 303)
    # openrouter_key 应被保存
    assert any(call.args[1] == "openrouter" for call in m_set_key.call_args_list)
