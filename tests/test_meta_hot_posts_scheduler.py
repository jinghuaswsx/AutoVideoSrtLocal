from datetime import datetime, timedelta
import time

from appcore.meta_hot_posts import scheduler
from appcore.meta_hot_posts.product_analysis import ProductAnalysisResult


def test_sync_hot_posts_fetches_until_target_count(monkeypatch):
    pages = []
    upserts = []
    queued = []

    class FakeClient:
        def fetch_page(self, *, page, **params):
            pages.append((page, params))
            start = (page - 1) * 100
            return {
                "items": [
                    {
                        "wedev_post_id": start + idx,
                        "product_url": f"https://example.com/products/{start + idx}",
                    }
                    for idx in range(100)
                ],
            }

    monkeypatch.setattr(scheduler.store, "upsert_hot_post", lambda item: upserts.append(item))
    monkeypatch.setattr(scheduler.store, "ensure_product_analysis", lambda url: queued.append(url))

    summary = scheduler.sync_hot_posts(client=FakeClient(), target_count=500, max_pages=20)

    assert [page for page, _params in pages] == [1, 2, 3, 4, 5]
    assert len(upserts) == 500
    assert len(queued) == 500
    assert summary["posts"] == 500
    assert summary["pages"] == 5


def test_sync_hot_posts_stops_when_upstream_returns_empty(monkeypatch):
    pages = []

    class FakeClient:
        def fetch_page(self, *, page, **params):
            pages.append(page)
            if page == 1:
                return {"items": [{"wedev_post_id": 1, "product_url": ""}]}
            return {"items": []}

    monkeypatch.setattr(scheduler.store, "upsert_hot_post", lambda item: None)
    monkeypatch.setattr(scheduler.store, "ensure_product_analysis", lambda url: None)

    summary = scheduler.sync_hot_posts(client=FakeClient(), target_count=500, max_pages=20)

    assert pages == [1, 2]
    assert summary["posts"] == 1


def test_sync_hot_posts_full_sync_uses_reported_total(monkeypatch):
    pages = []
    upserts = []
    queued = []

    class FakeClient:
        def fetch_page(self, *, page, **params):
            pages.append((page, params))
            if page <= 3:
                count = 30
            elif page == 4:
                count = 7
            else:
                count = 0
            start = (page - 1) * 30
            return {
                "total": 97,
                "size": 30,
                "items": [
                    {
                        "wedev_post_id": start + idx + 1,
                        "product_url": f"https://example.com/products/{start + idx + 1}",
                    }
                    for idx in range(count)
                ],
            }

    monkeypatch.setattr(scheduler.store, "upsert_hot_post", lambda item: upserts.append(item))
    monkeypatch.setattr(scheduler.store, "ensure_product_analysis", lambda url: queued.append(url))

    summary = scheduler.sync_hot_posts(client=FakeClient(), target_count=None, max_pages=20)

    assert [page for page, _params in pages] == [1, 2, 3, 4]
    assert len(upserts) == 97
    assert len(queued) == 97
    assert summary["posts"] == 97
    assert summary["reported_total"] == 97
    assert summary["page_size"] == 30
    assert summary["stop_reason"] == "reported_total_reached"


def test_sync_hot_posts_full_sync_stops_on_empty_page_before_reported_total(monkeypatch):
    pages = []

    class FakeClient:
        def fetch_page(self, *, page, **params):
            pages.append(page)
            if page == 1:
                return {
                    "total": 200,
                    "size": 30,
                    "items": [{"wedev_post_id": 1, "product_url": ""}],
                }
            return {"total": 200, "size": 30, "items": []}

    monkeypatch.setattr(scheduler.store, "upsert_hot_post", lambda item: None)
    monkeypatch.setattr(scheduler.store, "ensure_product_analysis", lambda url: None)

    summary = scheduler.sync_hot_posts(client=FakeClient(), target_count=None, max_pages=20)

    assert pages == [1, 2]
    assert summary["posts"] == 1
    assert summary["reported_total"] == 200
    assert summary["stop_reason"] == "empty_page"


def test_sync_hot_posts_full_sync_reports_max_pages_reached(monkeypatch):
    class FakeClient:
        def fetch_page(self, *, page, **params):
            return {
                "total": 1000,
                "size": 30,
                "items": [
                    {"wedev_post_id": page * 100 + idx, "product_url": ""}
                    for idx in range(30)
                ],
            }

    monkeypatch.setattr(scheduler.store, "upsert_hot_post", lambda item: None)
    monkeypatch.setattr(scheduler.store, "ensure_product_analysis", lambda url: None)

    summary = scheduler.sync_hot_posts(client=FakeClient(), target_count=None, max_pages=2)

    assert summary["pages"] == 2
    assert summary["posts"] == 60
    assert summary["reported_total"] == 1000
    assert summary["stop_reason"] == "max_pages_reached"


def test_register_schedules_daily_sync_analysis_translation_video_and_unified_analysis_queue(monkeypatch):
    calls = []
    now = datetime(2026, 5, 14, 18, 0, 0)

    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "add_controlled_job",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(scheduler, "_now", lambda: now)

    scheduler.register(object())

    sync_args, sync_kwargs = calls[0]
    analysis_args, analysis_kwargs = calls[1]
    translation_args, translation_kwargs = calls[2]
    video_args, video_kwargs = calls[3]
    queue_args, queue_kwargs = calls[4]
    startup_args, startup_kwargs = calls[5]
    tos_sync_args, tos_sync_kwargs = calls[6]
    assert sync_args[1] == "meta_hot_posts_sync_tick"
    assert sync_args[3] == "cron"
    assert sync_kwargs["hour"] == 7
    assert sync_kwargs["minute"] == 0
    assert analysis_args[1] == "meta_hot_posts_analysis_tick"
    assert analysis_args[3] == "interval"
    assert analysis_kwargs["minutes"] == 10
    assert translation_args[1] == "meta_hot_posts_translate_messages_tick"
    assert translation_args[3] == "interval"
    assert translation_kwargs["minutes"] == 10
    assert video_args[1] == "meta_hot_posts_video_localization_tick"
    assert video_args[3] == "interval"
    assert video_kwargs["minutes"] == 10
    assert "next_run_time" not in video_kwargs
    assert queue_args[1] == "meta_hot_posts_video_analysis_queue_tick"
    assert queue_args[2] == scheduler.video_analysis_queue_tick_once
    assert queue_args[3] == "interval"
    assert queue_kwargs["minutes"] == 10
    assert queue_kwargs["max_instances"] == 2
    assert startup_args[1] == "meta_hot_posts_video_localization_tick"
    assert startup_args[2] == scheduler.video_localization_startup_tick_once
    assert startup_args[3] == "date"
    assert video_kwargs["misfire_grace_time"] == 60
    assert startup_kwargs["id"] == "meta_hot_posts_video_localization_tick_startup"
    assert startup_kwargs["run_date"] == now + timedelta(seconds=5)
    assert tos_sync_args[1] == "meta_hot_posts_tos_video_sync_tick"
    assert tos_sync_args[3] == "interval"
    assert tos_sync_kwargs["minutes"] == 10
    assert tos_sync_kwargs["max_instances"] == 1
    assert len(calls) == 7


def test_video_analysis_queue_tick_once_defaults_to_560_second_window_with_hard_timeout(monkeypatch):
    captured = {}

    monkeypatch.setattr(scheduler, "_take_over_video_analysis_queue_singleton", lambda: {})
    monkeypatch.setattr(scheduler.scheduled_tasks, "start_run", lambda task_code: 42)
    monkeypatch.setattr(scheduler.scheduled_tasks, "finish_run", lambda *args, **kwargs: None)

    def fake_run(
        *,
        max_duration_seconds,
        user_id=None,
        run_id=None,
        per_item_delay_seconds,
        per_item_timeout_seconds,
        limit=None,
    ):
        captured["max_duration_seconds"] = max_duration_seconds
        captured["user_id"] = user_id
        captured["run_id"] = run_id
        captured["per_item_delay_seconds"] = per_item_delay_seconds
        captured["per_item_timeout_seconds"] = per_item_timeout_seconds
        captured["limit"] = limit
        return {"queued": 0, "scanned": 0, "done": 0, "failed": 0}

    monkeypatch.setattr(scheduler, "process_video_analysis_queue", fake_run)

    scheduler.video_analysis_queue_tick_once(user_id=9)

    assert captured["max_duration_seconds"] == 560
    assert captured["user_id"] == 9
    assert captured["run_id"] == 42
    assert captured["per_item_delay_seconds"] == 0
    assert captured["per_item_timeout_seconds"] == 40
    assert captured["limit"] is None


def test_video_analysis_queue_tick_once_replaces_previous_running_run(monkeypatch):
    started_at = datetime(2026, 5, 14, 10, 0, 0)
    events = []

    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "latest_running_run",
        lambda task_code: {"id": 33, "started_at": started_at},
    )
    monkeypatch.setattr(scheduler, "_now", lambda: started_at + timedelta(minutes=12))
    monkeypatch.setattr(
        scheduler.store,
        "reset_running_video_copyability_analyses",
        lambda: events.append(("reset_us",)) or 2,
    )
    monkeypatch.setattr(
        scheduler.store,
        "reset_running_europe_fit_assessments",
        lambda: events.append(("reset_europe",)) or 3,
    )
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: events.append(("finish", run_id, kwargs)),
    )
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "start_run",
        lambda task_code: events.append(("start", task_code)) or 44,
    )
    monkeypatch.setattr(
        scheduler,
        "process_video_analysis_queue",
        lambda *,
        max_duration_seconds,
        user_id=None,
        run_id=None,
        per_item_delay_seconds,
        per_item_timeout_seconds,
        limit=None: events.append(
            ("process", max_duration_seconds, run_id, per_item_delay_seconds, per_item_timeout_seconds, limit)
        )
        or {"scanned": 1, "done": 1, "failed": 0},
    )

    summary = scheduler.video_analysis_queue_tick_once()

    assert summary["running_run_replaced"] == 33
    assert summary["running_us_copyability_reset"] == 2
    assert summary["running_europe_fit_reset"] == 3
    assert events[0] == ("reset_us",)
    assert events[1] == ("reset_europe",)
    assert events[2][0] == "finish"
    assert events[2][2]["status"] == "failed"
    assert events[3] == ("start", scheduler.VIDEO_ANALYSIS_QUEUE_TASK_CODE)
    assert events[4] == ("process", 560, 44, 0, 40, None)


def test_video_localization_tick_once_defaults_to_30_seconds(monkeypatch):
    captured = {}

    monkeypatch.setattr(scheduler.scheduled_tasks, "latest_running_run", lambda task_code: None)
    monkeypatch.setattr(scheduler.scheduled_tasks, "start_run", lambda task_code: 42)
    monkeypatch.setattr(scheduler.scheduled_tasks, "finish_run", lambda *args, **kwargs: None)

    def fake_download_hot_post_videos(*, limit, min_delay_seconds):
        captured["limit"] = limit
        captured["min_delay_seconds"] = min_delay_seconds
        return {"scanned": 0, "downloaded": 0, "failed": 0}

    monkeypatch.setattr(scheduler.video_localization, "download_hot_post_videos", fake_download_hot_post_videos)

    scheduler.video_localization_tick_once()

    assert captured["limit"] == 30
    assert captured["min_delay_seconds"] == 30


def test_europe_fit_tick_once_defaults_to_30_materials(monkeypatch):
    captured = {}

    monkeypatch.setattr(scheduler.scheduled_tasks, "latest_running_run", lambda task_code: None)
    monkeypatch.setattr(scheduler.scheduled_tasks, "start_run", lambda task_code: 42)
    monkeypatch.setattr(scheduler.scheduled_tasks, "finish_run", lambda *args, **kwargs: None)

    def fake_assess(*, limit, user_id=None, run_id=None):
        captured["limit"] = limit
        captured["run_id"] = run_id
        return {"scanned": 0, "done": 0, "failed": 0}

    monkeypatch.setattr(scheduler, "assess_europe_fit_materials", fake_assess)

    scheduler.europe_fit_tick_once()

    assert captured["limit"] == 30
    assert captured["run_id"] == 42


def test_europe_fit_tick_once_replaces_previous_running_run(monkeypatch):
    started_at = datetime(2026, 5, 14, 10, 0, 0)
    events = []

    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "latest_running_run",
        lambda task_code: {"id": 31, "started_at": started_at},
    )
    monkeypatch.setattr(scheduler, "_now", lambda: started_at + timedelta(minutes=5))
    monkeypatch.setattr(
        scheduler.store,
        "reset_running_europe_fit_assessments",
        lambda: events.append(("reset_europe",)) or 4,
    )
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: events.append(("finish", run_id, kwargs)),
    )
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "start_run",
        lambda task_code: events.append(("start", task_code)) or 102,
    )
    monkeypatch.setattr(
        scheduler,
        "assess_europe_fit_materials",
        lambda *, limit, user_id=None, run_id=None: events.append(("assess", limit, run_id))
        or {"scanned": 1, "done": 1, "failed": 0},
    )

    summary = scheduler.europe_fit_tick_once(limit=1)

    assert summary["running_run_replaced"] == 31
    assert summary["running_europe_fit_reset"] == 4
    assert summary["running_age_seconds"] == 300
    assert summary["done"] == 1
    assert events[0] == ("reset_europe",)
    assert events[1][0] == "finish"
    assert events[1][1] == 31
    assert events[1][2]["status"] == "failed"
    assert "superseded by a new run" in events[1][2]["error_message"]
    assert events[2] == ("start", scheduler.EUROPE_FIT_TASK_CODE)
    assert events[3] == ("assess", 1, 102)


def test_process_video_analysis_queue_runs_us_before_europe_with_shared_delay(monkeypatch):
    events = []
    sleep_calls = []
    us_rows = [
        {
            "analysis_id": 1,
            "hot_post_id": 10,
            "local_video_path": "us-a.mp4",
            "analysis_status": "pending",
            "attempts": 0,
            "last_error": None,
        },
        {
            "analysis_id": 2,
            "hot_post_id": 11,
            "local_video_path": "us-b.mp4",
            "analysis_status": "pending",
            "attempts": 0,
            "last_error": None,
        },
    ]
    europe_rows = [
        {
            "id": 21,
            "local_video_path": "eu-a.mp4",
            "europe_fit_status": "pending",
            "europe_fit_attempts": 0,
            "europe_fit_last_error": None,
        }
    ]

    monkeypatch.setattr(scheduler, "resolve_billing_user_id", lambda user_id=None: 7)
    monkeypatch.setattr(scheduler.scheduled_tasks, "latest_running_run", lambda task_code: {"id": 42})
    monkeypatch.setattr(
        scheduler.store,
        "ensure_video_copyability_candidates",
        lambda: events.append(("ensure_us",)) or 2,
    )
    monkeypatch.setattr(
        scheduler.store,
        "ensure_europe_fit_candidates",
        lambda: events.append(("ensure_europe",)) or 1,
    )
    monkeypatch.setattr(
        scheduler.store,
        "next_pending_video_copyability_analyses",
        lambda limit: events.append(("select_us", limit)) or ([us_rows.pop(0)] if us_rows else []),
    )
    monkeypatch.setattr(
        scheduler.store,
        "next_pending_europe_fit_materials",
        lambda limit: events.append(("select_europe", limit)) or ([europe_rows.pop(0)] if europe_rows else []),
    )
    monkeypatch.setattr(
        scheduler.store,
        "mark_video_copyability_running",
        lambda analysis_id: events.append(("mark_us", analysis_id)),
    )
    monkeypatch.setattr(
        scheduler.store,
        "finish_video_copyability_analysis",
        lambda analysis_id, **kwargs: events.append(("finish_us", analysis_id, kwargs["error_message"])),
    )
    monkeypatch.setattr(
        scheduler.store,
        "mark_europe_fit_running",
        lambda post_id: events.append(("mark_europe", post_id)),
    )
    monkeypatch.setattr(
        scheduler.store,
        "finish_europe_fit_assessment",
        lambda post_id, **kwargs: events.append(("finish_europe", post_id, kwargs["error_message"])),
    )
    monkeypatch.setattr(
        scheduler.video_copyability,
        "analyze_video_copyability",
        lambda row, user_id=None: events.append(("analyze_us", row["analysis_id"], user_id))
        or {"overall_score": 88, "recommendation": "copy"},
    )
    monkeypatch.setattr(
        scheduler.europe_fit,
        "assess_material",
        lambda row, user_id=None: events.append(("analyze_europe", row["id"], user_id))
        or {"suitability_score": 90, "video_optimization": {}},
    )

    summary = scheduler.process_video_analysis_queue(
        limit=3,
        user_id=9,
        run_id=42,
        per_item_delay_seconds=30,
        sleep_fn=lambda seconds: sleep_calls.append(seconds),
    )

    assert summary["queued_us_copyability"] == 2
    assert summary["queued_europe_fit"] == 1
    assert summary["scanned"] == 3
    assert summary["done"] == 3
    assert summary["failed"] == 0
    assert summary["us_copyability_done"] == 2
    assert summary["europe_fit_done"] == 1
    assert events[:2] == [("ensure_us",), ("ensure_europe",)]
    assert [event[0] for event in events[2:]] == [
        "select_us",
        "mark_us",
        "analyze_us",
        "finish_us",
        "select_us",
        "mark_us",
        "analyze_us",
        "finish_us",
        "select_us",
        "select_europe",
        "mark_europe",
        "analyze_europe",
        "finish_europe",
    ]
    assert sleep_calls == [30, 30]


def test_process_video_analysis_queue_rebuilds_both_queues_before_selecting_items(monkeypatch):
    events = []
    rows = [
        {"analysis_id": 1, "hot_post_id": 10, "analysis_status": "pending", "attempts": 0},
        {"analysis_id": 2, "hot_post_id": 11, "analysis_status": "pending", "attempts": 0},
    ]

    monkeypatch.setattr(scheduler, "resolve_billing_user_id", lambda user_id=None: 7)
    monkeypatch.setattr(scheduler.scheduled_tasks, "latest_running_run", lambda task_code: {"id": 42})
    monkeypatch.setattr(
        scheduler.store,
        "ensure_video_copyability_candidates",
        lambda: events.append(("ensure_us",)) or 7,
    )
    monkeypatch.setattr(
        scheduler.store,
        "ensure_europe_fit_candidates",
        lambda: events.append(("ensure_europe",)) or 11,
    )
    monkeypatch.setattr(
        scheduler.store,
        "next_pending_video_copyability_analyses",
        lambda limit: events.append(("select_us", limit)) or ([rows.pop(0)] if rows else []),
    )
    monkeypatch.setattr(
        scheduler.store,
        "next_pending_europe_fit_materials",
        lambda limit: (_ for _ in ()).throw(AssertionError("US capacity should fill this round")),
    )
    monkeypatch.setattr(
        scheduler.store,
        "mark_video_copyability_running",
        lambda analysis_id: events.append(("mark_us", analysis_id)),
    )
    monkeypatch.setattr(
        scheduler.video_copyability,
        "analyze_video_copyability",
        lambda row, user_id=None: events.append(("analyze_us", row["analysis_id"], user_id))
        or {"overall_score": 88},
    )
    monkeypatch.setattr(
        scheduler.store,
        "finish_video_copyability_analysis",
        lambda analysis_id, **kwargs: events.append(("finish_us", analysis_id)),
    )

    summary = scheduler.process_video_analysis_queue(limit=2, user_id=7, run_id=42)

    assert summary["queued_us_copyability"] == 7
    assert summary["queued_europe_fit"] == 11
    assert summary["scanned"] == 2
    assert summary["us_copyability_done"] == 2
    assert events[:3] == [("ensure_us",), ("ensure_europe",), ("select_us", 1)]


def test_process_video_analysis_queue_stops_when_run_is_superseded(monkeypatch):
    events = []
    latest_calls = []

    monkeypatch.setattr(scheduler, "resolve_billing_user_id", lambda user_id=None: 7)
    monkeypatch.setattr(scheduler.store, "ensure_video_copyability_candidates", lambda: 0)
    monkeypatch.setattr(scheduler.store, "ensure_europe_fit_candidates", lambda: 0)
    monkeypatch.setattr(
        scheduler.store,
        "next_pending_video_copyability_analyses",
        lambda limit: [
            {
                "analysis_id": 1,
                "hot_post_id": 10,
                "local_video_path": "us-a.mp4",
                "analysis_status": "pending",
                "attempts": 0,
                "last_error": None,
            }
        ],
    )
    monkeypatch.setattr(scheduler.store, "next_pending_europe_fit_materials", lambda limit: [])
    monkeypatch.setattr(scheduler.store, "mark_video_copyability_running", lambda analysis_id: events.append(("mark", analysis_id)))
    monkeypatch.setattr(
        scheduler.video_copyability,
        "analyze_video_copyability",
        lambda row, user_id=None: events.append(("analyze", row["analysis_id"])) or {"overall_score": 88},
    )
    monkeypatch.setattr(
        scheduler.store,
        "finish_video_copyability_analysis",
        lambda analysis_id, **kwargs: events.append(("finish", analysis_id)),
    )

    def fake_latest(task_code):
        latest_calls.append(task_code)
        if len(latest_calls) >= 2:
            return {"id": 99}
        return {"id": 42}

    monkeypatch.setattr(scheduler.scheduled_tasks, "latest_running_run", fake_latest)

    summary = scheduler.process_video_analysis_queue(limit=1, user_id=7, run_id=42)

    assert summary["superseded"] is True
    assert summary["stop_reason"] == "newer_run_started"
    assert events == [("mark", 1)]


def test_process_video_analysis_queue_rate_limit_restores_us_state_and_stops_round(monkeypatch):
    events = []

    monkeypatch.setattr(scheduler, "resolve_billing_user_id", lambda user_id=None: 7)
    monkeypatch.setattr(scheduler.scheduled_tasks, "latest_running_run", lambda task_code: {"id": 42})
    monkeypatch.setattr(scheduler.store, "ensure_video_copyability_candidates", lambda: 1)
    monkeypatch.setattr(scheduler.store, "ensure_europe_fit_candidates", lambda: 0)
    monkeypatch.setattr(
        scheduler.store,
        "next_pending_video_copyability_analyses",
        lambda limit: [
            {
                "analysis_id": 1,
                "hot_post_id": 10,
                "local_video_path": "us-a.mp4",
                "analysis_status": "failed",
                "attempts": 2,
                "last_error": "old failure",
            }
        ],
    )
    monkeypatch.setattr(scheduler.store, "next_pending_europe_fit_materials", lambda limit: [])
    monkeypatch.setattr(scheduler.store, "mark_video_copyability_running", lambda analysis_id: events.append(("mark", analysis_id)))

    def raise_429(row, user_id=None):
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")

    monkeypatch.setattr(scheduler.video_copyability, "analyze_video_copyability", raise_429)
    monkeypatch.setattr(
        scheduler.store,
        "restore_video_copyability_analysis_state",
        lambda analysis_id, **kwargs: events.append(("restore", analysis_id, kwargs)),
        raising=False,
    )
    monkeypatch.setattr(
        scheduler.store,
        "finish_video_copyability_analysis",
        lambda analysis_id, **kwargs: (_ for _ in ()).throw(AssertionError("rate limit must not finish row")),
    )

    summary = scheduler.process_video_analysis_queue(limit=1, user_id=7, run_id=42)

    assert summary["failed"] == 1
    assert summary["rate_limited"] == 1
    assert summary["us_copyability_rate_limited"] == 1
    assert summary["suspended"] == 0
    assert summary["rate_limit_circuit_break"] is True
    assert summary["stop_reason"] == "rate_limited"
    assert events == [
        ("mark", 1),
        ("restore", 1, {"status": "failed", "attempts": 2, "last_error": "old failure"}),
    ]


def test_process_video_analysis_queue_rate_limit_restores_europe_state_and_stops_round(monkeypatch):
    events = []

    monkeypatch.setattr(scheduler, "resolve_billing_user_id", lambda user_id=None: 7)
    monkeypatch.setattr(scheduler.scheduled_tasks, "latest_running_run", lambda task_code: {"id": 42})
    monkeypatch.setattr(scheduler.store, "ensure_video_copyability_candidates", lambda: 0)
    monkeypatch.setattr(scheduler.store, "ensure_europe_fit_candidates", lambda: 1)
    monkeypatch.setattr(scheduler.store, "next_pending_video_copyability_analyses", lambda limit: [])
    monkeypatch.setattr(
        scheduler.store,
        "next_pending_europe_fit_materials",
        lambda limit: [
            {
                "id": 21,
                "local_video_path": "eu-a.mp4",
                "europe_fit_status": "failed",
                "europe_fit_attempts": 1,
                "europe_fit_last_error": "old europe failure",
            }
        ],
    )
    monkeypatch.setattr(
        scheduler.store,
        "mark_europe_fit_running",
        lambda post_id: events.append(("mark", post_id)),
    )

    def raise_429(row, user_id=None):
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")

    monkeypatch.setattr(scheduler.europe_fit, "assess_material", raise_429)
    monkeypatch.setattr(
        scheduler.store,
        "restore_europe_fit_assessment_state",
        lambda post_id, **kwargs: events.append(("restore", post_id, kwargs)),
        raising=False,
    )
    monkeypatch.setattr(
        scheduler.store,
        "finish_europe_fit_assessment",
        lambda post_id, **kwargs: (_ for _ in ()).throw(AssertionError("rate limit must not finish row")),
    )

    summary = scheduler.process_video_analysis_queue(limit=2, user_id=7, run_id=42)

    assert summary["scanned"] == 1
    assert summary["failed"] == 1
    assert summary["rate_limited"] == 1
    assert summary["europe_fit_rate_limited"] == 1
    assert summary["rate_limit_circuit_break"] is True
    assert summary["stop_reason"] == "rate_limited"
    assert events == [
        ("mark", 21),
        ("restore", 21, {"status": "failed", "attempts": 1, "last_error": "old europe failure"}),
    ]


def test_process_video_analysis_queue_hard_timeout_counts_as_us_failure(monkeypatch):
    events = []
    rows = [
        {
            "analysis_id": 1,
            "hot_post_id": 10,
            "local_video_path": "us-a.mp4",
            "analysis_status": "pending",
            "attempts": 0,
            "last_error": None,
        }
    ]

    monkeypatch.setattr(scheduler, "resolve_billing_user_id", lambda user_id=None: 7)
    monkeypatch.setattr(scheduler.scheduled_tasks, "latest_running_run", lambda task_code: {"id": 42})
    monkeypatch.setattr(scheduler.store, "ensure_video_copyability_candidates", lambda: 1)
    monkeypatch.setattr(scheduler.store, "ensure_europe_fit_candidates", lambda: 0)
    monkeypatch.setattr(
        scheduler.store,
        "next_pending_video_copyability_analyses",
        lambda limit: [rows.pop(0)] if rows else [],
    )
    monkeypatch.setattr(scheduler.store, "next_pending_europe_fit_materials", lambda limit: [])
    monkeypatch.setattr(scheduler.store, "mark_video_copyability_running", lambda analysis_id: events.append(("mark", analysis_id)))
    monkeypatch.setattr(
        scheduler.store,
        "restore_video_copyability_analysis_state",
        lambda analysis_id, **kwargs: (_ for _ in ()).throw(AssertionError("timeout must count as failure")),
        raising=False,
    )
    monkeypatch.setattr(
        scheduler.store,
        "finish_video_copyability_analysis",
        lambda analysis_id, **kwargs: events.append(("finish", analysis_id, kwargs)),
    )

    def slow_analyze(row, user_id=None):
        time.sleep(0.5)
        return {"overall_score": 88}

    monkeypatch.setattr(scheduler.video_copyability, "analyze_video_copyability", slow_analyze)

    started = time.monotonic()
    summary = scheduler.process_video_analysis_queue(
        limit=1,
        user_id=7,
        run_id=42,
        per_item_timeout_seconds=0.01,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.25
    assert summary["scanned"] == 1
    assert summary["failed"] == 1
    assert summary["timed_out"] == 1
    assert summary["us_copyability_timed_out"] == 1
    assert events[0] == ("mark", 1)
    assert events[1][0:2] == ("finish", 1)
    assert events[1][2]["result"] == {}
    assert events[1][2]["status_override"] == "failed"
    assert "timed out" in events[1][2]["error_message"]


def test_process_video_analysis_queue_hard_timeout_suspends_third_europe_attempt(monkeypatch):
    events = []
    rows = [
        {
            "id": 21,
            "local_video_path": "eu-a.mp4",
            "europe_fit_status": "failed",
            "europe_fit_attempts": 2,
            "europe_fit_last_error": "old europe failure",
        }
    ]

    monkeypatch.setattr(scheduler, "resolve_billing_user_id", lambda user_id=None: 7)
    monkeypatch.setattr(scheduler.scheduled_tasks, "latest_running_run", lambda task_code: {"id": 42})
    monkeypatch.setattr(scheduler.store, "ensure_video_copyability_candidates", lambda: 0)
    monkeypatch.setattr(scheduler.store, "ensure_europe_fit_candidates", lambda: 1)
    monkeypatch.setattr(scheduler.store, "next_pending_video_copyability_analyses", lambda limit: [])
    monkeypatch.setattr(
        scheduler.store,
        "next_pending_europe_fit_materials",
        lambda limit: [rows.pop(0)] if rows else [],
    )
    monkeypatch.setattr(scheduler.store, "mark_europe_fit_running", lambda post_id: events.append(("mark", post_id)))
    monkeypatch.setattr(
        scheduler.store,
        "restore_europe_fit_assessment_state",
        lambda post_id, **kwargs: (_ for _ in ()).throw(AssertionError("timeout must count as failure")),
        raising=False,
    )
    monkeypatch.setattr(
        scheduler.store,
        "finish_europe_fit_assessment",
        lambda post_id, **kwargs: events.append(("finish", post_id, kwargs)),
    )

    def slow_analyze(row, user_id=None):
        time.sleep(0.5)
        return {"suitability_score": 88, "video_optimization": {}}

    monkeypatch.setattr(scheduler.europe_fit, "assess_material", slow_analyze)

    started = time.monotonic()
    summary = scheduler.process_video_analysis_queue(
        limit=1,
        user_id=7,
        run_id=42,
        per_item_timeout_seconds=0.01,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.25
    assert summary["scanned"] == 1
    assert summary["failed"] == 1
    assert summary["suspended"] == 1
    assert summary["timed_out"] == 1
    assert summary["europe_fit_timed_out"] == 1
    assert summary["europe_fit_suspended"] == 1
    assert events[0] == ("mark", 21)
    assert events[1][0:2] == ("finish", 21)
    assert events[1][2]["status"] == "suspended"
    assert events[1][2]["result"] == {}
    assert events[1][2]["video_optimization"] == {}
    assert "timed out" in events[1][2]["error_message"]


def test_process_video_analysis_queue_suspends_after_third_us_attempt(monkeypatch):
    events = []

    monkeypatch.setattr(scheduler, "resolve_billing_user_id", lambda user_id=None: 7)
    monkeypatch.setattr(scheduler.scheduled_tasks, "latest_running_run", lambda task_code: {"id": 42})
    monkeypatch.setattr(scheduler.store, "ensure_video_copyability_candidates", lambda: 1)
    monkeypatch.setattr(scheduler.store, "ensure_europe_fit_candidates", lambda: 0)
    monkeypatch.setattr(
        scheduler.store,
        "next_pending_video_copyability_analyses",
        lambda limit: [{"analysis_id": 1, "hot_post_id": 10, "local_video_path": "us-a.mp4", "attempts": 2}],
    )
    monkeypatch.setattr(scheduler.store, "next_pending_europe_fit_materials", lambda limit: [])
    monkeypatch.setattr(scheduler.store, "mark_video_copyability_running", lambda analysis_id: events.append(("mark", analysis_id)))
    monkeypatch.setattr(
        scheduler.video_copyability,
        "analyze_video_copyability",
        lambda row, user_id=None: (_ for _ in ()).throw(RuntimeError("model returned empty response")),
    )
    monkeypatch.setattr(
        scheduler.store,
        "finish_video_copyability_analysis",
        lambda analysis_id, **kwargs: events.append(("finish", analysis_id, kwargs)),
    )

    summary = scheduler.process_video_analysis_queue(limit=1, user_id=7, run_id=42)

    assert summary["failed"] == 1
    assert summary["suspended"] == 1
    assert summary["us_copyability_suspended"] == 1
    assert events[1][2]["status_override"] == "suspended"


def test_process_video_analysis_queue_suspends_after_third_europe_attempt(monkeypatch):
    events = []

    monkeypatch.setattr(scheduler, "resolve_billing_user_id", lambda user_id=None: 7)
    monkeypatch.setattr(scheduler.scheduled_tasks, "latest_running_run", lambda task_code: {"id": 42})
    monkeypatch.setattr(scheduler.store, "ensure_video_copyability_candidates", lambda: 0)
    monkeypatch.setattr(scheduler.store, "ensure_europe_fit_candidates", lambda: 1)
    monkeypatch.setattr(scheduler.store, "next_pending_video_copyability_analyses", lambda limit: [])
    monkeypatch.setattr(
        scheduler.store,
        "next_pending_europe_fit_materials",
        lambda limit: [{"id": 21, "local_video_path": "eu-a.mp4", "europe_fit_attempts": 2}],
    )
    monkeypatch.setattr(scheduler.store, "mark_europe_fit_running", lambda post_id: events.append(("mark", post_id)))
    monkeypatch.setattr(
        scheduler.europe_fit,
        "assess_material",
        lambda row, user_id=None: (_ for _ in ()).throw(RuntimeError("bad json")),
    )
    monkeypatch.setattr(
        scheduler.store,
        "finish_europe_fit_assessment",
        lambda post_id, **kwargs: events.append(("finish", post_id, kwargs)),
    )

    summary = scheduler.process_video_analysis_queue(limit=1, user_id=7, run_id=42)

    assert summary["failed"] == 1
    assert summary["suspended"] == 1
    assert summary["europe_fit_suspended"] == 1
    assert events[1][2]["status"] == "suspended"


def test_process_video_analysis_queue_idles_without_billing_user_when_empty(monkeypatch):
    monkeypatch.setattr(
        scheduler,
        "resolve_billing_user_id",
        lambda user_id=None: (_ for _ in ()).throw(AssertionError("empty queue should idle")),
    )
    monkeypatch.setattr(scheduler.store, "ensure_video_copyability_candidates", lambda: 0)
    monkeypatch.setattr(scheduler.store, "ensure_europe_fit_candidates", lambda: 0)
    monkeypatch.setattr(scheduler.store, "next_pending_video_copyability_analyses", lambda limit: [])
    monkeypatch.setattr(scheduler.store, "next_pending_europe_fit_materials", lambda limit: [])

    summary = scheduler.process_video_analysis_queue(limit=10, user_id=None, run_id=42)

    assert summary["scanned"] == 0
    assert summary["done"] == 0
    assert summary["failed"] == 0
    assert summary["queued_us_copyability"] == 0
    assert summary["queued_europe_fit"] == 0


def test_analysis_tick_once_defaults_to_30_products_with_20_second_spacing(monkeypatch):
    captured = {}

    def fake_analyze_pending_products(*, limit, user_id=None, per_item_delay_seconds):
        captured["limit"] = limit
        captured["per_item_delay_seconds"] = per_item_delay_seconds
        return {"scanned": 0, "done": 0, "failed": 0}

    monkeypatch.setattr(scheduler.scheduled_tasks, "start_run", lambda task_code: 42)
    monkeypatch.setattr(scheduler.scheduled_tasks, "finish_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler.scheduled_tasks, "latest_running_run", lambda task_code: None)
    monkeypatch.setattr(scheduler, "analyze_pending_products", fake_analyze_pending_products)

    scheduler.analysis_tick_once()

    assert captured["limit"] == 30
    assert captured["per_item_delay_seconds"] == 20


def test_translation_tick_once_defaults_to_30_messages_with_no_spacing(monkeypatch):
    captured = {}

    def fake_translate_pending_messages(*, limit, user_id=None, per_item_delay_seconds):
        captured["limit"] = limit
        captured["per_item_delay_seconds"] = per_item_delay_seconds
        return {"scanned": 0, "done": 0, "failed": 0}

    monkeypatch.setattr(scheduler.scheduled_tasks, "start_run", lambda task_code: 42)
    monkeypatch.setattr(scheduler.scheduled_tasks, "finish_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler.scheduled_tasks, "latest_running_run", lambda task_code: None)
    monkeypatch.setattr(scheduler, "translate_pending_messages", fake_translate_pending_messages)

    scheduler.translation_tick_once()

    assert captured["limit"] == 30
    assert captured["per_item_delay_seconds"] == 0


def test_analysis_tick_once_skips_when_recent_run_is_still_running(monkeypatch):
    started_at = datetime(2026, 5, 13, 10, 30, 0)

    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "latest_running_run",
        lambda task_code: {"id": 10, "started_at": started_at},
    )
    monkeypatch.setattr(scheduler, "_now", lambda: started_at + timedelta(minutes=20))
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "start_run",
        lambda task_code: (_ for _ in ()).throw(AssertionError("new run must not start")),
    )

    summary = scheduler.analysis_tick_once()

    assert summary["skipped"] is True
    assert summary["reason"] == "previous_run_still_running"
    assert summary["running_run_id"] == 10


def test_analysis_tick_once_marks_stale_running_run_failed_then_starts(monkeypatch):
    started_at = datetime(2026, 5, 13, 10, 0, 0)
    events = []

    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "latest_running_run",
        lambda task_code: {"id": 10, "started_at": started_at},
    )
    monkeypatch.setattr(scheduler, "_now", lambda: started_at + timedelta(hours=1, minutes=2))
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: events.append(("finish", run_id, kwargs)),
    )
    monkeypatch.setattr(
        scheduler.store,
        "reset_stale_running_product_analyses",
        lambda older_than_seconds: events.append(("reset_products", older_than_seconds)) or 3,
    )
    monkeypatch.setattr(scheduler.scheduled_tasks, "start_run", lambda task_code: 99)
    monkeypatch.setattr(
        scheduler,
        "analyze_pending_products",
        lambda *, limit, user_id=None, per_item_delay_seconds: {"scanned": 0, "done": 0, "failed": 0},
    )

    summary = scheduler.analysis_tick_once()

    assert summary["stale_run_replaced"] == 10
    assert summary["stale_products_reset"] == 3
    assert ("reset_products", 3600) in events
    stale_finish = next(event for event in events if event[0] == "finish" and event[1] == 10)
    assert stale_finish[0] == "finish"
    assert stale_finish[1] == 10
    assert stale_finish[2]["status"] == "failed"
    assert "exceeded 3600s" in stale_finish[2]["error_message"]


def test_translation_tick_once_marks_stale_running_run_failed_then_starts(monkeypatch):
    started_at = datetime(2026, 5, 13, 10, 0, 0)
    events = []

    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "latest_running_run",
        lambda task_code: {"id": 12, "started_at": started_at},
    )
    monkeypatch.setattr(scheduler, "_now", lambda: started_at + timedelta(hours=1, minutes=2))
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: events.append(("finish", run_id, kwargs)),
    )
    monkeypatch.setattr(
        scheduler.store,
        "reset_stale_running_message_translations",
        lambda older_than_seconds: events.append(("reset_messages", older_than_seconds)) or 4,
    )
    monkeypatch.setattr(scheduler.scheduled_tasks, "start_run", lambda task_code: 100)
    monkeypatch.setattr(
        scheduler,
        "translate_pending_messages",
        lambda *, limit, user_id=None, per_item_delay_seconds: {"scanned": 0, "done": 0, "failed": 0},
    )

    summary = scheduler.translation_tick_once()

    assert summary["stale_run_replaced"] == 12
    assert summary["stale_messages_reset"] == 4
    assert ("reset_messages", 3600) in events
    stale_finish = next(event for event in events if event[0] == "finish" and event[1] == 12)
    assert stale_finish[2]["status"] == "failed"
    assert "exceeded 3600s" in stale_finish[2]["error_message"]


def test_video_localization_tick_once_skips_when_running_run_exists(monkeypatch):
    started_at = datetime(2026, 5, 14, 10, 0, 0)

    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "latest_running_run",
        lambda task_code: {"id": 21, "started_at": started_at},
    )
    monkeypatch.setattr(scheduler, "_now", lambda: started_at + timedelta(minutes=5))
    monkeypatch.setattr(
        scheduler.store,
        "reset_running_local_videos",
        lambda: (_ for _ in ()).throw(AssertionError("regular interval must not reset running videos")),
    )
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "start_run",
        lambda task_code: (_ for _ in ()).throw(AssertionError("new run must not start")),
    )

    summary = scheduler.video_localization_tick_once()

    assert summary["skipped"] is True
    assert summary["reason"] == "previous_run_still_running"
    assert summary["running_run_id"] == 21
    assert summary["running_age_seconds"] == 300


def test_video_localization_startup_tick_once_replaces_running_run(monkeypatch):
    started_at = datetime(2026, 5, 14, 10, 0, 0)
    events = []

    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "latest_running_run",
        lambda task_code: {"id": 21, "started_at": started_at},
    )
    monkeypatch.setattr(scheduler, "_now", lambda: started_at + timedelta(minutes=5))
    monkeypatch.setattr(
        scheduler.store,
        "reset_running_local_videos",
        lambda: events.append(("reset_videos",)) or 2,
    )
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: events.append(("finish", run_id, kwargs)),
    )
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "start_run",
        lambda task_code: events.append(("start", task_code)) or 101,
    )
    monkeypatch.setattr(
        scheduler.video_localization,
        "download_hot_post_videos",
        lambda *, limit, min_delay_seconds: events.append(("download", limit, min_delay_seconds))
        or {"scanned": 1, "downloaded": 1, "failed": 0},
    )

    summary = scheduler.video_localization_startup_tick_once(limit=1, min_delay_seconds=30)

    assert summary["running_run_replaced"] == 21
    assert summary["running_videos_reset"] == 2
    assert summary["running_age_seconds"] == 300
    assert summary["downloaded"] == 1
    assert "skipped" not in summary
    assert events[0] == ("reset_videos",)
    assert events[1][0] == "finish"
    assert events[1][1] == 21
    assert events[1][2]["status"] == "failed"
    assert "superseded by a new run" in events[1][2]["error_message"]
    assert events[2] == ("start", scheduler.VIDEO_LOCALIZATION_TASK_CODE)
    assert events[3] == ("download", 1, 30)


def test_translate_pending_messages_translates_and_saves(monkeypatch):
    finished = []
    marked = []
    sleep_calls = []

    monkeypatch.setattr(scheduler, "resolve_billing_user_id", lambda user_id=None: 7)
    monkeypatch.setattr(
        scheduler.store,
        "next_pending_message_translations",
        lambda limit: [
            {"id": 1, "message_html": "<p>Deep Clean.</p>"},
            {"id": 2, "message_html": "<p>Bright Garden.</p>"},
        ],
    )
    monkeypatch.setattr(scheduler.store, "mark_message_translation_running", lambda post_id: marked.append(post_id))
    monkeypatch.setattr(
        scheduler.message_translation,
        "translate_message_html",
        lambda message_html, user_id=None: f"中文:{message_html}",
    )
    monkeypatch.setattr(
        scheduler.store,
        "finish_message_translation",
        lambda post_id, **kwargs: finished.append((post_id, kwargs)),
    )

    summary = scheduler.translate_pending_messages(
        limit=2,
        user_id=9,
        per_item_delay_seconds=3,
        sleep_fn=lambda seconds: sleep_calls.append(seconds),
    )

    assert summary == {"scanned": 2, "done": 2, "failed": 0}
    assert marked == [1, 2]
    assert finished[0] == (1, {"translated_html": "中文:<p>Deep Clean.</p>", "error_message": None})
    assert finished[1] == (2, {"translated_html": "中文:<p>Bright Garden.</p>", "error_message": None})
    assert sleep_calls == [3]


def test_translate_pending_messages_records_failures(monkeypatch):
    finished = []

    monkeypatch.setattr(scheduler, "resolve_billing_user_id", lambda user_id=None: 7)
    monkeypatch.setattr(
        scheduler.store,
        "next_pending_message_translations",
        lambda limit: [{"id": 1, "message_html": "<p>Deep Clean.</p>"}],
    )
    monkeypatch.setattr(scheduler.store, "mark_message_translation_running", lambda post_id: None)

    def fail_translate(*args, **kwargs):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(scheduler.message_translation, "translate_message_html", fail_translate)
    monkeypatch.setattr(
        scheduler.store,
        "finish_message_translation",
        lambda post_id, **kwargs: finished.append((post_id, kwargs)),
    )

    summary = scheduler.translate_pending_messages(limit=1, user_id=9)

    assert summary == {"scanned": 1, "done": 0, "failed": 1}
    assert finished == [(1, {"translated_html": None, "error_message": "provider failed"})]


def test_assess_europe_fit_materials_stops_when_run_is_superseded(monkeypatch):
    finished = []
    marked = []
    latest_calls = []

    monkeypatch.setattr(scheduler, "resolve_billing_user_id", lambda user_id=None: 7)
    monkeypatch.setattr(
        scheduler.store,
        "next_pending_europe_fit_materials",
        lambda limit: [
            {"id": 1, "local_video_path": "meta_hot_posts/videos/1.mp4"},
            {"id": 2, "local_video_path": "meta_hot_posts/videos/2.mp4"},
        ],
    )
    monkeypatch.setattr(scheduler.store, "mark_europe_fit_running", lambda post_id: marked.append(post_id))
    monkeypatch.setattr(
        scheduler.store,
        "finish_europe_fit_assessment",
        lambda post_id, **kwargs: finished.append((post_id, kwargs)),
    )
    monkeypatch.setattr(
        scheduler.europe_fit,
        "assess_material",
        lambda row, user_id=None: {"suitability_score": 90, "video_optimization": {}},
    )

    def fake_latest(task_code):
        latest_calls.append(task_code)
        if len(latest_calls) >= 2:
            return {"id": 999}
        return {"id": 42}

    monkeypatch.setattr(scheduler.scheduled_tasks, "latest_running_run", fake_latest)

    summary = scheduler.assess_europe_fit_materials(limit=2, user_id=7, run_id=42)

    assert summary == {
        "scanned": 1,
        "done": 0,
        "failed": 0,
        "superseded": True,
        "stop_reason": "newer_run_started",
    }
    assert marked == [1]
    assert finished == []


def test_analyze_pending_products_keeps_product_result_when_category_fails(monkeypatch):
    finished = []

    monkeypatch.setattr(
        scheduler.store,
        "next_pending_product_analyses",
        lambda limit: [{"id": 7, "product_url": "https://example.com/products/socket"}],
    )
    monkeypatch.setattr(scheduler.store, "mark_analysis_running", lambda analysis_id: None)
    monkeypatch.setattr(
        scheduler.product_analysis,
        "fetch_product_analysis",
        lambda product_url: ProductAnalysisResult(
            title="Flexible Socket Extension",
            main_image_url="https://example.com/socket.jpg",
            price_min=19.99,
            price_max=29.99,
            currency="USD",
            skus=[{"sku": "SOCKET-1", "title": "Single", "price": 19.99, "currency": "USD"}],
        ),
    )

    def fail_category(**kwargs):
        finished.append(("category_kwargs", kwargs))
        raise ValueError("invalid llm json")

    monkeypatch.setattr(scheduler.product_analysis, "categorize_product", fail_category)
    monkeypatch.setattr(
        scheduler.store,
        "finish_analysis",
        lambda analysis_id, **kwargs: finished.append((analysis_id, kwargs)),
    )

    summary = scheduler.analyze_pending_products(limit=1, user_id=1)

    assert summary == {"scanned": 1, "done": 1, "failed": 0, "category_failed": 1}
    assert finished[0][0] == "category_kwargs"
    assert finished[0][1]["user_id"] == 1
    assert finished[1][0] == 7
    payload = finished[1][1]
    assert payload["status"] == "done"
    assert payload["result"]["title"] == "Flexible Socket Extension"
    assert payload["result"]["price_min"] == 19.99
    assert payload["category"]["category"] is None
    assert payload["category"]["provider"] == "openrouter"
    assert payload["category"]["model"] == "google/gemini-3.1-flash-lite"
    assert "invalid llm json" in payload["error_message"]


def test_analyze_pending_products_stops_after_global_category_provider_error(monkeypatch):
    finished = []
    marked = []

    monkeypatch.setattr(
        scheduler.store,
        "next_pending_product_analyses",
        lambda limit: [
            {"id": 7, "product_url": "https://example.com/products/socket"},
            {"id": 8, "product_url": "https://example.com/products/lamp"},
        ],
    )
    monkeypatch.setattr(scheduler.store, "mark_analysis_running", lambda analysis_id: marked.append(analysis_id))
    monkeypatch.setattr(
        scheduler.product_analysis,
        "fetch_product_analysis",
        lambda product_url: ProductAnalysisResult(
            title="Flexible Socket Extension",
            main_image_url="https://example.com/socket.jpg",
            price_min=19.99,
            price_max=29.99,
            currency="USD",
            skus=[],
        ),
    )

    def fail_category(**kwargs):
        raise RuntimeError("429 RESOURCE_EXHAUSTED. Please try again later.")

    monkeypatch.setattr(scheduler.product_analysis, "categorize_product", fail_category)
    monkeypatch.setattr(
        scheduler.store,
        "finish_analysis",
        lambda analysis_id, **kwargs: finished.append((analysis_id, kwargs)),
    )

    summary = scheduler.analyze_pending_products(limit=100, user_id=1)

    assert summary == {
        "scanned": 1,
        "done": 1,
        "failed": 0,
        "category_failed": 1,
        "stopped": True,
        "stop_reason": "global_category_provider_error",
    }
    assert marked == [7]
    assert [item[0] for item in finished] == [7]


def test_analyze_pending_products_waits_between_items(monkeypatch):
    sleep_calls = []

    monkeypatch.setattr(
        scheduler.store,
        "next_pending_product_analyses",
        lambda limit: [
            {"id": 7, "product_url": "https://example.com/products/socket"},
            {"id": 8, "product_url": "https://example.com/products/lamp"},
        ],
    )
    monkeypatch.setattr(scheduler.store, "mark_analysis_running", lambda analysis_id: None)
    monkeypatch.setattr(
        scheduler.product_analysis,
        "fetch_product_analysis",
        lambda product_url: ProductAnalysisResult(
            title="Flexible Socket Extension",
            main_image_url="https://example.com/socket.jpg",
            price_min=19.99,
            price_max=29.99,
            currency="USD",
            skus=[],
        ),
    )
    monkeypatch.setattr(
        scheduler.product_analysis,
        "categorize_product",
        lambda **kwargs: {
            "category": "Tools & Hardware",
            "confidence": 1.0,
            "reason": "",
            "provider": "gemini_vertex_adc",
            "model": "gemini-3.1-flash-lite-preview",
            "raw_response": {"text": "Tools & Hardware"},
        },
    )
    monkeypatch.setattr(scheduler.store, "finish_analysis", lambda analysis_id, **kwargs: None)

    summary = scheduler.analyze_pending_products(
        limit=2,
        user_id=1,
        per_item_delay_seconds=20,
        sleep_fn=lambda seconds: sleep_calls.append(seconds),
    )

    assert summary == {"scanned": 2, "done": 2, "failed": 0, "category_failed": 0}
    assert sleep_calls == [20]


def test_reanalyze_categories_uses_saved_title_without_fetching_product_page(monkeypatch):
    finished = []

    monkeypatch.setattr(
        scheduler.store,
        "next_category_reanalysis_candidates",
        lambda limit: [{"id": 7, "product_title": "Portable Blender"}],
    )
    monkeypatch.setattr(
        scheduler.product_analysis,
        "fetch_product_analysis",
        lambda product_url: (_ for _ in ()).throw(AssertionError("must not fetch product page")),
    )
    monkeypatch.setattr(
        scheduler.product_analysis,
        "categorize_product",
        lambda **kwargs: {
            "category": "Kitchenware",
            "confidence": 1.0,
            "reason": "Title maps to kitchen item.",
            "provider": "gemini_vertex_adc",
            "model": "gemini-3.1-flash-lite-preview",
            "raw_response": {"text": "Kitchenware"},
        },
    )
    monkeypatch.setattr(
        scheduler.store,
        "finish_category_reanalysis",
        lambda analysis_id, **kwargs: finished.append((analysis_id, kwargs)),
    )

    summary = scheduler.reanalyze_categories(limit=100, user_id=1)

    assert summary == {"scanned": 1, "done": 1, "failed": 0}
    assert finished[0][0] == 7
    assert finished[0][1]["category"]["category"] == "Kitchenware"
    assert finished[0][1]["error_message"] is None


def test_reanalyze_categories_marks_openrouter_model_even_when_category_call_fails(monkeypatch):
    finished = []

    monkeypatch.setattr(
        scheduler.store,
        "next_category_reanalysis_candidates",
        lambda limit: [{"id": 7, "product_title": "Portable Blender"}],
    )

    def fail_category(**kwargs):
        raise RuntimeError("openrouter unavailable")

    monkeypatch.setattr(scheduler.product_analysis, "categorize_product", fail_category)
    monkeypatch.setattr(
        scheduler.store,
        "finish_category_reanalysis",
        lambda analysis_id, **kwargs: finished.append((analysis_id, kwargs)),
    )

    summary = scheduler.reanalyze_categories(limit=100, user_id=1)

    assert summary == {"scanned": 1, "done": 0, "failed": 1}
    assert finished[0][1]["category"]["provider"] == "openrouter"
    assert finished[0][1]["category"]["model"] == "google/gemini-3.1-flash-lite"
    assert "openrouter unavailable" in finished[0][1]["error_message"]


def test_reanalyze_categories_waits_between_items(monkeypatch):
    sleep_calls = []

    monkeypatch.setattr(
        scheduler.store,
        "next_category_reanalysis_candidates",
        lambda limit: [
            {"id": 7, "product_title": "Portable Blender"},
            {"id": 8, "product_title": "Garden Light"},
        ],
    )
    monkeypatch.setattr(
        scheduler.product_analysis,
        "categorize_product",
        lambda **kwargs: {
            "category": "Kitchenware",
            "confidence": 1.0,
            "reason": "",
            "provider": "gemini_vertex_adc",
            "model": "gemini-3.1-flash-lite-preview",
            "raw_response": {"text": "Kitchenware"},
        },
    )
    monkeypatch.setattr(scheduler.store, "finish_category_reanalysis", lambda analysis_id, **kwargs: None)

    summary = scheduler.reanalyze_categories(
        limit=2,
        user_id=1,
        per_item_delay_seconds=20,
        sleep_fn=lambda seconds: sleep_calls.append(seconds),
    )

    assert summary == {"scanned": 2, "done": 2, "failed": 0}
    assert sleep_calls == [20]


def test_reanalyze_categories_can_run_with_concurrency(monkeypatch):
    import threading
    import time

    active = 0
    max_active = 0
    lock = threading.Lock()
    finished = []
    sleep_calls = []

    monkeypatch.setattr(
        scheduler.store,
        "next_category_reanalysis_candidates",
        lambda limit: [
            {"id": 7, "product_title": "Portable Blender"},
            {"id": 8, "product_title": "Garden Light"},
            {"id": 9, "product_title": "Phone Stand"},
            {"id": 10, "product_title": "Body Scrub"},
        ],
    )

    def categorize_product(**kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return {
            "category": "Kitchenware",
            "confidence": 1.0,
            "reason": "",
            "provider": "openrouter",
            "model": "google/gemini-3.1-flash-lite-preview",
            "raw_response": {"text": "Kitchenware"},
        }

    monkeypatch.setattr(scheduler.product_analysis, "categorize_product", categorize_product)
    monkeypatch.setattr(
        scheduler.store,
        "finish_category_reanalysis",
        lambda analysis_id, **kwargs: finished.append((analysis_id, kwargs)),
    )

    summary = scheduler.reanalyze_categories(
        limit=4,
        user_id=1,
        concurrency=3,
        per_item_delay_seconds=20,
        sleep_fn=lambda seconds: sleep_calls.append(seconds),
    )

    assert summary == {"scanned": 4, "done": 4, "failed": 0}
    assert max_active >= 2
    assert sorted(item[0] for item in finished) == [7, 8, 9, 10]
    assert sleep_calls == []


def test_reanalyze_categories_stops_after_global_adc_provider_error(monkeypatch):
    finished = []

    monkeypatch.setattr(
        scheduler.store,
        "next_category_reanalysis_candidates",
        lambda limit: [
            {"id": 7, "product_title": "Portable Blender"},
            {"id": 8, "product_title": "Garden Light"},
        ],
    )

    def fail_category(**kwargs):
        raise RuntimeError("Your default credentials were not found.")

    monkeypatch.setattr(scheduler.product_analysis, "categorize_product", fail_category)
    monkeypatch.setattr(
        scheduler.store,
        "finish_category_reanalysis",
        lambda analysis_id, **kwargs: finished.append((analysis_id, kwargs)),
    )

    summary = scheduler.reanalyze_categories(limit=100, user_id=1)

    assert summary == {
        "scanned": 1,
        "done": 0,
        "failed": 1,
        "stopped": True,
        "stop_reason": "global_category_provider_error",
    }
    assert [item[0] for item in finished] == [7]


def test_reanalyze_categories_stops_after_vertex_resource_exhausted(monkeypatch):
    finished = []

    monkeypatch.setattr(
        scheduler.store,
        "next_category_reanalysis_candidates",
        lambda limit: [
            {"id": 7, "product_title": "Portable Blender"},
            {"id": 8, "product_title": "Garden Light"},
        ],
    )

    def fail_category(**kwargs):
        raise RuntimeError("429 RESOURCE_EXHAUSTED. Please try again later.")

    monkeypatch.setattr(scheduler.product_analysis, "categorize_product", fail_category)
    monkeypatch.setattr(
        scheduler.store,
        "finish_category_reanalysis",
        lambda analysis_id, **kwargs: finished.append((analysis_id, kwargs)),
    )

    summary = scheduler.reanalyze_categories(limit=100, user_id=1)

    assert summary == {
        "scanned": 1,
        "done": 0,
        "failed": 1,
        "stopped": True,
        "stop_reason": "global_category_provider_error",
    }
    assert [item[0] for item in finished] == [7]
