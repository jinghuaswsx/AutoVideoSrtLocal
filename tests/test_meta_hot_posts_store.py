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
    assert "LEFT JOIN meta_hot_post_video_copyability_analyses va" in data_sql
    assert "va.status = 'done'" in data_sql
    assert "va.id AS video_copyability_analysis_id" in data_sql
    assert "va.overall_score AS video_copyability_overall_score" in data_sql
    assert "va.summary_zh AS video_copyability_summary_zh" in data_sql
    assert "LEFT JOIN meta_hot_post_europe_assessments e" in data_sql
    assert "e.status = 'done'" in data_sql
    assert "e.suitability_score AS europe_fit_score" in data_sql
    assert "e.strengths_zh_json AS europe_fit_strengths_zh_json" in data_sql
    assert "e.required_changes_zh_json AS europe_fit_required_changes_zh_json" in data_sql
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


def test_list_today_new_hot_posts_filters_by_first_seen_today():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        if "COUNT" in sql:
            return [{"cnt": 66}]
        return [{"id": 9, "first_seen_at": "2026-05-15 07:00:53"}]

    payload = store.list_today_new_hot_posts(query_fn=fake_query)

    data_sql, data_params = calls[-1]
    assert payload["total"] == 66
    assert payload["items"] == [{"id": 9, "first_seen_at": "2026-05-15 07:00:53"}]
    assert "p.first_seen_at >= CURDATE()" in data_sql
    assert "p.first_seen_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)" in data_sql
    assert "p.first_seen_at" in data_sql
    assert "LEFT JOIN meta_hot_post_video_copyability_analyses va" in data_sql
    assert "va.status = 'done'" in data_sql
    assert "va.id AS video_copyability_analysis_id" in data_sql
    assert "LEFT JOIN meta_hot_post_europe_assessments e" in data_sql
    assert "e.suitability_score AS europe_fit_score" in data_sql
    assert "ORDER BY p.first_seen_at DESC" in data_sql
    assert "COALESCE(p.sync_period_likes, 0) DESC" in data_sql
    assert data_params == [50, 0]


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


def test_set_hot_post_favorite_inserts_and_deletes_by_user():
    calls = []

    store.set_hot_post_favorite(
        7,
        user_id=88,
        favorited=True,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )
    store.set_hot_post_favorite(
        7,
        user_id=88,
        favorited=False,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    insert_sql, insert_params = calls[0]
    delete_sql, delete_params = calls[1]
    assert "INSERT INTO meta_hot_post_favorites" in insert_sql
    assert "user_id, hot_post_id" in insert_sql
    assert "ON DUPLICATE KEY UPDATE" in insert_sql
    assert insert_params == (88, 7)
    assert "DELETE FROM meta_hot_post_favorites" in delete_sql
    assert "WHERE user_id=%s AND hot_post_id=%s" in delete_sql
    assert delete_params == (88, 7)


def test_list_hot_posts_hydrates_user_favorite_state_when_user_id_given():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        if "COUNT" in sql:
            return [{"cnt": 1}]
        return [{"id": 7, "favorited_at": "2026-05-19 10:00:00"}]

    payload = store.list_hot_posts({}, user_id=88, query_fn=fake_query)

    data_sql, data_params = calls[-1]
    assert payload["items"][0]["favorited_at"] == "2026-05-19 10:00:00"
    assert "LEFT JOIN meta_hot_post_favorites fav" in data_sql
    assert "fav.hot_post_id = p.id AND fav.user_id = %s" in data_sql
    assert "fav.created_at AS favorited_at" in data_sql
    assert data_params == [88, 30, 0]


def test_list_favorite_hot_posts_sorts_by_user_choice():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        if "COUNT" in sql:
            return [{"cnt": 2}]
        return [{"id": 9, "favorited_at": "2026-05-19 09:00:00"}]

    payload = store.list_favorite_hot_posts(
        {"sort": "interactions", "page": "2", "page_size": "20"},
        user_id=88,
        query_fn=fake_query,
    )

    data_sql, data_params = calls[-1]
    assert payload["total"] == 2
    assert payload["items"][0]["id"] == 9
    assert "FROM meta_hot_post_favorites fav" in data_sql
    assert "JOIN meta_hot_posts p ON p.id = fav.hot_post_id" in data_sql
    assert "LEFT JOIN meta_hot_post_europe_assessments e" in data_sql
    assert "e.suitability_score AS europe_fit_score" in data_sql
    assert "va.summary_zh AS video_copyability_summary_zh" in data_sql
    assert "WHERE fav.user_id = %s" in data_sql
    assert "ORDER BY COALESCE(p.latest_likes, 0) DESC, fav.created_at DESC" in data_sql
    assert data_params == [88, 20, 20]


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


def test_list_hot_posts_selects_local_video_cache_fields():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        if "COUNT" in sql:
            return [{"cnt": 1}]
        return [{"wedev_post_id": 1, "local_video_status": "downloaded"}]

    payload = store.list_hot_posts({}, query_fn=fake_query)

    data_sql, _params = calls[-1]
    assert payload["items"][0]["local_video_status"] == "downloaded"
    assert "p.raw_json" in data_sql
    assert "p.local_video_path" in data_sql
    assert "p.local_video_duration_seconds" in data_sql
    assert "p.local_video_cover_path" in data_sql
    assert "p.local_video_status" in data_sql
    assert "p.local_video_error" in data_sql
    assert "p.local_video_downloaded_at" in data_sql
    assert "p.local_video_attempts" in data_sql


def test_next_pending_local_videos_selects_undownloaded_video_rows():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"id": 9, "video_url": "https://www.facebook.com/reel/1/"}]

    rows = store.next_pending_local_videos(limit=500, query_fn=fake_query)

    sql, params = calls[0]
    assert rows[0]["id"] == 9
    assert "video_url IS NOT NULL" in sql
    assert "local_video_status IN ('pending', 'failed')" in sql
    assert "local_video_status <> 'failed'" in sql
    assert "TIMESTAMPDIFF(HOUR, updated_at, NOW()) >= %s" in sql
    assert "local_video_status IS NULL" in sql
    assert "local_video_attempts < %s" in sql
    assert "local_video_status <> 'downloaded'" in sql
    assert "local_video_status <> 'unavailable'" in sql
    assert params == (5, 12, 100)


def test_local_video_status_transitions_are_recorded():
    calls = []

    store.mark_local_video_downloading(
        77,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )
    store.finish_local_video_download(
        77,
        local_video_path="meta_hot_posts/videos/77.mp4",
        local_video_duration_seconds=12.345,
        local_video_cover_path="meta_hot_posts/video_covers/77/thumbnail.jpg",
        error_message=None,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )
    store.finish_local_video_download(
        78,
        local_video_path=None,
        error_message="download failed",
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    running_sql, running_params = calls[0]
    success_sql, success_params = calls[1]
    failure_sql, failure_params = calls[2]
    assert "local_video_status='downloading'" in running_sql
    assert "local_video_attempts=local_video_attempts + 1" in running_sql
    assert running_params == (77,)
    assert "local_video_status='downloaded'" in success_sql
    assert "local_video_duration_seconds=%s" in success_sql
    assert "local_video_cover_path=%s" in success_sql
    assert "local_video_downloaded_at=NOW()" in success_sql
    assert success_params == (
        "meta_hot_posts/videos/77.mp4",
        12.345,
        "meta_hot_posts/video_covers/77/thumbnail.jpg",
        77,
    )
    assert "ELSE 'failed'" in failure_sql
    assert "local_video_error=CASE" in failure_sql
    assert failure_params == (5, 5, "download failed", "download failed", 78)


def test_finish_local_video_download_marks_unavailable_after_fifth_failure():
    calls = []

    store.finish_local_video_download(
        88,
        local_video_path=None,
        error_message="still blocked",
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "local_video_status=CASE WHEN local_video_attempts >= %s THEN 'unavailable' ELSE 'failed' END" in sql
    assert "unavailable after max retry attempts" in sql
    assert params == (5, 5, "still blocked", "still blocked", 88)


def test_local_video_metadata_queries_are_recorded():
    calls = []

    rows = store.list_local_videos_missing_metadata(
        limit=25,
        query_fn=lambda sql, params=(): calls.append((sql, params)) or [{"id": 5}],
    )
    updated = store.update_local_video_metadata(
        5,
        local_video_duration_seconds=33.2,
        local_video_cover_path="meta_hot_posts/video_covers/5/thumbnail.jpg",
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    select_sql, select_params = calls[0]
    update_sql, update_params = calls[1]
    assert rows == [{"id": 5}]
    assert "local_video_status = 'downloaded'" in select_sql
    assert "local_video_duration_seconds IS NULL" in select_sql
    assert "local_video_cover_path IS NULL" in select_sql
    assert select_params == (25,)
    assert updated == 1
    assert "UPDATE meta_hot_posts" in update_sql
    assert "local_video_duration_seconds=%s" in update_sql
    assert "local_video_cover_path=%s" in update_sql
    assert update_params == (33.2, "meta_hot_posts/video_covers/5/thumbnail.jpg", 5)


def test_list_local_videos_missing_metadata_can_scan_all_rows():
    calls = []

    rows = store.list_local_videos_missing_metadata(
        limit=None,
        query_fn=lambda sql, params=(): calls.append((sql, params)) or [{"id": 5}],
    )

    sql, params = calls[0]
    assert rows == [{"id": 5}]
    assert "LIMIT" not in sql
    assert params == ()


def test_reset_running_local_videos_marks_all_downloading_failed_for_takeover():
    calls = []

    result = store.reset_running_local_videos(
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 3,
    )

    sql, params = calls[0]
    assert result == 3
    assert "UPDATE meta_hot_posts" in sql
    assert "local_video_status='failed'" in sql
    assert "local_video_status='downloading'" in sql
    assert "TIMESTAMPDIFF" not in sql
    assert params == ()


def test_get_hot_post_local_video_returns_cache_row():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"id": 5, "local_video_path": "meta_hot_posts/videos/5.mp4"}]

    row = store.get_hot_post_local_video(5, query_fn=fake_query)

    sql, params = calls[0]
    assert row["local_video_path"] == "meta_hot_posts/videos/5.mp4"
    assert "FROM meta_hot_posts" in sql
    assert "local_video_status" in sql
    assert params == (5,)


def test_ensure_video_copyability_candidates_inserts_downloaded_product_videos():
    calls = []

    result = store.ensure_video_copyability_candidates(
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 6,
    )

    sql, params = calls[0]
    assert result == 6
    assert "INSERT INTO meta_hot_post_video_copyability_analyses" in sql
    assert "FROM meta_hot_posts p" in sql
    assert "p.local_video_status = 'downloaded'" in sql
    assert "p.product_url IS NOT NULL" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert params == ()


def test_ensure_video_copyability_candidates_default_returns_rowcount(monkeypatch):
    executed = []
    closed = []

    class FakeCursor:
        rowcount = 4

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=None):
            executed.append((sql, params))

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            closed.append(True)

    monkeypatch.setattr(store, "get_conn", lambda: FakeConn())

    result = store.ensure_video_copyability_candidates()

    assert result == 4
    assert executed
    assert closed == [True]


def test_ensure_video_copyability_candidate_for_post_inserts_single_downloaded_product_video():
    calls = []

    result = store.ensure_video_copyability_candidate_for_post(
        7,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert result == 1
    assert "INSERT INTO meta_hot_post_video_copyability_analyses" in sql
    assert "WHERE p.id = %s" in sql
    assert "p.local_video_status = 'downloaded'" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert params == (7,)


def test_get_hot_post_ai_analysis_row_selects_card_context_and_both_ai_results():
    calls = []

    row = store.get_hot_post_ai_analysis_row(
        7,
        query_fn=lambda sql, params=(): calls.append((sql, params)) or [{"id": 7}],
    )

    sql, params = calls[0]
    assert row == {"id": 7}
    assert "FROM meta_hot_posts p" in sql
    assert "LEFT JOIN meta_hot_post_product_analyses a" in sql
    assert "LEFT JOIN meta_hot_post_video_copyability_analyses va" in sql
    assert "LEFT JOIN meta_hot_post_europe_assessments e" in sql
    assert "va.status AS video_copyability_status" in sql
    assert "va.summary_zh AS video_copyability_summary_zh" in sql
    assert "e.llm_response_json AS europe_fit_llm_response_json" in sql
    assert "e.strengths_zh_json AS europe_fit_strengths_zh_json" in sql
    assert "e.risks_zh_json AS europe_fit_risks_zh_json" in sql
    assert "e.required_changes_zh_json AS europe_fit_required_changes_zh_json" in sql
    assert "e.reasoning_zh AS europe_fit_reasoning_zh" in sql
    assert "WHERE p.id=%s" in sql
    assert params == (7,)


def test_get_hot_post_ai_analysis_row_selects_chinese_cache_fields():
    calls = []

    store.get_hot_post_ai_analysis_row(
        7,
        query_fn=lambda sql, params=(): calls.append((sql, params)) or [{"id": 7}],
    )

    sql, _params = calls[0]
    assert "va.summary_zh AS video_copyability_summary_zh" in sql
    assert "e.strengths_zh_json AS europe_fit_strengths_zh_json" in sql
    assert "e.risks_zh_json AS europe_fit_risks_zh_json" in sql
    assert "e.required_changes_zh_json AS europe_fit_required_changes_zh_json" in sql
    assert "e.reasoning_zh AS europe_fit_reasoning_zh" in sql


def test_get_video_copyability_analysis_state_returns_attempt_state():
    calls = []

    row = store.get_video_copyability_analysis_state(
        7,
        query_fn=lambda sql, params=(): calls.append((sql, params)) or [{"id": 9, "attempts": 2}],
    )

    sql, params = calls[0]
    assert row["id"] == 9
    assert "FROM meta_hot_post_video_copyability_analyses" in sql
    assert "WHERE hot_post_id=%s" in sql
    assert "status" in sql
    assert "attempts" in sql
    assert params == (7,)


def test_ensure_europe_fit_candidates_inserts_downloaded_product_videos():
    calls = []

    result = store.ensure_europe_fit_candidates(
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 9,
    )

    sql, params = calls[0]
    assert result == 9
    assert "INSERT IGNORE INTO meta_hot_post_europe_assessments" in sql
    assert "SELECT p.id, 'pending'" in sql
    assert "FROM meta_hot_posts p" in sql
    assert "LEFT JOIN meta_hot_post_europe_assessments e ON e.post_id = p.id" in sql
    assert "p.local_video_status = 'downloaded'" in sql
    assert "p.local_video_path IS NOT NULL" in sql
    assert "p.product_url IS NOT NULL" in sql
    assert "e.id IS NULL" in sql
    assert params == ()


def test_ensure_europe_fit_candidates_default_returns_insert_rowcount(monkeypatch):
    executed = []
    closed = []

    class FakeCursor:
        rowcount = 5

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=None):
            executed.append((sql, params))

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            closed.append(True)

    monkeypatch.setattr(store, "get_conn", lambda: FakeConn())

    result = store.ensure_europe_fit_candidates()

    assert result == 5
    assert executed
    assert closed == [True]


def test_ensure_europe_fit_candidate_for_post_inserts_single_downloaded_product_video():
    calls = []

    result = store.ensure_europe_fit_candidate_for_post(
        7,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert result == 1
    assert "INSERT IGNORE INTO meta_hot_post_europe_assessments" in sql
    assert "WHERE p.id = %s" in sql
    assert "p.local_video_status = 'downloaded'" in sql
    assert "p.product_url IS NOT NULL" in sql
    assert params == (7,)


def test_get_europe_fit_assessment_state_returns_attempt_state():
    calls = []

    row = store.get_europe_fit_assessment_state(
        7,
        query_fn=lambda sql, params=(): calls.append((sql, params)) or [{"post_id": 7, "attempts": 2}],
    )

    sql, params = calls[0]
    assert row["post_id"] == 7
    assert "FROM meta_hot_post_europe_assessments" in sql
    assert "WHERE post_id=%s" in sql
    assert "status" in sql
    assert "attempts" in sql
    assert params == (7,)


def test_next_pending_video_copyability_analyses_selects_unfinished_downloaded_rows():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"analysis_id": 7, "hot_post_id": 9, "product_url": "https://example.com/p"}]

    rows = store.next_pending_video_copyability_analyses(limit=500, query_fn=fake_query)

    sql, params = calls[0]
    assert rows[0]["analysis_id"] == 7
    assert "meta_hot_post_video_copyability_analyses va" in sql
    assert "JOIN meta_hot_posts p ON p.id = va.hot_post_id" in sql
    assert "LEFT JOIN meta_hot_post_product_analyses pa" in sql
    assert "va.status IN ('pending', 'failed')" in sql
    assert "va.status AS analysis_status" in sql
    assert "va.last_error" in sql
    assert "p.local_video_status = 'downloaded'" in sql
    assert "va.attempts < %s" in sql
    assert params == (3, 100)


def test_video_copyability_status_transitions_are_recorded():
    calls = []

    store.mark_video_copyability_running(
        77,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )
    store.finish_video_copyability_analysis(
        77,
        result={
            "overall_score": 91,
            "copyability_score": 94,
            "meta_us_ad_fit_score": 89,
            "product_fit_score": 88,
            "compliance_risk_score": 12,
            "recommendation": "copy",
            "summary": "Strong hook.",
        },
        error_message=None,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )
    store.finish_video_copyability_analysis(
        78,
        result={},
        error_message="provider failed",
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    running_sql, running_params = calls[0]
    success_sql, success_params = calls[1]
    failure_sql, failure_params = calls[2]
    assert "UPDATE meta_hot_post_video_copyability_analyses" in running_sql
    assert "status='running'" in running_sql
    assert "attempts=attempts + 1" in running_sql
    assert running_params == (77,)
    assert "status=%s" in success_sql
    assert "overall_score=%s" in success_sql
    assert "analysis_json=%s" in success_sql
    assert "summary_zh=%s" in success_sql
    assert "analyzed_at=CASE WHEN %s = 'done' THEN NOW() ELSE analyzed_at END" in success_sql
    assert success_params[0] == "done"
    assert success_params[2:7] == (91, 94, 89, 88, 12)
    assert success_params[-1] == 77
    assert "status=%s" in failure_sql
    assert failure_params[0] == "failed"
    assert failure_params[1] == "provider failed"


def test_finish_video_copyability_can_suspend_after_attempt_limit():
    calls = []

    store.finish_video_copyability_analysis(
        78,
        result={},
        error_message="model returned empty response",
        status_override="suspended",
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "UPDATE meta_hot_post_video_copyability_analyses" in sql
    assert "status=%s" in sql
    assert params[0] == "suspended"
    assert params[1] == "model returned empty response"
    assert params[-1] == 78


def test_restore_video_copyability_analysis_state_only_updates_running_row():
    calls = []

    store.restore_video_copyability_analysis_state(
        78,
        status="failed",
        attempts=2,
        last_error="old failure",
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "UPDATE meta_hot_post_video_copyability_analyses" in sql
    assert "status=%s" in sql
    assert "attempts=%s" in sql
    assert "last_error=%s" in sql
    assert "WHERE id=%s" in sql
    assert "AND status='running'" in sql
    assert params == ("failed", 2, "old failure", 78)


def test_suspend_exhausted_video_copyability_analyses_quarantines_pending_or_failed_rows():
    calls = []

    result = store.suspend_exhausted_video_copyability_analyses(
        max_attempts=3,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 2,
    )

    sql, params = calls[0]
    assert result == 2
    assert "UPDATE meta_hot_post_video_copyability_analyses" in sql
    assert "SET status='suspended'" in sql
    assert "status IN ('pending', 'failed')" in sql
    assert "attempts >= %s" in sql
    assert "attempts exhausted; suspended by queue guard" in sql
    assert params == (3,)


def test_reset_running_video_copyability_analyses_requeues_for_takeover():
    calls = []

    result = store.reset_running_video_copyability_analyses(
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 4,
    )

    sql, params = calls[0]
    assert result == 4
    assert "UPDATE meta_hot_post_video_copyability_analyses" in sql
    assert "status=CASE WHEN attempts >= %s THEN 'suspended' ELSE 'pending' END" in sql
    assert "status='running'" in sql
    assert "superseded by a new run" in sql
    assert "attempts exhausted; suspended by queue guard" in sql
    assert params == (3, 3)


def test_list_top_video_copyability_analyses_orders_best_50():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"analysis_id": 1, "overall_score": 99, "product_url": "https://example.com/p"}]

    rows = store.list_top_video_copyability_analyses(limit=200, query_fn=fake_query)

    sql, params = calls[0]
    assert rows[0]["overall_score"] == 99
    assert "WHERE va.status = 'done'" in sql
    assert "ORDER BY va.overall_score DESC" in sql
    assert "va.copyability_score DESC" in sql
    assert "va.meta_us_ad_fit_score DESC" in sql
    assert params == (50,)


def test_video_copyability_summary_zh_translation_queue_and_status_updates():
    calls = []

    def fake_query(sql, params=()):
        calls.append(("query", sql, params))
        return [{"analysis_id": 9, "summary": "Strong hook.", "analysis_json": '{"risk_notes":["policy"]}'}]

    rows = store.next_pending_video_copyability_summary_translations(
        limit=500,
        query_fn=fake_query,
    )
    store.mark_video_copyability_summary_translation_running(
        9,
        execute_fn=lambda sql, params=(): calls.append(("execute", sql, params)) or 1,
    )
    store.finish_video_copyability_summary_translation(
        9,
        translated_summary="强钩子，产品展示清晰。",
        error_message=None,
        execute_fn=lambda sql, params=(): calls.append(("execute", sql, params)) or 1,
    )
    store.finish_video_copyability_summary_translation(
        10,
        translated_summary=None,
        error_message="quota exhausted",
        execute_fn=lambda sql, params=(): calls.append(("execute", sql, params)) or 1,
    )

    select_sql, select_params = calls[0][1], calls[0][2]
    assert rows[0]["analysis_id"] == 9
    assert "FROM meta_hot_post_video_copyability_analyses" in select_sql
    assert "status = 'done'" in select_sql
    assert "summary_zh_status IN ('pending', 'failed')" in select_sql
    assert "summary_zh_attempts < %s" in select_sql
    assert select_params == (3, 120)
    running_sql, running_params = calls[1][1], calls[1][2]
    assert "summary_zh_status='running'" in running_sql
    assert "summary_zh_attempts=summary_zh_attempts + 1" in running_sql
    assert running_params == (9,)
    success_sql, success_params = calls[2][1], calls[2][2]
    assert "summary_zh=%s" in success_sql
    assert "summary_zh_status='done'" in success_sql
    assert "summary_zh_translated_at=NOW()" in success_sql
    assert success_params == ("强钩子，产品展示清晰。", 9)
    failure_sql, failure_params = calls[3][1], calls[3][2]
    assert "summary_zh_status='failed'" in failure_sql
    assert "summary_zh_error=%s" in failure_sql
    assert failure_params == ("quota exhausted", 10)


def test_next_pending_europe_fit_materials_selects_downloaded_video_rows():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"id": 9, "product_url": "https://example.com/products/a"}]

    rows = store.next_pending_europe_fit_materials(limit=500, query_fn=fake_query)

    sql, params = calls[0]
    assert rows[0]["id"] == 9
    assert "FROM meta_hot_posts p" in sql
    assert "LEFT JOIN meta_hot_post_product_analyses a" in sql
    assert "LEFT JOIN meta_hot_post_europe_assessments e ON e.post_id = p.id" in sql
    assert "e.status AS europe_fit_status" in sql
    assert "e.attempts AS europe_fit_attempts" in sql
    assert "e.last_error AS europe_fit_last_error" in sql
    assert "p.local_video_status = 'downloaded'" in sql
    assert "p.local_video_path IS NOT NULL" in sql
    assert "p.product_url IS NOT NULL" in sql
    assert "(e.id IS NULL OR e.status IN ('pending', 'failed'))" in sql
    assert "COALESCE(e.attempts, 0) < %s" in sql
    assert params == (3, 100)


def test_europe_fit_status_transitions_are_recorded():
    calls = []

    store.mark_europe_fit_running(
        77,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )
    store.finish_europe_fit_assessment(
        77,
        status="done",
        result={
            "suitability_score": 91,
            "recommendation": "direct_reuse",
            "direct_reuse": True,
            "best_countries": ["DE", "FR"],
            "country_scores": {"DE": 92},
            "strengths": ["clear demo"],
            "risks": ["minor English overlay"],
            "required_changes": [],
            "reasoning": "Strong visual proof.",
            "provider": "openrouter",
            "model": "google/gemini-3-flash-preview",
            "raw_response": {"ok": True},
        },
        video_optimization={"optimized": True},
        error_message=None,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )
    store.finish_europe_fit_assessment(
        78,
        status="failed",
        result={},
        video_optimization={},
        error_message="local video missing",
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    running_sql, running_params = calls[0]
    success_sql, success_params = calls[1]
    failure_sql, failure_params = calls[2]
    assert "INSERT INTO meta_hot_post_europe_assessments" in running_sql
    assert "ON DUPLICATE KEY UPDATE" in running_sql
    assert "attempts=attempts + 1" in running_sql
    assert running_params == (77,)
    assert "suitability_score=%s" in success_sql
    assert "recommendation=%s" in success_sql
    assert "direct_reuse=%s" in success_sql
    assert "video_optimization_json=%s" in success_sql
    assert "assessed_at=CASE WHEN %s = 'done' THEN NOW() ELSE assessed_at END" in success_sql
    assert success_params[1] is None
    assert success_params[2] == 91
    assert success_params[3] == "direct_reuse"
    assert success_params[4] == 1
    assert success_params[-2] == "done"
    assert success_params[-1] == 77
    assert "last_error=%s" in failure_sql
    assert failure_params[0] == "failed"
    assert failure_params[1] == "local video missing"
    assert failure_params[-1] == 78


def test_restore_europe_fit_assessment_state_only_updates_running_row():
    calls = []

    store.restore_europe_fit_assessment_state(
        78,
        status="failed",
        attempts=1,
        last_error="old europe failure",
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "UPDATE meta_hot_post_europe_assessments" in sql
    assert "status=%s" in sql
    assert "attempts=%s" in sql
    assert "last_error=%s" in sql
    assert "WHERE post_id=%s" in sql
    assert "AND status='running'" in sql
    assert params == ("failed", 1, "old europe failure", 78)


def test_suspend_exhausted_europe_fit_assessments_quarantines_pending_or_failed_rows():
    calls = []

    result = store.suspend_exhausted_europe_fit_assessments(
        max_attempts=3,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert result == 1
    assert "UPDATE meta_hot_post_europe_assessments" in sql
    assert "SET status='suspended'" in sql
    assert "status IN ('pending', 'failed')" in sql
    assert "attempts >= %s" in sql
    assert "attempts exhausted; suspended by queue guard" in sql
    assert params == (3,)


def test_reset_running_europe_fit_assessments_requeues_for_takeover():
    calls = []

    result = store.reset_running_europe_fit_assessments(
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 3,
    )

    sql, params = calls[0]
    assert result == 3
    assert "UPDATE meta_hot_post_europe_assessments" in sql
    assert "status=CASE WHEN attempts >= %s THEN 'suspended' ELSE 'pending' END" in sql
    assert "status='running'" in sql
    assert "superseded by a new run" in sql
    assert "attempts exhausted; suspended by queue guard" in sql
    assert params == (3, 3)


def test_europe_fit_zh_translation_queue_and_status_updates():
    calls = []

    rows = store.next_pending_europe_fit_translations(
        limit=500,
        query_fn=lambda sql, params=(): calls.append((sql, params)) or [{"post_id": 9}],
    )
    store.mark_europe_fit_translation_running(
        9,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )
    store.finish_europe_fit_translation(
        9,
        translated={
            "strengths": ["演示清晰"],
            "risks": ["英文字幕需要本土化"],
            "required_changes": ["翻译字幕"],
            "reasoning": "适合欧洲投放，但需要本土化字幕。",
        },
        error_message=None,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )
    store.finish_europe_fit_translation(
        10,
        translated=None,
        error_message="429 resource exhausted",
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    select_sql, select_params = calls[0]
    running_sql, running_params = calls[1]
    success_sql, success_params = calls[2]
    failure_sql, failure_params = calls[3]
    assert rows == [{"post_id": 9}]
    assert "FROM meta_hot_post_europe_assessments" in select_sql
    assert "status = 'done'" in select_sql
    assert "zh_attempts < %s" in select_sql
    assert select_params == (3, 120)
    assert "SET zh_status='running'" in running_sql
    assert "zh_attempts=zh_attempts + 1" in running_sql
    assert running_params == (9,)
    assert "strengths_zh_json=%s" in success_sql
    assert "risks_zh_json=%s" in success_sql
    assert "required_changes_zh_json=%s" in success_sql
    assert "reasoning_zh=%s" in success_sql
    assert "zh_status='done'" in success_sql
    assert success_params[3] == "适合欧洲投放，但需要本土化字幕。"
    assert success_params[-1] == 9
    assert "zh_status='failed'" in failure_sql
    assert failure_params == ("429 resource exhausted", 10)


def test_list_top_europe_fit_materials_orders_by_score():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"id": 5, "europe_fit_score": 96}]

    rows = store.list_top_europe_fit_materials(limit=500, query_fn=fake_query)

    sql, params = calls[0]
    assert rows[0]["europe_fit_score"] == 96
    assert "FROM meta_hot_post_europe_assessments e" in sql
    assert "JOIN meta_hot_posts p ON p.id = e.post_id" in sql
    assert "LEFT JOIN meta_hot_post_video_copyability_analyses va" in sql
    assert "va.status = 'done'" in sql
    assert "va.id AS video_copyability_analysis_id" in sql
    assert "e.strengths_zh_json AS europe_fit_strengths_zh_json" in sql
    assert "e.risks_zh_json AS europe_fit_risks_zh_json" in sql
    assert "e.required_changes_zh_json AS europe_fit_required_changes_zh_json" in sql
    assert "e.reasoning_zh AS europe_fit_reasoning_zh" in sql
    assert "e.status = 'done'" in sql
    assert "ORDER BY e.suitability_score DESC" in sql
    assert params == (50,)

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
        store.CATEGORY_PROVIDER,
        store.CATEGORY_MODEL,
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
    assert params == (store.CATEGORY_PROVIDER, store.CATEGORY_MODEL, "category failed:%", 80)


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
        store.CATEGORY_PROVIDER,
        store.CATEGORY_MODEL,
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
