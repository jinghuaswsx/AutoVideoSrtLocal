"""Route tests for ``/admin/runtime/active-tasks``.

These tests do not touch the local MySQL: ``appcore.db.execute`` is patched
to a no-op and the user loader is patched to return an in-memory fake user.
"""
from __future__ import annotations

import pytest


def _build_fake_app(monkeypatch, *, fake_user: dict | None):
    """Create a Flask test client with the given fake user (or anonymous)."""
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)

    if fake_user is not None:
        def _loader(user_id):
            if int(user_id) == int(fake_user["id"]):
                return fake_user
            return None
        monkeypatch.setattr("web.auth.get_by_id", _loader)
    else:
        monkeypatch.setattr("web.auth.get_by_id", lambda user_id: None)

    from web.app import create_app

    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()
    if fake_user is not None:
        with client.session_transaction() as session:
            session["_user_id"] = str(fake_user["id"])
            session["_fresh"] = True
    return client


@pytest.fixture(autouse=True)
def _reset_shutdown_state():
    from appcore import shutdown_coordinator, task_recovery

    shutdown_coordinator.reset()
    with task_recovery._active_lock:
        task_recovery._active_tasks.clear()
    yield
    shutdown_coordinator.reset()
    with task_recovery._active_lock:
        task_recovery._active_tasks.clear()


def test_anonymous_redirects_to_login(monkeypatch):
    client = _build_fake_app(monkeypatch, fake_user=None)
    resp = client.get("/admin/runtime/active-tasks", follow_redirects=False)
    # Flask-Login default: redirect (302) to login_view for unauthenticated.
    assert resp.status_code in (302, 401)


def test_admin_role_is_forbidden(monkeypatch):
    fake = {"id": 5, "username": "alice", "role": "admin", "is_active": 1}
    client = _build_fake_app(monkeypatch, fake_user=fake)
    resp = client.get("/admin/runtime/active-tasks")
    # superadmin_required must reject role=admin.
    assert resp.status_code == 403


def test_role_user_is_forbidden(monkeypatch):
    fake = {"id": 6, "username": "bob", "role": "user", "is_active": 1}
    client = _build_fake_app(monkeypatch, fake_user=fake)
    resp = client.get("/admin/runtime/active-tasks")
    assert resp.status_code == 403


def test_superadmin_with_other_username_is_forbidden(monkeypatch):
    # is_superadmin requires both role=superadmin AND username=='admin'.
    fake = {"id": 7, "username": "rooty", "role": "superadmin", "is_active": 1}
    client = _build_fake_app(monkeypatch, fake_user=fake)
    resp = client.get("/admin/runtime/active-tasks")
    assert resp.status_code == 403


def test_superadmin_admin_username_returns_snapshot(monkeypatch):
    fake = {"id": 1, "username": "admin", "role": "superadmin", "is_active": 1}
    client = _build_fake_app(monkeypatch, fake_user=fake)
    resp = client.get("/admin/runtime/active-tasks")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert isinstance(payload, dict)
    assert payload["active_count"] == 0
    assert payload["active_tasks"] == []
    assert payload["shutting_down"] is False
    assert "scheduler_running" in payload
    assert "scheduler_jobs" in payload


def test_snapshot_reflects_registered_active_tasks(monkeypatch):
    from appcore import task_recovery

    fake = {"id": 1, "username": "admin", "role": "superadmin", "is_active": 1}
    task_recovery.register_active_task("translation", "task-A")
    task_recovery.register_active_task("image_translate", "task-B")
    monkeypatch.setattr(
        "appcore.task_state.get",
        lambda task_id: {
            "task-A": {"display_name": "Hello", "status": "running", "type": "translation"},
            "task-B": {"display_name": "Img", "status": "running", "type": "image_translate"},
        }.get(task_id),
    )

    client = _build_fake_app(monkeypatch, fake_user=fake)
    resp = client.get("/admin/runtime/active-tasks")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["active_count"] == 2
    pairs = {(t["project_type"], t["task_id"]) for t in payload["active_tasks"]}
    assert pairs == {("translation", "task-A"), ("image_translate", "task-B")}


def test_shutting_down_is_true_when_requested(monkeypatch):
    from appcore import shutdown_coordinator

    fake = {"id": 1, "username": "admin", "role": "superadmin", "is_active": 1}
    shutdown_coordinator.request_shutdown("test-shutdown")

    client = _build_fake_app(monkeypatch, fake_user=fake)
    resp = client.get("/admin/runtime/active-tasks")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["shutting_down"] is True
    assert payload["shutdown_reason"] == "test-shutdown"


def test_response_stable_when_scheduler_read_fails(monkeypatch):
    fake = {"id": 1, "username": "admin", "role": "superadmin", "is_active": 1}

    def _broken():
        raise RuntimeError("scheduler down")

    monkeypatch.setattr("appcore.scheduler.current_scheduler", _broken)

    client = _build_fake_app(monkeypatch, fake_user=fake)
    resp = client.get("/admin/runtime/active-tasks")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["scheduler_running"] is False
    assert payload["scheduler_jobs"] == []
