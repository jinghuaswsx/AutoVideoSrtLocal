from appcore.meta_hot_posts import scheduler
from appcore.meta_hot_posts.product_analysis import ProductAnalysisResult
from datetime import datetime, timedelta


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


def test_register_schedules_daily_sync_analysis_and_translation(monkeypatch):
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
    assert video_kwargs["misfire_grace_time"] == 60
    assert video_kwargs["next_run_time"] == now + timedelta(seconds=5)


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


def test_translation_tick_once_defaults_to_50_messages_with_3_second_spacing(monkeypatch):
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

    assert captured["limit"] == 50
    assert captured["per_item_delay_seconds"] == 3


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


def test_video_localization_tick_once_replaces_running_run_every_time(monkeypatch):
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

    summary = scheduler.video_localization_tick_once(limit=1, min_delay_seconds=10)

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
    assert events[3] == ("download", 1, 10)


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
    assert payload["category"]["model"] == "google/gemini-3.1-flash-lite-preview"
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
    assert finished[0][1]["category"]["model"] == "google/gemini-3.1-flash-lite-preview"
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
