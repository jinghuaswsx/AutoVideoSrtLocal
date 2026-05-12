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
