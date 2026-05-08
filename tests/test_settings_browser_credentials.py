from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _neutralize_db(monkeypatch):
    monkeypatch.setattr("appcore.db.query", lambda *a, **k: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *a, **k: None)
    monkeypatch.setattr("appcore.db.execute", lambda *a, **k: 0)
    monkeypatch.setattr("appcore.db._get_pool", lambda: MagicMock())

    fake_admin_row = {
        "id": 1,
        "username": "admin",
        "role": "superadmin",
        "is_active": 1,
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
    monkeypatch.setattr("appcore.llm_provider_configs.query", lambda *a, **k: [])
    monkeypatch.setattr("appcore.llm_provider_configs.query_one", lambda *a, **k: None)
    monkeypatch.setattr("appcore.llm_provider_configs.execute", lambda *a, **k: 0)


@pytest.fixture
def admin_no_db_client(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    from web.app import create_app

    fake_user = {
        "id": 1,
        "username": "admin",
        "role": "superadmin",
        "is_active": 1,
    }
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


def test_settings_browser_credentials_tab_renders_masked_username(admin_no_db_client):
    row = {
        "env_code": "DXM01-Meta",
        "provider": "facebook",
        "username_mask": "acct********1025",
        "password_present": True,
        "enabled": True,
        "last_login_status": "failed",
        "last_error": "login_required",
    }
    with patch("web.routes.settings._provider_rows_by_group", return_value=[]), \
         patch("web.routes.settings.browser_login_credentials.list_credentials_view", return_value=[row]):
        resp = admin_no_db_client.get("/settings?tab=browser_credentials")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "浏览器登录凭据" in body
    assert "acct********1025" in body
    assert "acct000000001025" not in body
    assert "plain-password" not in body


def test_settings_post_browser_credentials_blank_password_preserves_old(admin_no_db_client):
    saved = []
    with patch(
        "web.routes.settings.browser_login_credentials.save_credential",
        side_effect=lambda *args, **kwargs: saved.append((args, kwargs)),
    ):
        resp = admin_no_db_client.post("/settings", data={
            "tab": "browser_credentials",
            "browser_env_code": "DXM01-Meta",
            "browser_provider": "facebook",
            "browser_username": "acct000000001025",
            "browser_password": "",
            "browser_enabled": "on",
        })

    assert resp.status_code in (302, 303)
    args, kwargs = saved[0]
    assert args[:2] == ("DXM01-Meta", "facebook")
    assert kwargs["username"] == "acct000000001025"
    assert kwargs["password"] is None
    assert kwargs["enabled"] is True
    assert kwargs["updated_by"] == 1
