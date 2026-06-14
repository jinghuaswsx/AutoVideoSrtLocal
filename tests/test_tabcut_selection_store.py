import json

from appcore.tabcut_selection import service, store


def test_list_video_candidates_applies_filters_and_sort_whitelist():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        if "COUNT" in sql:
            return [{"cnt": 1}]
        return [{"video_id": "v1", "score": 12.5}]

    payload = store.list_video_candidates(
        {
            "category_l1": "Food",
            "min_video_sales": "10",
            "min_goods_gmv_7d": "99.5",
            "sort": "goods_gmv_7d",
            "page": "1",
            "page_size": "20",
        },
        query_fn=fake_query,
    )

    data_sql, data_params = calls[-1]
    assert payload["total"] == 1
    assert payload["items"] == [{"video_id": "v1", "score": 12.5}]
    assert "c.category_l1_name = %s" in data_sql
    assert "c.item_sold_count >= %s" in data_sql
    assert "c.goods_gmv_7d >= %s" in data_sql
    assert "LEFT JOIN tabcut_videos" in data_sql
    assert "LEFT JOIN (" in data_sql
    assert "FROM tabcut_goods_snapshots" in data_sql
    assert "v.local_product_id" in data_sql
    assert "new_product_parent_task_id" in data_sql
    assert "g.item_name_zh AS primary_item_name_zh" in data_sql
    assert "g.item_name_zh_short AS primary_item_name_zh_short" in data_sql
    assert "g.category_l1_name_zh AS category_l1_name_zh" in data_sql
    assert "ORDER BY c.goods_gmv_7d DESC" in data_sql
    assert data_params[:4] == ["US", "Food", 10, 99.5]


def test_list_video_candidates_keeps_earliest_candidate_per_video_id():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 1}] if "COUNT" in sql else []

    store.list_video_candidates({}, query_fn=fake_query)

    count_sql = calls[0][0]
    data_sql = calls[-1][0]
    assert "c.id = (" in count_sql
    assert "SELECT MIN(c2.id)" in count_sql
    assert "c2.video_id = c.video_id" in count_sql
    assert "c.id = (" in data_sql
    assert "SELECT MIN(c2.id)" in data_sql
    assert "c2.video_id = c.video_id" in data_sql


def test_list_video_candidates_aggregates_goods_snapshot_join_to_one_row_per_item():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_video_candidates({}, query_fn=fake_query)

    data_sql = calls[-1][0]
    assert "FROM tabcut_goods_snapshots" in data_sql
    assert "GROUP BY biz_date, region, item_id" in data_sql
    assert "LEFT JOIN tabcut_goods_snapshots gs" not in data_sql


def test_list_video_candidates_rejects_unknown_sort():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_video_candidates({"sort": "score; DROP TABLE users"}, query_fn=fake_query)

    assert "ORDER BY c.play_count DESC" in calls[-1][0]
    assert "DROP TABLE" not in calls[-1][0]


def test_list_video_candidates_filters_by_video_publish_date_range():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_video_candidates(
        {
            "publish_date_from": "2026-04-13",
            "publish_date_to": "2026-05-12",
        },
        query_fn=fake_query,
    )

    data_sql, data_params = calls[-1]
    assert "v.create_time >= %s" in data_sql
    assert "v.create_time < DATE_ADD(%s, INTERVAL 1 DAY)" in data_sql
    assert data_params[:3] == ["US", "2026-04-13", "2026-05-12"]


def test_list_video_candidates_filters_by_source_rank():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_video_candidates({"source_rank": "7d"}, query_fn=fake_query)

    count_sql, count_params = calls[0]
    data_sql, data_params = calls[-1]
    assert "EXISTS (" in count_sql
    assert "tabcut_video_snapshots source_vs" in count_sql
    assert "source_vs.source_sort IN (%s, %s)" in count_sql
    assert "video_7d_play" in count_params
    assert "video_7d_sales" in count_params
    assert "source_vs.source_sort IN (%s, %s)" in data_sql
    assert data_params[:3] == ["US", "video_7d_play", "video_7d_sales"]


def test_list_video_candidates_filters_by_primary_item_price_range():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_video_candidates(
        {
            "min_item_price": "10.50",
            "max_item_price": "25",
        },
        query_fn=fake_query,
    )

    data_sql, data_params = calls[-1]
    assert "c.primary_item_price_min >= %s" in data_sql
    assert "c.primary_item_price_min <= %s" in data_sql
    assert data_params[:3] == ["US", 10.5, 25.0]


def test_list_video_candidates_filters_by_goods_sales_range():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_video_candidates(
        {
            "min_goods_sales_7d": "50",
            "max_goods_sales_7d": "500",
        },
        query_fn=fake_query,
    )

    data_sql, data_params = calls[-1]
    assert "c.goods_sold_count_7d >= %s" in data_sql
    assert "c.goods_sold_count_7d <= %s" in data_sql
    assert data_params[:3] == ["US", 50, 500]


def test_list_video_candidates_filters_by_mark_status_and_selects_mark_fields():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_video_candidates({"mark_status": "ok"}, query_fn=fake_query)

    count_sql, count_params = calls[0]
    data_sql, data_params = calls[-1]
    assert "v.mark_status = %s" in count_sql
    assert "v.mark_status = %s" in data_sql
    assert "v.is_marked" in data_sql
    assert "v.mark_status" in data_sql
    assert count_params[:2] == ["US", "ok"]
    assert data_params[:2] == ["US", "ok"]


def test_list_video_candidates_filters_by_empty_mark_status():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_video_candidates({"mark_status": "empty"}, query_fn=fake_query)

    data_sql, data_params = calls[-1]
    assert "v.mark_status IS NULL OR v.mark_status = ''" in data_sql
    assert "COALESCE(v.is_marked, 0) = 0" in data_sql
    assert data_params[:1] == ["US"]


def test_list_video_candidates_rejects_unknown_source_rank():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_video_candidates({"source_rank": "7d; DROP TABLE tabcut_videos"}, query_fn=fake_query)

    assert "source_vs.source_sort" not in calls[-1][0]
    assert "DROP TABLE" not in calls[-1][0]


def test_list_today_new_video_candidates_filters_by_first_seen_and_orders_latest_first():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    payload = store.list_today_new_video_candidates(
        {"source_rank": "7d", "page": "2", "page_size": "20"},
        query_fn=fake_query,
    )

    count_sql, count_params = calls[0]
    data_sql, data_params = calls[-1]
    assert payload["page"] == 2
    assert payload["page_size"] == 20
    assert "v.first_seen_at >= CURDATE()" in count_sql
    assert "v.first_seen_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)" in count_sql
    assert "v.first_seen_at >= CURDATE()" in data_sql
    assert "v.first_seen_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)" in data_sql
    assert "v.first_seen_at" in data_sql
    assert (
        "ORDER BY v.first_seen_at DESC, "
        "COALESCE(vs.play_count, c.play_count, 0) DESC, "
        "c.score DESC, c.video_id ASC"
    ) in " ".join(data_sql.split())
    assert count_params[:3] == ["US", "video_7d_play", "video_7d_sales"]
    assert data_params[:5] == ["US", "video_7d_play", "video_7d_sales", 20, 20]


def test_build_today_new_videos_response_uses_today_new_store_payload(monkeypatch):
    seen = {}

    def fake_list(args):
        seen.update(args)
        return {
            "items": [
                {
                    "video_id": "v1",
                    "primary_item_id": "i1",
                    "primary_item_name": "Demo product",
                    "video_raw_json": "{}",
                    "first_seen_at": "2026-06-11 08:00:00",
                }
            ],
            "total": 1,
            "page": 1,
            "page_size": 50,
        }

    monkeypatch.setattr(store, "list_today_new_video_candidates", fake_list)
    monkeypatch.setattr(service, "_tabcut_attach_fine_ai_evaluation", lambda items: None)

    result = service.build_today_new_videos_response({"q": "demo"})

    assert result.status_code == 200
    assert seen == {"q": "demo"}
    assert result.payload["total"] == 1
    assert result.payload["items"][0]["video_id"] == "v1"
    assert result.payload["items"][0]["first_seen_at"] == "2026-06-11 08:00:00"


def test_list_category_options_returns_distinct_l1_names():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [
            {"value": "Beauty", "label": "Beauty", "video_count": 12, "goods_count": 7},
            {"value": "Food", "label": "Food", "video_count": 3, "goods_count": 9},
        ]

    result = store.list_category_options(query_fn=fake_query)

    sql, params = calls[0]
    assert result == [
        {"value": "Beauty", "label": "Beauty", "video_count": 12, "goods_count": 7},
        {"value": "Food", "label": "Food", "video_count": 3, "goods_count": 9},
    ]
    assert "tabcut_video_candidates" in sql
    assert "tabcut_goods" in sql
    assert "category_l1_name" in sql
    assert params == ["US", "US"]


def test_list_goods_filters_by_snapshot_date_source_category_and_sales():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_goods(
        {
            "biz_date": "2026-05-11",
            "source_category": "25",
            "min_sales_7d": "50",
        },
        query_fn=fake_query,
    )

    data_sql, data_params = calls[-1]
    assert "s.biz_date = %s" in data_sql
    assert "s.source = %s" in data_sql
    assert "COALESCE(s.sold_count_7d, s.sold_count_period) >= %s" in data_sql
    assert data_params[:4] == ["US", "2026-05-11", "goods_cat_25", 50]


def test_list_goods_filters_by_goods_rank_kind_and_period():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_goods(
        {
            "goods_rank_kind": "new",
            "goods_rank_period": "30d",
            "min_sales_30d": "5000",
        },
        query_fn=fake_query,
    )

    data_sql, data_params = calls[-1]
    assert "s.source = %s" in data_sql
    assert "COALESCE(s.sold_count_30d, s.sold_count_period) >= %s" in data_sql
    assert data_params[:3] == ["US", "goods_new_30d", 5000]


def test_list_goods_rejects_unknown_goods_rank_source():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_goods(
        {
            "goods_rank_kind": "new; DROP TABLE tabcut_goods",
            "goods_rank_period": "30d",
        },
        query_fn=fake_query,
    )

    data_sql, data_params = calls[-1]
    assert "s.source = %s" not in data_sql
    assert "DROP TABLE" not in data_sql
    assert data_params == ["US", 50, 0]


def test_list_goods_filters_by_display_price_range():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_goods(
        {
            "min_price": "12.5",
            "max_price": "22",
        },
        query_fn=fake_query,
    )

    data_sql, data_params = calls[-1]
    assert "COALESCE(s.price_min, s.price_max) >= %s" in data_sql
    assert "COALESCE(s.price_min, s.price_max) <= %s" in data_sql
    assert data_params[:3] == ["US", 12.5, 22.0]


def test_list_goods_filters_by_display_sales_range():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_goods(
        {
            "min_sales_7d": "50",
            "max_sales_7d": "500",
        },
        query_fn=fake_query,
    )

    data_sql, data_params = calls[-1]
    assert "COALESCE(s.sold_count_7d, s.sold_count_period) >= %s" in data_sql
    assert "COALESCE(s.sold_count_7d, s.sold_count_period) <= %s" in data_sql
    assert data_params[:3] == ["US", 50, 500]


def test_list_goods_filters_by_mark_status_and_selects_mark_fields():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_goods({"mark_status": "bad"}, query_fn=fake_query)

    count_sql, count_params = calls[0]
    data_sql, data_params = calls[-1]
    assert "g.mark_status = %s" in count_sql
    assert "g.mark_status = %s" in data_sql
    assert "g.is_marked" in data_sql
    assert "g.mark_status" in data_sql
    assert "g.item_name_zh" in data_sql
    assert "g.item_name_zh_short" in data_sql
    assert "g.category_l1_name_zh" in data_sql
    assert count_params[:2] == ["US", "bad"]
    assert data_params[:2] == ["US", "bad"]


def test_next_pending_goods_translations_selects_untranslated_rows():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"item_id": "i1", "item_name": "English title"}]

    rows = store.next_pending_goods_translations(limit=12, query_fn=fake_query)

    sql, params = calls[0]
    assert rows[0]["item_id"] == "i1"
    assert "FROM tabcut_goods" in sql
    assert "zh_translation_status IN ('pending', 'failed')" in sql
    assert "zh_translation_attempts < %s" in sql
    assert params == [3, 12]


def test_finish_goods_translation_updates_chinese_fields():
    calls = []

    store.finish_goods_translation(
        "i1",
        payload={
            "item_name_zh": "紫色牙贴套装",
            "item_name_zh_short": "紫色牙贴",
            "category_l1_name_zh": "美妆个护",
        },
        error_message=None,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "UPDATE tabcut_goods" in sql
    assert "item_name_zh=%s" in sql
    assert "zh_translation_status='done'" in sql
    assert params[0:3] == ["紫色牙贴套装", "紫色牙贴", ""]
    assert params[-1] == "i1"


def test_list_video_candidates_selects_video_translation_fields():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_video_candidates({}, query_fn=fake_query)

    data_sql = calls[-1][0]
    assert "v.video_desc_zh" in data_sql
    assert "v.primary_item_name_zh AS video_primary_item_name_zh" in data_sql
    assert "v.zh_translation_status AS video_zh_translation_status" in data_sql
    assert "v.zh_translation_attempts AS video_zh_translation_attempts" in data_sql
    assert "v.zh_translation_error AS video_zh_translation_error" in data_sql
    assert "v.zh_translated_at AS video_zh_translated_at" in data_sql


def test_next_pending_video_translations_selects_untranslated_rows():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"video_id": "v1", "video_desc": "English copy"}]

    rows = store.next_pending_video_translations(limit=10, query_fn=fake_query)

    sql, params = calls[0]
    assert rows[0]["video_id"] == "v1"
    assert "FROM tabcut_videos" in sql
    assert "zh_translation_status IN ('pending', 'failed')" in sql
    assert "zh_translation_attempts < %s" in sql
    assert "video_desc IS NOT NULL" in sql
    assert params == [3, 10]


def test_mark_video_translation_running_increments_attempts():
    calls = []

    store.mark_video_translation_running(
        "v1",
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "UPDATE tabcut_videos" in sql
    assert "zh_translation_status='running'" in sql
    assert "zh_translation_attempts=zh_translation_attempts + 1" in sql
    assert params == ["v1"]


def test_finish_video_translation_updates_chinese_fields():
    calls = []

    store.finish_video_translation(
        "v1",
        payload={
            "video_desc_zh": "中文视频文案",
            "primary_item_name_zh": "中文商品标题",
        },
        error_message=None,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "UPDATE tabcut_videos" in sql
    assert "video_desc_zh=%s" in sql
    assert "primary_item_name_zh=%s" in sql
    assert "zh_translation_status='done'" in sql
    assert params == ["中文视频文案", "中文商品标题", "v1"]


def test_finish_video_translation_records_failure():
    calls = []

    store.finish_video_translation(
        "v1",
        payload=None,
        error_message="provider unavailable",
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "UPDATE tabcut_videos" in sql
    assert "zh_translation_status='failed'" in sql
    assert "zh_translation_error=%s" in sql
    assert params == ["provider unavailable", "v1"]


def test_build_videos_response_hydrates_chinese_product_display_fields(monkeypatch):
    monkeypatch.setattr(
        store,
        "list_video_candidates",
        lambda args: {
            "items": [
                {
                    "video_id": "v1",
                    "primary_item_id": "i1",
                    "primary_item_name": "Purple Teeth Whitening Strips",
                    "video_primary_item_name_zh": "紫色牙齿美白贴套装",
                    "video_desc_zh": "这是一款紫色牙齿美白贴",
                    "video_zh_translation_status": "done",
                    "primary_item_name_zh": "紫色牙齿美白贴套装",
                    "primary_item_name_zh_short": "紫色美白牙贴",
                    "category_l1_name": "Beauty & Personal Care",
                    "category_l1_name_zh": "美妆个护",
                    "zh_translation_status": "done",
                    "video_raw_json": "{}",
                }
            ],
            "total": 1,
        },
    )
    monkeypatch.setattr(service, "_tabcut_attach_fine_ai_evaluation", lambda items: None)

    result = service.build_videos_response({})
    item = result.payload["items"][0]

    assert item["primary_item_display_name"] == "紫色美白牙贴"
    assert item["primary_item_display_title"] == "紫色牙齿美白贴套装"
    assert item["primary_item_category_zh"] == "美妆个护"
    assert item["primary_item_is_translated"] is True
    assert item["video_desc_zh"] == "这是一款紫色牙齿美白贴"
    assert item["video_primary_item_name_zh"] == "紫色牙齿美白贴套装"


def test_build_goods_response_hydrates_source_category_label(monkeypatch):
    monkeypatch.setattr(
        store,
        "list_goods",
        lambda args: {
            "items": [{"item_id": "i1", "source": "goods_cat_25"}],
            "total": 1,
        },
    )

    result = service.build_goods_response({})

    assert result.payload["items"][0]["source_category_label"] == "五金工具"
    assert result.payload["items"][0]["source_category_name"] == "Tools & Hardware"


def test_build_goods_response_hydrates_rank_labels(monkeypatch):
    monkeypatch.setattr(
        store,
        "list_goods",
        lambda args: {
            "items": [{"item_id": "i1", "source": "goods_new_30d"}],
            "total": 1,
        },
    )

    result = service.build_goods_response({})

    item = result.payload["items"][0]
    assert item["goods_rank_kind"] == "new"
    assert item["goods_rank_kind_label"] == "新品榜"
    assert item["goods_rank_period"] == "30d"
    assert item["goods_rank_period_label"] == "月榜"


def test_build_videos_response_hydrates_raw_card_fields(monkeypatch):
    monkeypatch.setattr(
        store,
        "list_video_candidates",
        lambda args: {
            "items": [
                {
                    "video_id": "v1",
                    "primary_item_id": "i1",
                    "primary_item_pic_url": None,
                    "video_raw_json": json.dumps(
                        {
                            "hashtags": [{"hashtagName": "clean"}, {"hashtagName": "shop"}],
                            "itemList": [
                                {
                                    "itemCoverUrl": "item.webp",
                                    "itemName": "Gloves",
                                    "skuPrice": 3.76,
                                    "soldCount": 538,
                                    "currencySymbol": "$",
                                }
                            ],
                        }
                    ),
                }
            ],
            "total": 1,
        },
    )

    result = service.build_videos_response({})
    item = result.payload["items"][0]

    assert item["hashtags"] == ["clean", "shop"]
    assert item["primary_item_pic_url"] == "item.webp"
    assert item["primary_item_name"] == "Gloves"
    assert item["primary_item_price_min"] == 3.76
    assert item["primary_item_sold_count"] == 538
    assert item["primary_item_url"] == "https://www.tiktok.com/shop/pdp/i1"
    assert "video_raw_json" not in item


def test_upsert_video_candidate_uses_parameterized_execute():
    calls = []

    store.upsert_video_candidate(
        {
            "biz_date": "2026-05-11",
            "region": "US",
            "video_id": "v1",
            "primary_item_id": "i1",
            "primary_item_price_min": 12.34,
            "primary_item_price_max": 15.67,
            "price_currency": "$",
            "score": 10.5,
            "score_parts": {"play_count": 1.0},
        },
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "%s" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert params[0:4] == ["2026-05-11", "US", "v1", "i1"]
    assert params[4:7] == [12.34, 15.67, "$"]


def test_set_tabcut_video_mark_status_updates_video_row():
    calls = []

    result = store.set_video_mark_status(
        "v1",
        mark_status="ok",
        user_id=7,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert result == 1
    assert "UPDATE tabcut_videos" in sql
    assert "mark_status=%s" in sql
    assert "is_marked=%s" in sql
    assert params == ["ok", 1, 1, 1, 7, "v1"]


def test_set_tabcut_goods_mark_status_clears_goods_row():
    calls = []

    result = store.set_goods_mark_status(
        "i1",
        mark_status=None,
        user_id=7,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert result == 1
    assert "UPDATE tabcut_goods" in sql
    assert params == [None, 0, 0, 0, 7, "i1"]


def test_set_tabcut_video_local_import_binding_updates_video_row():
    calls = []

    result = store.set_video_local_import_binding(
        "v1",
        product_id=12,
        media_item_id=34,
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert result == 1
    assert "UPDATE tabcut_videos" in sql
    assert "local_product_id = %s" in sql
    assert "local_media_item_id = %s" in sql
    assert params == [12, 34, "v1"]


def test_build_tabcut_refresh_response_delegates_to_runner():
    seen = []

    result = service.build_tabcut_refresh_response(
        {"biz_date": "2026-05-11"},
        runner_fn=lambda **kwargs: seen.append(kwargs) or {"ok": True, "mode": "fake"},
    )

    assert result.status_code == 202
    assert result.payload["ok"] is True
    assert seen == [{"biz_date": "2026-05-11", "target_date": None, "days": 30}]


def test_hydrate_video_items_uses_analysis_video_search_root_item_fields():
    payload = {
        "items": [
            {
                "video_id": "v1",
                "primary_item_id": "i1",
                "primary_item_pic_url": None,
                "video_raw_json": json.dumps(
                    {
                        "itemId": "i1",
                        "itemName": "Demo product",
                        "itemCoverUrl": "https://cdn.example/item.webp",
                        "priceAmount": {"local": 19.99},
                        "currencySymbolInfo": {"local": "$"},
                        "itemSoldCountTotal": 1234,
                    }
                ),
            }
        ],
        "total": 1,
    }

    hydrated = service._hydrate_video_items(payload)

    item = hydrated["items"][0]
    assert item["primary_item_name"] == "Demo product"
    assert item["primary_item_pic_url"] == "https://cdn.example/item.webp"
    assert item["primary_item_price_min"] == 19.99
    assert item["currency_symbol"] == "$"
    assert item["primary_item_sold_count"] == 1234
    assert item["primary_item_url"] == "https://www.tiktok.com/shop/pdp/i1"


def test_hydrate_video_items_uses_price_currency_as_currency_symbol(monkeypatch):
    monkeypatch.setattr(
        store,
        "list_video_candidates",
        lambda args: {
            "items": [
                {
                    "video_id": "v1",
                    "primary_item_id": "i1",
                    "primary_item_price_min": 9.99,
                    "price_currency": "€",
                    "video_raw_json": "{}",
                }
            ],
            "total": 1,
        },
    )

    result = service.build_videos_response({})
    item = result.payload["items"][0]

    assert item["currency_symbol"] == "€"
