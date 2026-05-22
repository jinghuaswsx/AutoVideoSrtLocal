from __future__ import annotations

from pathlib import Path


def test_push_status_cache_scheduler_registers_two_minute_controlled_job():
    from appcore import push_status_cache_scheduler

    calls = []

    class FakeScheduler:
        def add_job(self, func, trigger, **kwargs):
            calls.append((func, trigger, kwargs))

    push_status_cache_scheduler.register(FakeScheduler())

    assert len(calls) == 1
    func, trigger, kwargs = calls[0]
    assert getattr(func, "__wrapped__", None) is push_status_cache_scheduler.tick_once
    assert trigger == "interval"
    assert kwargs["minutes"] == 2
    assert kwargs["id"] == "push_status_cache_refresh"
    assert kwargs["replace_existing"] is True
    assert kwargs["max_instances"] == 1


def test_push_status_cache_scheduler_tick_records_run(monkeypatch):
    from appcore import push_status_cache_scheduler

    events = []

    monkeypatch.setattr(
        push_status_cache_scheduler.scheduled_tasks,
        "start_run",
        lambda task_code: events.append(("start", task_code)) or 42,
    )
    monkeypatch.setattr(
        push_status_cache_scheduler.pushes,
        "refresh_push_status_cache",
        lambda limit=None: {"scanned": 2, "refreshed": 2, "status_counts": {"pending": 2}},
    )
    monkeypatch.setattr(
        push_status_cache_scheduler.scheduled_tasks,
        "finish_run",
        lambda run_id, status, summary=None, error_message=None: events.append(
            ("finish", run_id, status, summary, error_message)
        ),
    )

    summary = push_status_cache_scheduler.tick_once()

    assert summary["refreshed"] == 2
    assert events == [
        ("start", "push_status_cache_refresh"),
        (
            "finish",
            42,
            "success",
            {"scanned": 2, "refreshed": 2, "status_counts": {"pending": 2}},
            None,
        ),
    ]


def test_push_status_cache_scheduler_registered_in_app_scheduler():
    source = (Path(__file__).resolve().parents[1] / "appcore" / "scheduler.py").read_text(encoding="utf-8")

    assert "push_status_cache_scheduler" in source
    assert "push_status_cache_scheduler.register(_scheduler)" in source
