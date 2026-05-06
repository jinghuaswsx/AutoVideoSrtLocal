from __future__ import annotations

import json


def _client_for_user(monkeypatch, *, role: str = "user", username: str = "worker", permissions=None):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "appcore.medias.list_enabled_language_codes",
        lambda: ["de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en"],
    )

    from web.app import create_app

    fake_user = {
        "id": 9,
        "username": username,
        "role": role,
        "is_active": 1,
        "permissions": json.dumps(permissions) if permissions is not None else None,
    }
    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: fake_user if int(user_id) == 9 else None)

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "9"
        session["_fresh"] = True
    return client


def test_order_profit_and_orphan_orders_are_separate_admin_default_permissions():
    from appcore.permissions import ROLE_ADMIN, ROLE_USER, default_permissions_for_role

    admin_defaults = default_permissions_for_role(ROLE_ADMIN)
    user_defaults = default_permissions_for_role(ROLE_USER)

    assert admin_defaults["order_profit"] is True
    assert admin_defaults["orphan_orders"] is True
    assert user_defaults["order_profit"] is False
    assert user_defaults["orphan_orders"] is False


def test_new_menu_permission_defaults_to_admin_only():
    from appcore.permissions import GROUP_MANAGEMENT, menu_permission

    assert menu_permission("future_menu", GROUP_MANAGEMENT, "Future menu") == (
        "future_menu",
        GROUP_MANAGEMENT,
        "Future menu",
        True,
        False,
    )


def test_data_analytics_grant_does_not_show_order_profit_or_orphan_menus(monkeypatch):
    client = _client_for_user(monkeypatch, permissions={"data_analytics": True})

    response = client.get("/tools/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'href="/order-analytics"' in html
    assert 'href="/order-profit"' not in html
    assert 'href="/order-analytics/orphan-orders"' not in html


def test_order_profit_permission_grant_allows_user_page_access(monkeypatch):
    client = _client_for_user(monkeypatch, permissions={"order_profit": True})

    response = client.get("/order-profit")

    assert response.status_code == 200
    assert "order-profit" in response.get_data(as_text=True)


def test_orphan_orders_permission_grant_allows_user_page_access(monkeypatch):
    client = _client_for_user(monkeypatch, permissions={"orphan_orders": True})

    response = client.get("/order-analytics/orphan-orders")

    assert response.status_code == 200
    assert "orphan-orders" in response.get_data(as_text=True)
