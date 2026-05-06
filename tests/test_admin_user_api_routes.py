from __future__ import annotations

from types import SimpleNamespace


def _superadmin_client(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.medias.list_enabled_language_codes", lambda: ["en", "de"])

    from web.app import create_app

    fake_user = {
        "id": 1,
        "username": "admin",
        "role": "superadmin",
        "is_active": 1,
    }
    monkeypatch.setattr("web.auth.get_by_id", lambda uid: fake_user if int(uid) == 1 else None)

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True
    return client


def test_user_permissions_missing_user_returns_json_404(monkeypatch):
    monkeypatch.setattr("appcore.users.get_by_id", lambda uid: None)

    resp = _superadmin_client(monkeypatch).get("/admin/api/users/99/permissions")

    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_user_role_update_rejects_superadmin_without_mutation(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "appcore.users.get_by_id",
        lambda uid: {
            "id": uid,
            "username": "root-admin",
            "role": "superadmin",
            "is_active": 1,
        },
    )

    from web.routes import admin as route_mod

    monkeypatch.setattr(route_mod, "update_role", lambda *args, **kwargs: calls.append(args))

    resp = _superadmin_client(monkeypatch).put("/admin/api/users/1/role", json={"role": "admin"})

    assert resp.status_code == 403
    assert "error" in resp.get_json()
    assert calls == []


def test_user_permissions_update_delegates_and_audits(monkeypatch):
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
    monkeypatch.setattr(route_mod, "update_permissions", lambda user_id, perms: {"task.read": True})
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    resp = _superadmin_client(monkeypatch).put(
        "/admin/api/users/9/permissions",
        json={"permissions": {"task.read": True}},
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "permissions": {"task.read": True}}
    assert calls[0]["action"] == "admin_user_permissions_updated"
    assert calls[0]["target_type"] == "user"
    assert calls[0]["target_id"] == 9
