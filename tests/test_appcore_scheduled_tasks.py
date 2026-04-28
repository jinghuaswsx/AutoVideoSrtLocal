from datetime import datetime


def test_latest_failure_alert_only_returns_failed_latest_run(monkeypatch):
    from appcore import scheduled_tasks

    monkeypatch.setattr(
        scheduled_tasks,
        "latest_run",
        lambda task_code: {"id": 9, "task_code": task_code, "status": "failed"},
    )

    assert scheduled_tasks.latest_failure_alert() == {
        "id": 9,
        "task_code": "shopifyid",
        "status": "failed",
    }

    monkeypatch.setattr(
        scheduled_tasks,
        "latest_run",
        lambda task_code: {"id": 10, "task_code": task_code, "status": "success"},
    )

    assert scheduled_tasks.latest_failure_alert() is None


def test_normalize_row_decodes_summary_json():
    from appcore import scheduled_tasks

    row = scheduled_tasks._normalize_row(
        {
            "id": 1,
            "task_code": "shopifyid",
            "summary_json": '{"updated": 3, "fetched": 404}',
            "started_at": datetime(2026, 4, 25, 12, 10),
        }
    )

    assert row["summary"] == {"updated": 3, "fetched": 404}
    assert "summary_json" not in row


def test_task_definitions_include_tos_backup():
    from appcore import scheduled_tasks

    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}

    assert definitions["tos_backup"]["schedule"] == "每天 01:00"
