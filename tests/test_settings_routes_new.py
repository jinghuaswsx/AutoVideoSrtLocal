"""Task 16: /settings 路由增补 Bindings Tab。"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _neutralize_db(monkeypatch):
    """无本地 DB 环境：所有 appcore.db 出口返回空，不真正连 MySQL。"""
    monkeypatch.setattr("appcore.db.query", lambda *a, **k: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *a, **k: None)
    monkeypatch.setattr("appcore.db.execute", lambda *a, **k: 0)
    def fake_api_key_query_one(sql, params=()):
        if "FROM users WHERE username = %s" in sql and params and params[0] == "admin":
            return {"id": 1, "username": "admin", "is_active": 1}
        if "FROM users WHERE id = %s" in sql and params and int(params[0]) == 1:
            return {"id": 1, "username": "admin", "is_active": 1}
        return None
    monkeypatch.setattr("appcore.api_keys.query", lambda *a, **k: [])
    monkeypatch.setattr("appcore.api_keys.query_one", fake_api_key_query_one)
    monkeypatch.setattr("appcore.api_keys.execute", lambda *a, **k: 0)
    # _get_pool 被任何地方触发都返回 MagicMock，避免初始化连接
    monkeypatch.setattr("appcore.db._get_pool", lambda: MagicMock())


@pytest.fixture
def admin_no_db_client(monkeypatch):
    """Admin Flask client with app-startup DB touches neutralized."""
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    from web.app import create_app

    fake_user = {"id": 1, "username": "admin", "role": "admin", "is_active": 1}
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


@pytest.fixture
def non_owner_clients(monkeypatch):
    """Clients for users who are not username=admin."""
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    from web.app import create_app

    users = {
        2: {"id": 2, "username": "alice", "role": "user", "is_active": 1},
        3: {"id": 3, "username": "manager", "role": "admin", "is_active": 1},
    }
    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: users.get(int(user_id)))

    app = create_app()
    normal = app.test_client()
    with normal.session_transaction() as session:
        session["_user_id"] = "2"
        session["_fresh"] = True
    manager = app.test_client()
    with manager.session_transaction() as session:
        session["_user_id"] = "3"
        session["_fresh"] = True
    return manager, normal


def test_settings_requires_exact_admin_username(non_owner_clients):
    manager_no_db_client, normal_no_db_client = non_owner_clients
    assert manager_no_db_client.get("/settings").status_code == 403
    assert normal_no_db_client.get("/settings").status_code == 403
    assert manager_no_db_client.get("/admin/settings/ai-pricing/list").status_code == 403


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


def test_settings_get_renders_gpt_5_mini_translate_option(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="aistudio"):
        resp = admin_no_db_client.get("/settings?tab=providers")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'value="gpt_5_mini"' in body
    assert "GPT 5-mini" in body


def test_settings_provider_secrets_render_as_plain_text(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={
        "openrouter": {
            "key_value": "sk-openrouter-visible",
            "extra": {"base_url": "https://openrouter.example/api", "model_id": "model-visible"},
        },
        "doubao_llm": {
            "key_value": "ark-visible",
            "extra": {"base_url": "https://ark.example/api", "model_id": "doubao-visible"},
        },
    }), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="openrouter"), \
         patch("web.routes.settings.get_image_translate_default_model",
               return_value="gemini-3-pro-image-preview"):
        resp = admin_no_db_client.get("/settings?tab=providers")

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'type="password"' not in body
    assert 'value="sk-openrouter-visible"' in body
    assert 'value="https://openrouter.example/api"' in body
    assert 'value="ark-visible"' in body
    assert "data-settings-copy" in body


def test_settings_push_secrets_render_values(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="aistudio"), \
         patch("web.routes.settings.get_image_translate_default_model",
               return_value="gemini-3.1-flash-image-preview"), \
         patch("appcore.pushes.get_push_target_url", return_value="http://push.example"), \
         patch("appcore.pushes.get_localized_texts_base_url", return_value="https://wedev.example"), \
         patch("appcore.pushes.get_localized_texts_authorization", return_value="Bearer visible-token"), \
         patch("appcore.pushes.get_localized_texts_cookie", return_value="sessionid=visible-cookie"):
        resp = admin_no_db_client.get("/settings?tab=push")

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'value="Bearer visible-token"' in body
    assert 'value="sessionid=visible-cookie"' in body


def test_settings_get_renders_seedream_channel_label(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="doubao"), \
         patch("web.routes.settings.get_image_translate_default_model",
               return_value="doubao-seedream-5-0-260128"):
        resp = admin_no_db_client.get("/settings")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "豆包 ARK（Seedream）" in body
    assert "DOUBAO_LLM_API_KEY" in body
    assert "VOLC_API_KEY" in body


def test_settings_get_renders_global_image_translate_model_select(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="openrouter"), \
         patch("web.routes.settings.get_image_translate_default_model",
               return_value="gemini-3-pro-image-preview"):
        resp = admin_no_db_client.get("/settings?tab=providers")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "选通道" in body
    assert 'name="image_translate_default_model"' in body
    assert 'value="gemini-3-pro-image-preview" selected' in body
    assert '"openrouter"' in body
    assert "Nano Banana Pro（高保真）" in body


def test_settings_get_renders_openai_image2_controls_for_openrouter(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="openrouter"), \
         patch("web.routes.settings.get_image_translate_default_model",
               return_value="gemini-3-pro-image-preview"), \
         patch("web.routes.settings.is_openrouter_openai_image2_enabled", return_value=True), \
         patch("web.routes.settings.get_openrouter_openai_image2_default_quality", return_value="high"):
        resp = admin_no_db_client.get("/settings?tab=providers")

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "启用 OpenAI Image 2" in body
    assert 'name="openrouter_openai_image2_enabled"' in body
    assert 'name="openrouter_openai_image2_default_quality"' in body
    assert 'value="high"' in body and 'selected' in body
    # 开启状态下 checkbox 应该有 checked
    assert 'id="openrouterOpenaiImage2Enabled"' in body
    assert "checked" in body


def test_settings_get_hides_openai_image2_controls_for_non_openrouter(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="aistudio"), \
         patch("web.routes.settings.get_image_translate_default_model",
               return_value="gemini-3.1-flash-image-preview"), \
         patch("web.routes.settings.is_openrouter_openai_image2_enabled", return_value=False), \
         patch("web.routes.settings.get_openrouter_openai_image2_default_quality", return_value="mid"):
        resp = admin_no_db_client.get("/settings?tab=providers")

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    # 控件存在但 hidden
    assert 'id="openrouterOpenaiImage2Controls"' in body
    assert "hidden" in body


def test_settings_post_providers_saves_openai_image2_controls(admin_no_db_client):
    with patch("web.routes.settings.set_image_translate_channel"), \
         patch("web.routes.settings.set_image_translate_default_model"), \
         patch("web.routes.settings.set_openrouter_openai_image2_enabled") as m_enabled, \
         patch("web.routes.settings.set_openrouter_openai_image2_default_quality") as m_quality:
        resp = admin_no_db_client.post("/settings", data={
            "tab": "providers",
            "translate_pref": "vertex_gemini_31_flash_lite",
            "jianying_project_root": "/custom/path",
            "image_translate_channel": "openrouter",
            "image_translate_default_model": "gemini-3-pro-image-preview",
            "openrouter_openai_image2_enabled": "1",
            "openrouter_openai_image2_default_quality": "high",
        })

    assert resp.status_code in (302, 303)
    m_enabled.assert_called_once_with(True)
    m_quality.assert_called_once_with("high")


def test_settings_post_providers_persists_false_when_checkbox_absent(admin_no_db_client):
    with patch("web.routes.settings.set_image_translate_channel"), \
         patch("web.routes.settings.set_image_translate_default_model"), \
         patch("web.routes.settings.set_openrouter_openai_image2_enabled") as m_enabled, \
         patch("web.routes.settings.set_openrouter_openai_image2_default_quality"):
        resp = admin_no_db_client.post("/settings", data={
            "tab": "providers",
            "translate_pref": "vertex_gemini_31_flash_lite",
            "jianying_project_root": "/custom/path",
            "image_translate_channel": "openrouter",
            "image_translate_default_model": "gemini-3-pro-image-preview",
            # checkbox 未勾选 → 浏览器不会提交该字段
            "openrouter_openai_image2_default_quality": "mid",
        })

    assert resp.status_code in (302, 303)
    m_enabled.assert_called_once_with(False)


def test_settings_post_providers_saves_global_image_translate_channel_and_model(admin_no_db_client):
    with patch("web.routes.settings.set_image_translate_channel") as m_set_channel, \
         patch("web.routes.settings.set_image_translate_default_model") as m_set_model:
        resp = admin_no_db_client.post("/settings", data={
            "tab": "providers",
            "translate_pref": "vertex_gemini_31_flash_lite",
            "jianying_project_root": "/custom/path",
            "image_translate_channel": "openrouter",
            "image_translate_default_model": "gemini-3-pro-image-preview",
        })

    assert resp.status_code in (302, 303)
    m_set_channel.assert_called_once_with("openrouter")
    m_set_model.assert_called_once_with("openrouter", "gemini-3-pro-image-preview")


def test_settings_bindings_hides_image_translate_generate(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings.llm_bindings.list_all",
               return_value=[
                   {
                       "code": "image_translate.detect", "module": "image",
                       "label": "图片文字检测", "description": "...",
                       "provider": "openrouter", "model": "gemini-3.1-flash-lite-preview",
                       "extra": {}, "enabled": True, "is_custom": True,
                       "updated_at": None, "updated_by": None,
                   },
                   {
                       "code": "image_translate.generate", "module": "image",
                       "label": "图片本地化重绘", "description": "...",
                       "provider": "gemini_vertex", "model": "gemini-3.1-flash-image-preview",
                       "extra": {}, "enabled": True, "is_custom": True,
                       "updated_at": None, "updated_by": None,
                   },
               ]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="openrouter"), \
         patch("web.routes.settings.get_image_translate_default_model",
               return_value="gemini-3.1-flash-image-preview"):
        resp = admin_no_db_client.get("/settings?tab=bindings")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "image_translate.detect" in body
    assert "image_translate.generate" not in body
    assert "图片本地化重绘" not in body


def test_settings_post_bindings_ignores_image_translate_generate(admin_no_db_client):
    with patch("web.routes.settings.llm_bindings.upsert") as m_upsert:
        resp = admin_no_db_client.post("/settings", data={
            "tab": "bindings",
            "binding_image_translate.generate_provider": "gemini_vertex",
            "binding_image_translate.generate_model": "gemini-3.1-flash-image-preview",
        })

    assert resp.status_code in (302, 303)
    assert not any(
        call.args and call.args[0] == "image_translate.generate"
        for call in m_upsert.call_args_list
    )


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
