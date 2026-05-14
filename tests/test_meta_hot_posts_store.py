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
            "mark_status": "ok",
            "created_from": "2026-05-01",
            "created_to": "2026-05-13",
            "q": "magic",
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
    assert "p.mark_status = %s" in data_sql
    assert "p.creation_time >= %s" in data_sql
    assert "p.creation_time < DATE_ADD(%s, INTERVAL 1 DAY)" in data_sql
    assert "p.is_marked" in data_sql
    assert "p.mark_status" in data_sql
    assert "p.marked_at" in data_sql
    assert "p.marked_by" in data_sql
    assert "p.message_zh_html" in data_sql
    assert "(p.message_html LIKE %s OR p.message_zh_html LIKE %s" in data_sql
    assert "ORDER BY COALESCE(p.sync_period_likes, 0) DESC, p.creation_time DESC, p.id DESC" in data_sql
    assert data_params[:8] == ["Kitchenware", 10.0, 30.5, 1000, 50, "ok", "2026-05-01", "2026-05-13"]
    assert data_params[8:12] == ["%magic%", "%magic%", "%magic%", "%magic%"]


def test_list_hot_posts_empty_mark_status_selects_unchecked_rows():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        if "COUNT" in sql:
            return [{"cnt": 2}]
        return [{"wedev_post_id": 1}, {"wedev_post_id": 2}]

    payload = store.list_hot_posts(
        {"mark_status": "empty"},
        query_fn=fake_query,
    )

    data_sql, data_params = calls[-1]
    assert payload["total"] == 2
    assert "(p.mark_status IS NULL OR p.mark_status = '')" in data_sql
    assert "COALESCE(p.is_marked, 0) = 0" in data_sql
    assert data_params == [30, 0]


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
    assert "message_zh_status" in sql
    assert "message_zh_html=CASE WHEN VALUES(message_html) <=> message_html" in sql
    assert "message_zh_status=CASE WHEN VALUES(message_html) <=> message_html" in sql
    assert "message_zh_attempts=CASE WHEN VALUES(message_html) <=> message_html" in sql
    assert params[0] == 123


def test_set_hot_post_mark_status_updates_local_mark_audit_fields():
    calls = []

    store.set_hot_post_mark_status(
        123,
        mark_status="bad",
        user_id=88,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "UPDATE meta_hot_posts" in sql
    assert "mark_status=%s" in sql
    assert "is_marked=%s" in sql
    assert "marked_at=CASE WHEN %s = 1 THEN NOW() ELSE NULL END" in sql
    assert "marked_by=CASE WHEN %s = 1 THEN %s ELSE NULL END" in sql
    assert "WHERE id=%s" in sql
    assert params == ("bad", 1, 1, 1, 88, 123)


def test_next_pending_message_translations_selects_untranslated_rows():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"id": 7, "message_html": "<p>Hello</p>"}]

    rows = store.next_pending_message_translations(limit=500, query_fn=fake_query)

    sql, params = calls[0]
    assert rows[0]["id"] == 7
    assert "message_html IS NOT NULL" in sql
    assert "message_zh_status IN ('pending', 'failed')" in sql
    assert "message_zh_attempts < %s" in sql
    assert params == (3, 100)


def test_mark_message_translation_running_increments_attempts():
    calls = []

    store.mark_message_translation_running(
        123,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "UPDATE meta_hot_posts" in sql
    assert "message_zh_status='running'" in sql
    assert "message_zh_attempts=message_zh_attempts + 1" in sql
    assert params == (123,)


def test_finish_message_translation_saves_translated_html():
    calls = []

    store.finish_message_translation(
        123,
        translated_html="你好<br>世界",
        error_message=None,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "UPDATE meta_hot_posts" in sql
    assert "message_zh_html=%s" in sql
    assert "message_zh_status='done'" in sql
    assert "message_zh_translated_at=NOW()" in sql
    assert params == ("你好<br>世界", 123)


def test_finish_message_translation_failure_records_error():
    calls = []

    store.finish_message_translation(
        123,
        translated_html=None,
        error_message="provider failed",
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "message_zh_status='failed'" in sql
    assert "message_zh_error=%s" in sql
    assert params == ("provider failed", 123)


def test_reset_stale_running_message_translations_marks_old_running_failed():
    calls = []

    store.reset_stale_running_message_translations(
        older_than_seconds=3600,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 2,
    )

    sql, params = calls[0]
    assert "UPDATE meta_hot_posts" in sql
    assert "message_zh_status='running'" in sql
    assert "TIMESTAMPDIFF(SECOND, updated_at, NOW()) >= %s" in sql
    assert params == (3600,)


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


def test_next_category_reanalysis_candidates_selects_done_rows_with_titles():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"id": 11, "product_title": "Portable Blender"}]

    rows = store.next_category_reanalysis_candidates(limit=500, query_fn=fake_query)

    sql, params = calls[0]
    assert rows[0]["id"] == 11
    assert "status = 'done'" in sql
    assert "product_title IS NOT NULL" in sql
    assert "last_error LIKE %s" in sql
    assert "category failed:%%" not in sql
    assert "category_l1 = 'Other'" in sql
    assert params == (
        "openrouter",
        "google/gemini-3.1-flash-lite-preview",
        "category failed:%",
        "category failed:%",
        100,
    )


def test_next_category_reanalysis_candidates_include_all_skips_current_openrouter_route():
    calls = []

    store.next_category_reanalysis_candidates(
        limit=80,
        include_all=True,
        query_fn=lambda sql, params=(): calls.append((sql, params)) or [],
    )

    sql, params = calls[0]
    assert "COALESCE(llm_provider, '') <> %s" in sql
    assert "COALESCE(llm_model, '') <> %s" in sql
    assert "AND (last_error LIKE %s" not in sql
    assert "category failed:%%" not in sql
    assert params == ("openrouter", "google/gemini-3.1-flash-lite-preview", "category failed:%", 80)


def test_next_category_reanalysis_candidates_excludes_current_openrouter_failures():
    calls = []

    store.next_category_reanalysis_candidates(
        limit=100,
        query_fn=lambda sql, params=(): calls.append((sql, params)) or [],
    )

    sql, params = calls[0]
    assert (
        "COALESCE(llm_provider, '') <> %s OR COALESCE(llm_model, '') <> %s) "
        "AND (last_error LIKE %s"
    ) in " ".join(sql.split())
    assert params == (
        "openrouter",
        "google/gemini-3.1-flash-lite-preview",
        "category failed:%",
        "category failed:%",
        100,
    )


def test_finish_category_reanalysis_updates_only_category_fields():
    calls = []

    store.finish_category_reanalysis(
        11,
        category={
            "category": "Kitchenware",
            "confidence": 1.0,
            "reason": "Title maps to kitchen product.",
            "provider": "openrouter",
            "model": "google/gemini-3.1-flash-lite-preview",
            "raw_response": {"text": "Kitchenware"},
        },
        error_message=None,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "UPDATE meta_hot_post_product_analyses" in sql
    assert "product_title=" not in sql
    assert "sku_prices_json=" not in sql
    assert "category_l1=%s" in sql
    assert params[1] == "Kitchenware"
    assert params[4] == "openrouter"
    assert params[-1] == 11
