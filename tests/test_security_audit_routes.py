def _client_with_user(monkeypatch, username="admin", role="superadmin", user_id=1):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.medias.list_enabled_language_codes", lambda: ["en", "de"])
    monkeypatch.setattr("appcore.scheduled_tasks.latest_failure_alert", lambda: None)

    from web.app import create_app

    fake_user = {
        "id": user_id,
        "username": username,
        "role": role,
        "is_active": 1,
    }
    monkeypatch.setattr(
        "web.auth.get_by_id",
        lambda uid: fake_user if int(uid) == user_id else None,
    )

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def test_security_audit_page_visible_to_reserved_superadmin(monkeypatch):
    client = _client_with_user(
        monkeypatch,
        username="admin",
        role="superadmin",
        user_id=1,
    )

    resp = client.get("/admin/security-audit")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "系统安全审计" in body
    assert "data-security-audit" in body
    assert "素材下载明细" in body


def test_security_audit_page_forbidden_for_normal_admin(monkeypatch):
    client = _client_with_user(
        monkeypatch,
        username="manager",
        role="admin",
        user_id=2,
    )

    assert client.get("/admin/security-audit").status_code == 403


def test_security_audit_api_forbidden_for_normal_user(monkeypatch):
    client = _client_with_user(
        monkeypatch,
        username="user",
        role="user",
        user_id=3,
    )

    assert client.get("/admin/security-audit/api/logs").status_code == 403


def test_security_audit_api_returns_logs(monkeypatch):
    client = _client_with_user(
        monkeypatch,
        username="admin",
        role="superadmin",
        user_id=1,
    )
    from web.routes import security_audit

    monkeypatch.setattr(
        security_audit.system_audit,
        "list_logs",
        lambda **kwargs: [{"id": 1, "action": "login_success"}],
    )
    monkeypatch.setattr(
        security_audit.system_audit,
        "count_logs",
        lambda **kwargs: 1,
    )

    resp = client.get("/admin/security-audit/api/logs?module=auth")

    assert resp.status_code == 200
    assert resp.get_json()["items"][0]["action"] == "login_success"


def test_layout_shows_security_audit_only_to_superadmin(monkeypatch):
    client = _client_with_user(
        monkeypatch,
        username="admin",
        role="superadmin",
        user_id=1,
    )
    resp = client.get("/tools/")
    assert "/admin/security-audit" in resp.get_data(as_text=True)

    client = _client_with_user(
        monkeypatch,
        username="manager",
        role="admin",
        user_id=2,
    )
    resp = client.get("/tools/")
    assert "/admin/security-audit" not in resp.get_data(as_text=True)
