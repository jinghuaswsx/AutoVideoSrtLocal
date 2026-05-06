from __future__ import annotations


def test_admin_runtime_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.admin_runtime import (
        AdminRuntimeSnapshotResponse,
        admin_runtime_flask_response,
    )

    result = AdminRuntimeSnapshotResponse({"active_count": 0}, 200)

    with authed_client_no_db.application.app_context():
        response, status_code = admin_runtime_flask_response(result)

    assert status_code == 200
    assert response.get_json() == {"active_count": 0}


def test_build_active_tasks_snapshot_response_maps_runtime_state():
    from web.services.admin_runtime import build_active_tasks_snapshot_response

    class _Job:
        id = "job-1"
        name = "Heartbeat"
        next_run_time = None

    class _Scheduler:
        running = True

        @staticmethod
        def get_jobs():
            return [_Job()]

    result = build_active_tasks_snapshot_response(
        snapshot_active_tasks_fn=lambda: [{"project_type": "translation", "task_id": "task-1"}],
        is_shutdown_requested_fn=lambda: True,
        shutdown_reason_fn=lambda: "test-stop",
        current_scheduler_fn=lambda: _Scheduler(),
    )

    assert result.status_code == 200
    assert result.payload == {
        "shutting_down": True,
        "shutdown_reason": "test-stop",
        "active_count": 1,
        "active_tasks": [{"project_type": "translation", "task_id": "task-1"}],
        "scheduler_running": True,
        "scheduler_jobs": [{"id": "job-1", "name": "Heartbeat", "next_run_time": None}],
    }


def test_build_active_tasks_snapshot_response_survives_scheduler_failure():
    from web.services.admin_runtime import build_active_tasks_snapshot_response

    def _broken_scheduler():
        raise RuntimeError("scheduler down")

    result = build_active_tasks_snapshot_response(
        snapshot_active_tasks_fn=lambda: [],
        is_shutdown_requested_fn=lambda: False,
        shutdown_reason_fn=lambda: "",
        current_scheduler_fn=_broken_scheduler,
    )

    assert result.status_code == 200
    assert result.payload["scheduler_running"] is False
    assert result.payload["scheduler_jobs"] == []
