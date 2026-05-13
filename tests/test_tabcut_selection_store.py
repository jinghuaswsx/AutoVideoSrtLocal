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
    assert "LEFT JOIN tabcut_goods_snapshots" in data_sql
    assert "ORDER BY c.goods_gmv_7d DESC" in data_sql
    assert data_params[:4] == ["US", "Food", 10, 99.5]


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


def test_list_video_candidates_rejects_unknown_source_rank():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    store.list_video_candidates({"source_rank": "7d; DROP TABLE tabcut_videos"}, query_fn=fake_query)

    assert "source_vs.source_sort" not in calls[-1][0]
    assert "DROP TABLE" not in calls[-1][0]


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
            "score": 10.5,
            "score_parts": {"play_count": 1.0},
        },
        execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1,
    )

    sql, params = calls[0]
    assert "%s" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert params[0:4] == ["2026-05-11", "US", "v1", "i1"]


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
