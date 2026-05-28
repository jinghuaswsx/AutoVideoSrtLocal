import pytest
from appcore import tasks as tasks_svc


def test_api_update_assignee_missing_param(authed_client_no_db):
    rsp = authed_client_no_db.patch("/tasks/api/123/assignee", json={})
    assert rsp.status_code == 400
    assert "assignee_id 必填" in rsp.get_json()["error"]


def test_api_update_assignee_non_admin_forbidden(authed_user_client_no_db):
    rsp = authed_user_client_no_db.patch("/tasks/api/123/assignee", json={"assignee_id": 9})
    assert rsp.status_code == 403


def test_api_update_assignee_success(authed_client_no_db, monkeypatch):
    calls = []

    def mock_update_task_assignee(*, task_id, assignee_id, actor_user_id, is_admin):
        calls.append((task_id, assignee_id, actor_user_id, is_admin))

    monkeypatch.setattr(tasks_svc, "update_task_assignee", mock_update_task_assignee)

    rsp = authed_client_no_db.patch("/tasks/api/456/assignee", json={"assignee_id": 8})
    assert rsp.status_code == 200
    assert rsp.get_json()["ok"] is True
    assert len(calls) == 1
    assert calls[0][0] == 456
    assert calls[0][1] == 8
    assert calls[0][3] is True


def test_api_update_assignee_service_error_handling(authed_client_no_db, monkeypatch):
    def mock_update_task_assignee_raise(*args, **kwargs):
        raise tasks_svc.StateError("任务已是终态，不可更改")

    monkeypatch.setattr(tasks_svc, "update_task_assignee", mock_update_task_assignee_raise)

    rsp = authed_client_no_db.patch("/tasks/api/456/assignee", json={"assignee_id": 8})
    assert rsp.status_code == 400
    assert "任务已是终态，不可更改" in rsp.get_json()["error"]
