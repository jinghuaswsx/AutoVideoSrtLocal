def test_task_center_auto_archive_scheduler_registers_daily_six_oclock_job(monkeypatch):
    from appcore import task_center_auto_archive_scheduler as scheduler

    calls = []

    def fake_add_controlled_job(sched, task_code, func, trigger, **kwargs):
        calls.append((sched, task_code, func, trigger, kwargs))

    monkeypatch.setattr(scheduler.scheduled_tasks, "add_controlled_job", fake_add_controlled_job)

    fake_scheduler = object()
    scheduler.register(fake_scheduler)

    assert calls == [
        (
            fake_scheduler,
            "task_center_auto_archive",
            scheduler.tick_once,
            "cron",
            {
                "hour": 6,
                "minute": 0,
                "id": "task_center_auto_archive",
                "replace_existing": True,
                "max_instances": 1,
                "coalesce": True,
            },
        )
    ]


def test_task_center_auto_archive_scheduler_tick_records_success(monkeypatch):
    from appcore import task_center_auto_archive_scheduler as scheduler

    calls = []
    summary = {"archived_children": 1, "archived_parents": 1, "errors": 0}

    monkeypatch.setattr(scheduler.scheduled_tasks, "start_run", lambda task_code: 42)
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "finish_run",
        lambda run_id, status, summary, error_message=None: calls.append(
            (run_id, status, summary, error_message)
        ),
    )
    monkeypatch.setattr(
        scheduler.tasks,
        "auto_archive_completed_pushed_tasks",
        lambda limit=None: summary,
    )

    assert scheduler.tick_once(limit=5) == summary
    assert calls == [(42, "success", summary, None)]


def test_task_center_auto_archive_scheduler_tick_marks_failed_when_items_error(monkeypatch):
    from appcore import task_center_auto_archive_scheduler as scheduler

    calls = []
    summary = {"archived_children": 0, "archived_parents": 0, "errors": 2}

    monkeypatch.setattr(scheduler.scheduled_tasks, "start_run", lambda task_code: 43)
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "finish_run",
        lambda run_id, status, summary, error_message=None: calls.append(
            (run_id, status, summary, error_message)
        ),
    )
    monkeypatch.setattr(
        scheduler.tasks,
        "auto_archive_completed_pushed_tasks",
        lambda limit=None: summary,
    )

    assert scheduler.tick_once() == summary
    assert calls == [(43, "failed", summary, "2 task(s) failed")]


def test_task_center_auto_archive_scheduler_registered_in_app_scheduler():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "appcore" / "scheduler.py").read_text(encoding="utf-8")

    assert "task_center_auto_archive_scheduler" in source
    assert "task_center_auto_archive_scheduler.register(_scheduler)" in source
