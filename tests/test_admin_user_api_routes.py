from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def _superadmin_client(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.db.query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.scheduled_tasks.query", lambda *args, **kwargs: [])
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


def test_user_password_update_delegates_and_audits(monkeypatch):
    from web.routes import admin as route_mod

    update_calls = []
    audit_calls = []
    monkeypatch.setattr(
        "appcore.users.get_by_id",
        lambda uid: {
            "id": uid,
            "username": "worker",
            "role": "user",
            "is_active": 1,
        },
    )
    monkeypatch.setattr(
        route_mod,
        "update_password",
        lambda user_id, password: update_calls.append((user_id, password)),
    )
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: audit_calls.append(kwargs)),
        raising=False,
    )

    resp = _superadmin_client(monkeypatch).put(
        "/admin/api/users/9/password",
        json={"password": "new-secret"},
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert update_calls == [(9, "new-secret")]
    assert audit_calls[0]["action"] == "admin_user_password_updated"
    assert audit_calls[0]["target_type"] == "user"
    assert audit_calls[0]["target_id"] == 9
    assert audit_calls[0]["target_label"] == "worker"


def test_user_password_update_rejects_blank_password(monkeypatch):
    update_calls = []

    monkeypatch.setattr(
        "appcore.users.get_by_id",
        lambda uid: {
            "id": uid,
            "username": "worker",
            "role": "user",
            "is_active": 1,
        },
    )
    from web.routes import admin as route_mod

    monkeypatch.setattr(route_mod, "update_password", lambda *args: update_calls.append(args))

    resp = _superadmin_client(monkeypatch).put(
        "/admin/api/users/9/password",
        json={"password": "   "},
    )

    assert resp.status_code == 400
    assert "error" in resp.get_json()
    assert update_calls == []


def test_user_profile_update_delegates_without_password_and_audits(monkeypatch):
    from web.routes import admin as route_mod

    update_calls = []
    audit_calls = []
    monkeypatch.setattr(
        "appcore.users.get_by_id",
        lambda uid: {
            "id": uid,
            "username": "worker",
            "role": "user",
            "is_active": 1,
        },
    )
    monkeypatch.setattr(route_mod, "editable_user_profile_fields", lambda: ["xingming"])
    monkeypatch.setattr(
        route_mod,
        "update_user_profile",
        lambda user_id, **kwargs: update_calls.append((user_id, kwargs)),
    )
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: audit_calls.append(kwargs)),
        raising=False,
    )

    resp = _superadmin_client(monkeypatch).put(
        "/admin/api/users/9",
        json={
            "username": "worker-updated",
            "role": "admin",
            "is_active": False,
            "xingming": "王同学",
            "work_scopes": ["translation"],
            "password": "must-not-be-forwarded",
        },
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert update_calls == [
        (
            9,
            {
                "username": "worker-updated",
                "role": "admin",
                "is_active": False,
                "xingming": "王同学",
                "work_scopes": ["translation"],
            },
        )
    ]
    assert "password" not in update_calls[0][1]
    assert audit_calls[0]["action"] == "admin_user_profile_updated"
    assert audit_calls[0]["target_type"] == "user"
    assert audit_calls[0]["target_id"] == 9
    assert audit_calls[0]["target_label"] == "worker"


def test_admin_users_page_renders_profile_info_and_split_actions(monkeypatch):
    from web.routes import admin as route_mod

    monkeypatch.setattr(
        route_mod,
        "list_users",
        lambda: [
            {
                "id": 9,
                "username": "worker",
                "xingming": "王同学",
                "role": "user",
                "permissions": {"medias": True, "pushes": False, "work_scope_translation": True},
                "is_active": 1,
                "created_at": "2026-05-20 10:00:00",
            }
        ],
    )

    resp = _superadmin_client(monkeypatch).get("/admin/users")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "姓名" in body
    assert "王同学" in body
    assert "工作范围" in body
    assert "翻译工作" in body
    assert "editWorkScope_translation" in body
    assert "编辑" in body
    assert "修改密码" in body
    assert "openUserEditModal" in body
    assert "openPasswordModal" in body


def test_admin_users_requires_login_before_render(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.medias.list_enabled_language_codes", lambda: ["en"])

    from web.routes import admin as route_mod

    monkeypatch.setattr(
        route_mod,
        "list_users",
        lambda: (_ for _ in ()).throw(AssertionError("should not render")),
    )

    from web.app import create_app

    resp = create_app().test_client().get("/admin/users")

    assert resp.status_code == 302


def test_admin_users_permission_button_uses_json_encoded_arguments():
    template = (Path(__file__).resolve().parents[1] / "web" / "templates" / "admin_users.html").read_text(
        encoding="utf-8"
    )

    assert "{{ u.username | tojson }}" in template
    assert "{{ u.role | tojson }}" in template
    assert "{{ u.permissions_payload | tojson }}" in template
    assert "openPermModal({{ u.id }}, '{{ u.username }}'" not in template


def test_admin_users_password_button_uses_json_encoded_arguments():
    template = (Path(__file__).resolve().parents[1] / "web" / "templates" / "admin_users.html").read_text(
        encoding="utf-8"
    )

    assert "openUserEditModal({{ u.profile_payload | tojson }})" in template
    assert ">编辑</button>" in template
    assert ">修改密码</button>" in template
    assert "openPasswordModal({{ u.id | tojson }}, {{ u.username | tojson }})" in template
    assert "openPasswordModal({{ u.id }}, '{{ u.username }}')" not in template
    assert "/admin/api/users/' + passwordUserId + '/password" in template
    assert "/admin/api/users/' + editUserId" in template
    assert "editWorkScope_{{ scope.code }}" in template
    assert "work_scopes" in template
