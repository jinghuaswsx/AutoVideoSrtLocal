from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

import pytest


@pytest.fixture
def superadmin_client_no_db(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.scheduled_tasks._safe_query_rows", lambda *args, **kwargs: [])

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


def _settings_get_patches(domain_rows=None):
    return [
        patch("web.routes.settings.get_all", return_value={}),
        patch("web.routes.settings._provider_rows_by_group", return_value=[]),
        patch("web.routes.settings._infrastructure_rows_by_group", return_value=[]),
        patch("web.routes.settings.llm_bindings.list_all", return_value=[]),
        patch("web.routes.settings.get_image_translate_channel", return_value="aistudio"),
        patch("web.routes.settings.get_image_translate_default_model", return_value="gemini-3.1-flash"),
        patch("appcore.product_link_domains._query", return_value=domain_rows or []),
        patch("appcore.settings._query_one", return_value=None),
        patch("appcore.settings._query", return_value=[]),
        patch("appcore.pushes.get_push_target_url", return_value=""),
        patch("appcore.pushes.get_localized_texts_base_url", return_value=""),
        patch("appcore.pushes.get_localized_texts_authorization", return_value=""),
        patch("appcore.pushes.get_localized_texts_cookie", return_value=""),
        patch("appcore.pushes.get_product_links_base_url", return_value=""),
        patch("appcore.pushes.get_product_links_username", return_value=""),
        patch("appcore.pushes.get_product_links_password", return_value=""),
    ]


def _admin_settings_get_patches(domain_rows=None):
    return [
        patch("web.routes.admin.get_all_retention_settings", return_value={"default": 168}),
        patch("web.routes.admin.get_setting", return_value=None),
        patch("web.routes.admin.product_roas.get_configured_rmb_per_usd", return_value="6.83"),
        patch("web.routes.admin.medias.list_languages_for_admin", return_value=[]),
        patch("web.routes.admin.product_link_domains.list_domains", return_value=domain_rows or []),
    ]


def test_api_settings_no_longer_renders_product_domain_management_card(superadmin_client_no_db):
    patches = _settings_get_patches()
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        resp = superadmin_client_no_db.get("/settings?tab=providers")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "domainManagementCard" not in body


def test_admin_settings_domains_tab_renders_product_domain_management_card(superadmin_client_no_db):
    patches = _admin_settings_get_patches([
        {"id": 1, "domain": "newjoyloo.com", "enabled": True, "sort_order": 10},
        {"id": 2, "domain": "omurio.com", "enabled": True, "sort_order": 20},
    ])
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        resp = superadmin_client_no_db.get("/admin/settings?tab=domains")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "domainManagementCard" in body
    assert "newjoyloo.com" in body
    assert "omurio.com" in body


def test_admin_settings_domains_add_row_has_large_input_spacing(superadmin_client_no_db):
    patches = _admin_settings_get_patches([
        {"id": 1, "domain": "newjoyloo.com", "enabled": True, "sort_order": 10},
    ])
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        resp = superadmin_client_no_db.get("/admin/settings?tab=domains")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert (
        ".settings-domain-add { display:grid; grid-template-columns:minmax(220px, 1fr) auto; "
        "gap:10px; align-items:end; margin-bottom:100px; }"
    ) in body
    assert (
        ".settings-domain-add-field { display:flex; align-items:center; gap:8px; margin:0; "
        "color:var(--text-main,#111827); font-size:26px; font-weight:700; line-height:1.3; "
        "white-space:nowrap; }"
    ) in body
    assert ".settings-domain-input {" in body
    assert "width:440px; max-width:100%; height:48px;" in body
    assert 'class="settings-domain-add-field"' in body
    assert 'class="settings-domain-input"' in body


def test_admin_settings_post_product_domains_adds_normalized_domain(superadmin_client_no_db):
    with patch("web.routes.admin.product_link_domains.upsert_domain") as upsert:
        resp = superadmin_client_no_db.post(
            "/admin/settings",
            data={
                "tab": "domains",
                "domain_action": "add",
                "new_domain": "https://Omurio.com/",
            },
        )

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/settings?tab=domains")
    upsert.assert_called_once_with("https://Omurio.com/", enabled=True)


def test_admin_settings_post_product_domains_saves_enabled_ids(superadmin_client_no_db):
    with patch("web.routes.admin.product_link_domains.set_global_enabled_domain_ids") as save:
        resp = superadmin_client_no_db.post(
            "/admin/settings",
            data={
                "tab": "domains",
                "domain_action": "save",
                "enabled_domain_ids": ["1", "bad", "2"],
            },
        )

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/settings?tab=domains")
    save.assert_called_once_with([1, 2])


def test_admin_settings_domains_tab_renders_default_badge_and_set_default_button(
    superadmin_client_no_db,
):
    patches = _admin_settings_get_patches([
        {
            "id": 1,
            "domain": "newjoyloo.com",
            "enabled": True,
            "is_default": True,
            "sort_order": 10,
        },
        {
            "id": 2,
            "domain": "omurio.com",
            "enabled": True,
            "is_default": False,
            "sort_order": 20,
        },
    ])
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        resp = superadmin_client_no_db.get("/admin/settings?tab=domains")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Default row should render the badge, not the "set default" button.
    assert 'class="settings-domain-default-badge"' in body
    # Non-default row should render the "set default" button with its id.
    assert "设为默认" in body
    assert 'name="default_domain_id" value="2"' in body


def test_admin_settings_domains_tab_marks_disabled_default_with_warning_badge(
    superadmin_client_no_db,
):
    patches = _admin_settings_get_patches([
        {
            "id": 1,
            "domain": "newjoyloo.com",
            "enabled": False,
            "is_default": True,
            "sort_order": 10,
        },
    ])
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        resp = superadmin_client_no_db.get("/admin/settings?tab=domains")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "默认（已停用）" in body


def test_admin_settings_post_product_domains_set_default_invokes_helper(
    superadmin_client_no_db,
):
    with patch("web.routes.admin.product_link_domains.set_default_domain") as setter:
        resp = superadmin_client_no_db.post(
            "/admin/settings",
            data={
                "tab": "domains",
                "domain_action": "set_default",
                "default_domain_id": "7",
            },
        )

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/settings?tab=domains")
    setter.assert_called_once_with(7)


def test_admin_settings_post_product_domains_set_default_rejects_zero_id(
    superadmin_client_no_db,
):
    with patch("web.routes.admin.product_link_domains.set_default_domain") as setter:
        resp = superadmin_client_no_db.post(
            "/admin/settings",
            data={
                "tab": "domains",
                "domain_action": "set_default",
                "default_domain_id": "0",
            },
        )

    assert resp.status_code == 302
    setter.assert_not_called()
