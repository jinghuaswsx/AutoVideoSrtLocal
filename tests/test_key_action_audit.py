from types import SimpleNamespace


def _client(monkeypatch, *, user_id=1, username="admin", role="admin"):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.scheduled_tasks.latest_failure_alert", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.medias.list_enabled_language_codes", lambda: ["en", "de"])

    from web.app import create_app

    fake_user = {
        "id": user_id,
        "username": username,
        "role": role,
        "is_active": 1,
    }
    monkeypatch.setattr("web.auth.get_by_id", lambda uid: fake_user if int(uid) == user_id else None)

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def test_task_claim_records_audit(monkeypatch):
    from web.routes import tasks as route_mod

    calls = []
    monkeypatch.setattr(route_mod.tasks_svc, "claim_parent", lambda **kwargs: None)
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    resp = _client(monkeypatch).post("/tasks/api/parent/5/claim")

    assert resp.status_code == 200
    assert calls[0]["action"] == "task_parent_claimed"
    assert calls[0]["module"] == "tasks"
    assert calls[0]["target_type"] == "task"
    assert calls[0]["target_id"] == 5


def test_push_reset_records_audit(monkeypatch):
    from web.routes import pushes as route_mod

    calls = []
    monkeypatch.setattr(route_mod.pushes, "reset_push_state", lambda item_id: None)
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    resp = _client(monkeypatch).post("/pushes/api/items/8/reset")

    assert resp.status_code == 204
    assert calls[0]["action"] == "push_reset"
    assert calls[0]["module"] == "pushes"
    assert calls[0]["target_type"] == "media_item"
    assert calls[0]["target_id"] == 8


def test_push_credentials_update_records_keys_without_secret_values(monkeypatch):
    from web.routes import pushes as route_mod

    calls = []
    saved = []
    monkeypatch.setattr("appcore.settings.set_setting", lambda key, value: saved.append((key, value)))
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    resp = _client(monkeypatch).post(
        "/pushes/api/push-credentials",
        json={
            "push_target_url": "https://push.example.test",
            "push_product_links_password": "super-secret-password",
        },
    )

    assert resp.status_code == 200
    assert set(saved) == {
        ("push_target_url", "https://push.example.test"),
        ("push_product_links_password", "super-secret-password"),
    }
    assert calls[0]["action"] == "push_credentials_updated"
    assert calls[0]["target_type"] == "system_setting"
    assert set(calls[0]["detail"]["updated_keys"]) == {"push_target_url", "push_product_links_password"}
    assert "super-secret-password" not in str(calls[0])


def test_superadmin_user_role_update_records_audit(monkeypatch):
    from web.routes import admin as route_mod

    calls = []
    monkeypatch.setattr(
        "appcore.users.get_by_id",
        lambda uid: {
            "id": uid,
            "username": "worker",
            "role": "user",
            "is_active": 1,
        },
    )
    monkeypatch.setattr(route_mod, "update_role", lambda user_id, role: None)
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    resp = _client(monkeypatch, role="superadmin").put("/admin/api/users/9/role", json={"role": "admin"})

    assert resp.status_code == 200
    assert calls[0]["action"] == "admin_user_role_updated"
    assert calls[0]["module"] == "admin"
    assert calls[0]["target_type"] == "user"
    assert calls[0]["target_id"] == 9
    assert calls[0]["target_label"] == "worker"
    assert calls[0]["detail"] == {"old_role": "user", "new_role": "admin"}
