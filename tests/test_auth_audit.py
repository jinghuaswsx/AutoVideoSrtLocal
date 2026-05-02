from types import SimpleNamespace


def _app(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.scheduled_tasks.latest_failure_alert", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.medias.list_enabled_language_codes", lambda: ["en", "de"])

    from web.app import create_app

    return create_app()


def test_login_success_records_audit(monkeypatch):
    from web.routes import auth

    calls = []
    row = {
        "id": 9,
        "username": "alice",
        "role": "user",
        "is_active": 1,
        "password_hash": "hash",
    }
    monkeypatch.setattr(auth, "get_by_username", lambda username: row)
    monkeypatch.setattr(auth, "check_password", lambda password, hashed: True)
    monkeypatch.setattr(auth, "login_user", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        auth,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    client = _app(monkeypatch).test_client()

    resp = client.post("/login", data={"username": "alice", "password": "pw"})

    assert resp.status_code == 302
    assert calls[0]["action"] == "login_success"
    assert calls[0]["module"] == "auth"
    assert calls[0]["target_id"] == 9
    assert calls[0]["target_label"] == "alice"


def test_login_failure_records_audit_without_password(monkeypatch):
    from web.routes import auth

    calls = []
    monkeypatch.setattr(auth, "get_by_username", lambda username: None)
    monkeypatch.setattr(
        auth,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    client = _app(monkeypatch).test_client()

    client.post("/login", data={"username": "missing", "password": "secret"})

    assert calls[0]["action"] == "login_failed"
    assert calls[0]["module"] == "auth"
    assert calls[0]["status"] == "failed"
    assert calls[0]["detail"] == {"username": "missing"}
    assert "secret" not in str(calls[0])


def test_logout_records_audit_before_logout(monkeypatch):
    from web.routes import auth

    calls = []
    fake_user = {
        "id": 4,
        "username": "bob",
        "role": "user",
        "is_active": 1,
    }
    monkeypatch.setattr("web.auth.get_by_id", lambda uid: fake_user if int(uid) == 4 else None)
    monkeypatch.setattr(auth, "logout_user", lambda: None)
    monkeypatch.setattr(
        auth,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    app = _app(monkeypatch)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "4"
        session["_fresh"] = True

    resp = client.get("/logout")

    assert resp.status_code == 302
    assert calls[0]["action"] == "logout"
    assert calls[0]["target_type"] == "user"
    assert calls[0]["target_id"] == 4
    assert calls[0]["target_label"] == "bob"
