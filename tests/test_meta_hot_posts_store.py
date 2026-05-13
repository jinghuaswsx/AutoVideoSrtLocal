from appcore.meta_hot_posts import store


def test_list_hot_posts_applies_category_price_interaction_comment_and_create_filters():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        if "COUNT" in sql:
            return [{"cnt": 1}]
        return [{"wedev_post_id": 1, "product_title": "Demo"}]

    payload = store.list_hot_posts(
        {
            "category": "Kitchenware",
            "min_price": "10",
            "max_price": "30.5",
            "min_interactions": "1000",
            "min_comments": "50",
            "created_from": "2026-05-01",
            "created_to": "2026-05-13",
            "page": "1",
            "page_size": "20",
        },
        query_fn=fake_query,
    )

    data_sql, data_params = calls[-1]
    assert payload["total"] == 1
    assert "a.category_l1 = %s" in data_sql
    assert "a.price_min >= %s" in data_sql
    assert "a.price_min <= %s" in data_sql
    assert "p.latest_likes >= %s" in data_sql
    assert "p.latest_comments >= %s" in data_sql
    assert "p.creation_time >= %s" in data_sql
    assert "p.creation_time < DATE_ADD(%s, INTERVAL 1 DAY)" in data_sql
    assert "ORDER BY COALESCE(p.sync_period_likes, 0) DESC, p.creation_time DESC, p.id DESC" in data_sql
    assert data_params[:7] == ["Kitchenware", 10.0, 30.5, 1000, 50, "2026-05-01", "2026-05-13"]


def test_upsert_hot_post_uses_wedev_post_unique_key():
    calls = []

    store.upsert_hot_post(
        {
            "wedev_post_id": 123,
            "page_id": "p",
            "post_id": "post",
            "bm_page_id": "bm",
            "post_url": "https://facebook.com/p/posts/post",
            "ad_library_url": "https://facebook.com/ads/library",
            "product_url": "https://example.com/products/a",
            "creation_time": "2026-05-08 12:00:00",
            "last_synced_at": "2026-05-12 09:00:00",
            "likes": 1,
            "comments": 2,
            "shares": 3,
            "latest_likes": 4,
            "latest_comments": 5,
            "latest_shares": 6,
            "sync_period_likes": 7,
            "sync_period_hours": 8.5,
            "copycat": False,
            "select_json": {},
            "video_url": "",
            "image_url": "",
            "message_html": "hello",
            "raw_json": {"id": 123},
        },
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "INSERT INTO meta_hot_posts" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "wedev_post_id" in sql
    assert params[0] == 123


def test_next_pending_product_analyses_selects_unfinished_rows():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"id": 7, "product_url": "https://example.com/p"}]

    rows = store.next_pending_product_analyses(limit=3, query_fn=fake_query)

    sql, params = calls[0]
    assert rows[0]["id"] == 7
    assert "meta_hot_post_product_analyses" in sql
    assert "status IN ('pending', 'failed')" in sql
    assert "attempts < %s" in sql
    assert params == (3, 3)


def test_next_pending_product_analyses_allows_100_per_tick():
    calls = []

    store.next_pending_product_analyses(
        limit=500,
        query_fn=lambda sql, params=(): calls.append((sql, params)) or [],
    )

    assert calls[0][1] == (3, 100)


def test_list_failed_product_analyses_returns_recent_failures():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [
            {
                "id": 9,
                "product_url": "https://example.com/bad",
                "attempts": 2,
                "last_error": "403 Client Error",
                "updated_at": "2026-05-13 18:00:00",
            }
        ]

    rows = store.list_failed_product_analyses(limit=200, query_fn=fake_query)

    sql, params = calls[0]
    assert rows[0]["product_url"] == "https://example.com/bad"
    assert "WHERE status = 'failed'" in sql
    assert "ORDER BY updated_at DESC" in sql
    assert params == (100,)


def test_reset_stale_running_product_analyses_marks_old_running_failed():
    calls = []

    store.reset_stale_running_product_analyses(
        older_than_seconds=3600,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 4,
    )

    sql, params = calls[0]
    assert "UPDATE meta_hot_post_product_analyses" in sql
    assert "status='running'" in sql
    assert "TIMESTAMPDIFF(SECOND, updated_at, NOW()) >= %s" in sql
    assert params == (3600,)
