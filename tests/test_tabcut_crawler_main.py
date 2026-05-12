from __future__ import annotations

from types import SimpleNamespace


def test_run_collection_records_scheduled_task_success(monkeypatch, tmp_path):
    from tools.tabcut_crawler import main

    events = []
    output_dir = tmp_path / "tabcut-output"
    args = SimpleNamespace(
        mode="recent7",
        cdp_url="http://127.0.0.1:9227",
        output_dir=str(output_dir),
        days=30,
        pages=20,
        page_size=100,
        sort_field="video_sold_count",
        video_create_time_begin=None,
        video_create_time_end=None,
        min_interval_seconds=3.3,
        no_persist=False,
        no_record_run=False,
    )

    monkeypatch.setattr(main.scheduled_tasks, "start_run", lambda task_code: events.append(("start", task_code)) or 42)
    monkeypatch.setattr(
        main.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: events.append(("finish", run_id, kwargs)),
    )

    result = main.run_collection(
        args,
        collect_recent7_fn=lambda **kwargs: {"ok": True, "output_dir": str(output_dir), "goods_count": 450},
    )

    assert result == {"ok": True, "output_dir": str(output_dir), "goods_count": 450}
    assert events == [
        ("start", main.TASK_CODE),
        (
            "finish",
            42,
                {
                    "status": "success",
                    "summary": {"ok": True, "output_dir": str(output_dir), "goods_count": 450},
                    "error_message": None,
                    "output_file": str(output_dir),
                },
            ),
    ]


def test_run_collection_records_scheduled_task_failure(monkeypatch, tmp_path):
    from tools.tabcut_crawler import main

    events = []
    args = SimpleNamespace(
        mode="recent7",
        cdp_url="http://127.0.0.1:9227",
        output_dir=str(tmp_path),
        days=30,
        pages=20,
        page_size=100,
        sort_field="video_sold_count",
        video_create_time_begin=None,
        video_create_time_end=None,
        min_interval_seconds=3.3,
        no_persist=False,
        no_record_run=False,
    )

    monkeypatch.setattr(main.scheduled_tasks, "start_run", lambda task_code: events.append(("start", task_code)) or 7)
    monkeypatch.setattr(
        main.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: events.append(("finish", run_id, kwargs)),
    )

    def fail_collection(**kwargs):
        raise RuntimeError("tabcut cdp unavailable")

    try:
        main.run_collection(args, collect_recent7_fn=fail_collection)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected collection failure")

    assert events[0] == ("start", main.TASK_CODE)
    assert events[1][0:2] == ("finish", 7)
    assert events[1][2]["status"] == "failed"
    assert events[1][2]["error_message"] == "tabcut cdp unavailable"
