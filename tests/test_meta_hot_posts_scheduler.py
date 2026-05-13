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


def test_register_schedules_daily_sync_at_7am_and_analysis_interval(monkeypatch):
    calls = []

    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "add_controlled_job",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    scheduler.register(object())

    sync_args, sync_kwargs = calls[0]
    analysis_args, analysis_kwargs = calls[1]
    assert sync_args[1] == "meta_hot_posts_sync_tick"
    assert sync_args[3] == "cron"
    assert sync_kwargs["hour"] == 7
    assert sync_kwargs["minute"] == 0
    assert analysis_args[1] == "meta_hot_posts_analysis_tick"
    assert analysis_args[3] == "interval"
    assert analysis_kwargs["minutes"] == 10


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
        raise ValueError("invalid llm json")

    monkeypatch.setattr(scheduler.product_analysis, "categorize_product", fail_category)
    monkeypatch.setattr(
        scheduler.store,
        "finish_analysis",
        lambda analysis_id, **kwargs: finished.append((analysis_id, kwargs)),
    )

    summary = scheduler.analyze_pending_products(limit=1)

    assert summary == {"scanned": 1, "done": 1, "failed": 0, "category_failed": 1}
    assert finished[0][0] == 7
    payload = finished[0][1]
    assert payload["status"] == "done"
    assert payload["result"]["title"] == "Flexible Socket Extension"
    assert payload["result"]["price_min"] == 19.99
    assert payload["category"]["category"] == "Other"
    assert "invalid llm json" in payload["error_message"]
