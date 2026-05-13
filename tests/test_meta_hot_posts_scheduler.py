from appcore.meta_hot_posts import scheduler


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
