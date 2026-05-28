from __future__ import annotations

from pathlib import Path


def test_media_product_ad_status_cache_scheduler_registers_hourly_controlled_job():
    from appcore import media_product_ad_status_cache_scheduler

    calls = []

    class FakeScheduler:
        def add_job(self, func, trigger, **kwargs):
            calls.append((func, trigger, kwargs))

    media_product_ad_status_cache_scheduler.register(FakeScheduler())

    assert len(calls) == 1
    func, trigger, kwargs = calls[0]
    assert getattr(func, "__wrapped__", None) is media_product_ad_status_cache_scheduler.tick_once
    assert trigger == "interval"
    assert kwargs["hours"] == 1
    assert kwargs["id"] == "media_product_ad_status_cache_refresh"
    assert kwargs["replace_existing"] is True
    assert kwargs["max_instances"] == 1


def test_media_product_ad_status_cache_scheduler_tick_records_run(monkeypatch):
    from appcore import media_product_ad_status_cache_scheduler

    events = []

    monkeypatch.setattr(
        media_product_ad_status_cache_scheduler.scheduled_tasks,
        "start_run",
        lambda task_code: events.append(("start", task_code)) or 42,
    )
    monkeypatch.setattr(
        media_product_ad_status_cache_scheduler.media_product_ad_status_cache,
        "refresh_all",
        lambda: {"product_rows": 2, "lang_rows": 5},
    )
    monkeypatch.setattr(
        media_product_ad_status_cache_scheduler.scheduled_tasks,
        "finish_run",
        lambda run_id, status, summary=None, error_message=None: events.append(
            ("finish", run_id, status, summary, error_message)
        ),
    )

    summary = media_product_ad_status_cache_scheduler.tick_once()

    assert summary == {"product_rows": 2, "lang_rows": 5}
    assert events == [
        ("start", "media_product_ad_status_cache_refresh"),
        ("finish", 42, "success", {"product_rows": 2, "lang_rows": 5}, None),
    ]


def test_media_product_ad_status_cache_scheduler_registered_in_app_scheduler():
    source = (
        Path(__file__).resolve().parents[1] / "appcore" / "scheduler.py"
    ).read_text(encoding="utf-8")

    assert "media_product_ad_status_cache_scheduler" in source
    assert "media_product_ad_status_cache_scheduler.register(_scheduler)" in source
