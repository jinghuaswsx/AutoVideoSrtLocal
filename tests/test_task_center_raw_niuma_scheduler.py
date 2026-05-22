def test_task_center_raw_niuma_scheduler_tick_records_run(monkeypatch):
    from appcore import task_center_raw_niuma_scheduler as scheduler

    events = []
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "start_run",
        lambda task_code: events.append(("start", task_code)) or 42,
    )
    monkeypatch.setattr(
        scheduler.task_raw_video_processing,
        "reconcile_inflight_niuma_processing",
        lambda: {"scanned": 1, "attached": 1},
    )
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "finish_run",
        lambda run_id, status, summary=None, error_message=None: events.append(
            ("finish", run_id, status, summary, error_message)
        ),
    )

    summary = scheduler.tick_once()

    assert summary == {"scanned": 1, "attached": 1}
    assert events == [
        ("start", "task_center_raw_niuma_watch"),
        ("finish", 42, "success", {"scanned": 1, "attached": 1}, None),
    ]


def test_task_center_raw_niuma_scheduler_registers_minute_job(monkeypatch):
    from appcore import task_center_raw_niuma_scheduler as scheduler

    calls = []
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "add_controlled_job",
        lambda scheduler_obj, task_code, func, trigger, **kwargs: calls.append(
            (scheduler_obj, task_code, func, trigger, kwargs)
        ),
    )
    scheduler_obj = object()

    scheduler.register(scheduler_obj)

    assert calls == [
        (
            scheduler_obj,
            "task_center_raw_niuma_watch",
            scheduler.tick_once,
            "interval",
            {
                "seconds": 60,
                "id": "task_center_raw_niuma_watch",
                "max_instances": 1,
                "coalesce": True,
                "replace_existing": True,
            },
        )
    ]
