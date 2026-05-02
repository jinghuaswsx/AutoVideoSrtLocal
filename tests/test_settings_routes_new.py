"""/settings 路由测试（2026-04-25 DB-driven providers tab）。

关键变化：
  - providers Tab 模板改用 provider_groups 迭代，每个 provider_code 一行
    独立 api_key / base_url / model_id / extra_config 输入，敏感凭据不回显。
  - 保存 POST 走 `provider_<code>_*` 字段，经 DAO.save_provider_config 落 DB。
  - 旧 SERVICES 硬编码字段（openrouter_key / doubao_llm_key 等）已移除。
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _neutralize_db(monkeypatch):
    monkeypatch.setattr("appcore.db.query", lambda *a, **k: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *a, **k: None)
    monkeypatch.setattr("appcore.db.execute", lambda *a, **k: 0)

    fake_admin_row = {
        "id": 1, "username": "admin", "role": "superadmin", "is_active": 1,
    }

    def fake_api_key_query_one(sql, params=()):
        if "role = 'superadmin'" in sql:
            return fake_admin_row
        if "FROM users WHERE username = %s" in sql and params and params[0] == "admin":
            return fake_admin_row
        if "FROM users WHERE id = %s" in sql and params and int(params[0]) == 1:
            return fake_admin_row
        return None

    monkeypatch.setattr("appcore.api_keys.query", lambda *a, **k: [])
    monkeypatch.setattr("appcore.api_keys.query_one", fake_api_key_query_one)
    monkeypatch.setattr("appcore.api_keys.execute", lambda *a, **k: 0)
    monkeypatch.setattr("appcore.db._get_pool", lambda: MagicMock())
    # DAO 默认返回空：无 provider 时 providers Tab 仍能渲染
    monkeypatch.setattr("appcore.llm_provider_configs.query", lambda *a, **k: [])
    monkeypatch.setattr("appcore.llm_provider_configs.query_one", lambda *a, **k: None)
    monkeypatch.setattr("appcore.llm_provider_configs.execute", lambda *a, **k: 0)


@pytest.fixture
def admin_no_db_client(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    from web.app import create_app

    fake_user = {"id": 1, "username": "admin", "role": "superadmin", "is_active": 1}
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


# ---------------------------------------------------------------------------
# 工具：构造 provider_groups fixture 数据
# ---------------------------------------------------------------------------

def _fake_provider_groups(rows: list[dict] | None = None) -> list[dict]:
    rows = rows or [
        {
            "provider_code": "openrouter_text",
            "display_name": "OpenRouter 文本",
            "api_key": "sk-openrouter-visible",
            "api_key_present": True,
            "api_key_mask": "已配置（末四位 ible）",
            "base_url": "https://openrouter.example/api",
            "model_id": "model-visible",
            "extra_config_json": "",
            "enabled": True,
        },
        {
            "provider_code": "doubao_llm",
            "display_name": "豆包 ARK 文本",
            "api_key": "ark-visible",
            "api_key_present": True,
            "api_key_mask": "已配置（末四位 ible）",
            "base_url": "https://ark.example/api",
            "model_id": "doubao-visible",
            "extra_config_json": "",
            "enabled": True,
        },
    ]
    return [{"code": "text_llm", "label": "文本 / 本土化 LLM", "rows": rows}]


def test_provider_rows_by_group_masks_api_key_in_view(monkeypatch):
    from appcore.llm_provider_configs import LlmProviderConfig
    from web.routes import settings as settings_routes

    monkeypatch.setattr(
        settings_routes.llm_provider_configs,
        "list_provider_configs",
        lambda: [
            LlmProviderConfig(
                provider_code="openrouter_text",
                display_name="OpenRouter 文本",
                group_code="text_llm",
                api_key="sk-openrouter-visible",
                base_url="https://openrouter.example/api",
                model_id="model-visible",
            ),
        ],
    )

    provider_groups = settings_routes._provider_rows_by_group()
    row = provider_groups[0]["rows"][0]
    assert "api_key" not in row
    assert row["api_key_present"] is True
    assert row["api_key_mask"] == "已配置（末四位 ible）"


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------

def test_settings_requires_exact_admin_username(non_owner_clients):
    manager_client, normal_client = non_owner_clients
    assert manager_client.get("/settings").status_code == 403
    assert normal_client.get("/settings").status_code == 403
    assert manager_client.get("/admin/settings/ai-pricing/list").status_code == 403


# ---------------------------------------------------------------------------
# GET /settings —— 基础渲染
# ---------------------------------------------------------------------------

def test_settings_get_renders_bindings_rows(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings._provider_rows_by_group",
               return_value=_fake_provider_groups([])), \
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
    assert "video_score.run" in body or "视频评分" in body


def test_settings_get_renders_gpt_5_mini_translate_option(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings._provider_rows_by_group",
               return_value=_fake_provider_groups([])), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="aistudio"):
        resp = admin_no_db_client.get("/settings?tab=providers")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'value="gpt_5_mini"' in body
    assert "GPT 5-mini" in body


def test_settings_get_renders_gpt_5_5_translate_option(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings._provider_rows_by_group",
               return_value=_fake_provider_groups([])), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="aistudio"):
        resp = admin_no_db_client.get("/settings?tab=providers")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'value="gpt_5_5"' in body
    assert "GPT-5.5" in body


# ---------------------------------------------------------------------------
# GET /settings?tab=providers —— 供应商凭据不回显
# ---------------------------------------------------------------------------

def test_settings_provider_secrets_do_not_render_plain_text(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings._provider_rows_by_group",
               return_value=_fake_provider_groups()), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="openrouter"), \
         patch("web.routes.settings.get_image_translate_default_model",
               return_value="gemini-3-pro-image-preview"):
        resp = admin_no_db_client.get("/settings?tab=providers")

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'type="password"' in body
    assert "sk-openrouter-visible" not in body
    assert "ark-visible" not in body
    assert "已配置，留空不变" in body
    assert "已配置（末四位 ible）" in body
    assert 'value="https://openrouter.example/api"' in body
    assert 'value="provider_openrouter_text_api_key"' in body
    assert 'value="provider_doubao_llm_api_key"' in body
    # 新输入名约定：provider_<code>_(api_key|base_url|model_id|extra_config)
    assert 'name="provider_openrouter_text_api_key"' in body
    assert 'name="provider_doubao_llm_api_key"' in body


def test_settings_provider_rows_show_provider_code_and_extra_config(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings._provider_rows_by_group",
               return_value=_fake_provider_groups([{
                   "provider_code": "gemini_cloud_text",
                   "display_name": "Google Cloud Vertex 文本",
                   "api_key": "cloud-visible",
                   "api_key_present": True,
                   "api_key_mask": "已配置（末四位 ible）",
                   "base_url": "",
                   "model_id": "",
                   "extra_config_json": '{"project": "demo-gcp", "location": "us-central1"}',
                   "enabled": True,
               }])), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="aistudio"):
        resp = admin_no_db_client.get("/settings?tab=providers")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "gemini_cloud_text" in body
    assert "cloud-visible" not in body
    assert "demo-gcp" in body
    assert 'name="provider_gemini_cloud_text_extra_config"' in body


# ---------------------------------------------------------------------------
# Push / 其他 Tab
# ---------------------------------------------------------------------------

def test_settings_push_secrets_do_not_render_plain_text(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings._provider_rows_by_group",
               return_value=_fake_provider_groups([])), \
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
    assert "Bearer visible-token" not in body
    assert "sessionid=visible-cookie" not in body
    assert "已配置，留空不变" in body
    assert "状态：已配置" in body


def test_settings_get_renders_seedream_channel_label(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings._provider_rows_by_group",
               return_value=_fake_provider_groups([])), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="doubao"), \
         patch("web.routes.settings.get_image_translate_default_model",
               return_value="doubao-seedream-5-0-260128"):
        resp = admin_no_db_client.get("/settings")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "豆包 ARK（Seedream）" in body
    # 页面里不应再出现 DOUBAO_LLM_API_KEY / VOLC_API_KEY 这种 env 变量名
    assert "DOUBAO_LLM_API_KEY" not in body
    assert "VOLC_API_KEY" not in body


def test_settings_get_renders_global_image_translate_model_select(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings._provider_rows_by_group",
               return_value=_fake_provider_groups([])), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="openrouter"), \
         patch("web.routes.settings.get_image_translate_default_model",
               return_value="gemini-3-pro-image-preview"):
        resp = admin_no_db_client.get("/settings?tab=providers")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "图片翻译通道" in body
    assert 'name="image_translate_default_model"' in body
    assert 'value="gemini-3-pro-image-preview" selected' in body
    assert '"openrouter"' in body
    assert "Nano Banana Pro（高保真）" in body


def test_settings_get_renders_openai_image2_controls_for_openrouter(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings._provider_rows_by_group",
               return_value=_fake_provider_groups([])), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="openrouter"), \
         patch("web.routes.settings.get_image_translate_default_model",
               return_value="gemini-3-pro-image-preview"), \
         patch("web.routes.settings.is_openrouter_openai_image2_enabled", return_value=True), \
         patch("web.routes.settings.get_openrouter_openai_image2_default_quality",
               return_value="high"):
        resp = admin_no_db_client.get("/settings?tab=providers")

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "启用 OpenAI Image 2" in body
    assert 'name="openrouter_openai_image2_enabled"' in body
    assert 'name="openrouter_openai_image2_default_quality"' in body
    assert 'value="high"' in body and 'selected' in body
    assert 'id="openrouterOpenaiImage2Enabled"' in body
    assert "checked" in body


def test_settings_get_hides_openai_image2_controls_for_non_openrouter(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings._provider_rows_by_group",
               return_value=_fake_provider_groups([])), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="aistudio"), \
         patch("web.routes.settings.get_image_translate_default_model",
               return_value="gemini-3.1-flash-image-preview"), \
         patch("web.routes.settings.is_openrouter_openai_image2_enabled", return_value=False), \
         patch("web.routes.settings.get_openrouter_openai_image2_default_quality",
               return_value="mid"):
        resp = admin_no_db_client.get("/settings?tab=providers")

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'id="openrouterOpenaiImage2Controls"' in body
    assert "hidden" in body


# ---------------------------------------------------------------------------
# POST providers Tab —— 新的 provider_<code>_* 字段路由
# ---------------------------------------------------------------------------

def test_settings_post_providers_saves_provider_api_key_via_dao(admin_no_db_client):
    with patch("web.routes.settings.set_image_translate_channel"), \
         patch("web.routes.settings.set_image_translate_default_model"), \
         patch("web.routes.settings.set_openrouter_openai_image2_enabled"), \
         patch("web.routes.settings.set_openrouter_openai_image2_default_quality"), \
         patch("appcore.llm_provider_configs.save_provider_config") as m_save:
        resp = admin_no_db_client.post("/settings", data={
            "tab": "providers",
            "translate_pref": "vertex_gemini_31_flash_lite",
            "provider_openrouter_text_api_key": "sk-fresh",
            "provider_openrouter_text_base_url": "https://openrouter.example/api",
            "provider_openrouter_text_model_id": "anthropic/claude-sonnet-4.6",
            "provider_openrouter_text_extra_config": "",
            "image_translate_channel": "openrouter",
            "image_translate_default_model": "gemini-3-pro-image-preview",
            "openrouter_openai_image2_enabled": "1",
            "openrouter_openai_image2_default_quality": "high",
        })

    assert resp.status_code in (302, 303)
    save_calls = [c for c in m_save.call_args_list if c.args[0] == "openrouter_text"]
    assert save_calls, "openrouter_text 保存未触发"
    fields = save_calls[0].args[1]
    assert fields["api_key"] == "sk-fresh"
    assert fields["base_url"] == "https://openrouter.example/api"
    assert fields["model_id"] == "anthropic/claude-sonnet-4.6"
    # 空 extra_config 传为 {}
    assert fields["extra_config"] == {}


def test_settings_post_providers_keeps_existing_api_key_when_secret_input_blank(admin_no_db_client):
    with patch("web.routes.settings.set_image_translate_channel"), \
         patch("web.routes.settings.set_image_translate_default_model"), \
         patch("web.routes.settings.set_openrouter_openai_image2_enabled"), \
         patch("web.routes.settings.set_openrouter_openai_image2_default_quality"), \
         patch("appcore.llm_provider_configs.save_provider_config") as m_save:
        resp = admin_no_db_client.post("/settings", data={
            "tab": "providers",
            "translate_pref": "vertex_gemini_31_flash_lite",
            "provider_openrouter_text_api_key": "",
            "provider_openrouter_text_base_url": "https://openrouter.example/api",
            "provider_openrouter_text_model_id": "anthropic/claude-sonnet-4.6",
            "provider_openrouter_text_extra_config": "",
            "image_translate_channel": "openrouter",
            "image_translate_default_model": "gemini-3-pro-image-preview",
        })

    assert resp.status_code in (302, 303)
    save_call = next(c for c in m_save.call_args_list if c.args[0] == "openrouter_text")
    assert "api_key" not in save_call.args[1]
    assert save_call.args[1]["base_url"] == "https://openrouter.example/api"


def test_settings_post_providers_clears_api_key_only_when_requested(admin_no_db_client):
    with patch("web.routes.settings.set_image_translate_channel"), \
         patch("web.routes.settings.set_image_translate_default_model"), \
         patch("web.routes.settings.set_openrouter_openai_image2_enabled"), \
         patch("web.routes.settings.set_openrouter_openai_image2_default_quality"), \
         patch("appcore.llm_provider_configs.save_provider_config") as m_save:
        resp = admin_no_db_client.post("/settings", data={
            "tab": "providers",
            "translate_pref": "vertex_gemini_31_flash_lite",
            "provider_openrouter_text_api_key": "",
            "provider_openrouter_text_base_url": "https://openrouter.example/api",
            "provider_openrouter_text_model_id": "anthropic/claude-sonnet-4.6",
            "provider_openrouter_text_extra_config": "",
            "clear": "provider_openrouter_text_api_key",
            "image_translate_channel": "openrouter",
            "image_translate_default_model": "gemini-3-pro-image-preview",
        })

    assert resp.status_code in (302, 303)
    save_call = next(c for c in m_save.call_args_list if c.args[0] == "openrouter_text")
    assert save_call.args[1]["api_key"] == ""


def test_settings_post_providers_parses_json_extra_config(admin_no_db_client):
    with patch("web.routes.settings.set_image_translate_channel"), \
         patch("web.routes.settings.set_image_translate_default_model"), \
         patch("web.routes.settings.set_openrouter_openai_image2_enabled"), \
         patch("web.routes.settings.set_openrouter_openai_image2_default_quality"), \
         patch("appcore.llm_provider_configs.save_provider_config") as m_save:
        admin_no_db_client.post("/settings", data={
            "tab": "providers",
            "translate_pref": "vertex_gemini_31_flash_lite",
            "provider_gemini_cloud_text_api_key": "cloud-key",
            "provider_gemini_cloud_text_base_url": "",
            "provider_gemini_cloud_text_model_id": "gemini-3.1-pro-preview",
            "provider_gemini_cloud_text_extra_config": '{"project": "demo-gcp", "location": "us-central1"}',
            "image_translate_channel": "cloud",
            "image_translate_default_model": "gemini-3-pro-image-preview",
        })

    cloud_call = next(c for c in m_save.call_args_list if c.args[0] == "gemini_cloud_text")
    fields = cloud_call.args[1]
    assert fields["extra_config"] == {"project": "demo-gcp", "location": "us-central1"}


def test_settings_post_providers_skips_invalid_json_extra_config(admin_no_db_client):
    with patch("web.routes.settings.set_image_translate_channel"), \
         patch("web.routes.settings.set_image_translate_default_model"), \
         patch("web.routes.settings.set_openrouter_openai_image2_enabled"), \
         patch("web.routes.settings.set_openrouter_openai_image2_default_quality"), \
         patch("appcore.llm_provider_configs.save_provider_config") as m_save:
        admin_no_db_client.post("/settings", data={
            "tab": "providers",
            "translate_pref": "vertex_gemini_31_flash_lite",
            "provider_doubao_asr_api_key": "asr-key",
            "provider_doubao_asr_extra_config": "{not json",
            "image_translate_channel": "aistudio",
            "image_translate_default_model": "gemini-3.1-flash-image-preview",
        })
    # 非法 JSON 不应触发保存该 provider 行
    for call in m_save.call_args_list:
        assert call.args[0] != "doubao_asr"


def test_settings_post_providers_persists_image2_off_when_checkbox_absent(admin_no_db_client):
    with patch("web.routes.settings.set_image_translate_channel"), \
         patch("web.routes.settings.set_image_translate_default_model"), \
         patch("web.routes.settings.set_openrouter_openai_image2_enabled") as m_enabled, \
         patch("web.routes.settings.set_openrouter_openai_image2_default_quality"):
        resp = admin_no_db_client.post("/settings", data={
            "tab": "providers",
            "translate_pref": "vertex_gemini_31_flash_lite",
            "image_translate_channel": "openrouter",
            "image_translate_default_model": "gemini-3-pro-image-preview",
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
            "image_translate_channel": "openrouter",
            "image_translate_default_model": "gemini-3-pro-image-preview",
        })

    assert resp.status_code in (302, 303)
    m_set_channel.assert_called_once_with("openrouter")
    m_set_model.assert_called_once_with("openrouter", "gemini-3-pro-image-preview")


# ---------------------------------------------------------------------------
# Bindings Tab
# ---------------------------------------------------------------------------

def test_settings_bindings_hides_image_translate_generate(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings._provider_rows_by_group",
               return_value=_fake_provider_groups([])), \
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
    with patch("web.routes.settings.set_image_translate_channel"), \
         patch("appcore.llm_provider_configs.save_provider_config") as m_save:
        resp = admin_no_db_client.post("/settings", data={
            "provider_openrouter_text_api_key": "legacy-submit",
            "translate_pref": "vertex_gemini_31_flash_lite",
        })
    assert resp.status_code in (302, 303)
    assert any(call.args[0] == "openrouter_text" for call in m_save.call_args_list)
