from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import appcore.mingkong_materials as mm


def test_material_key_is_stable_and_path_specific():
    first = mm.material_key_for("cool-widget", 901, "uploads2/a.mp4")
    second = mm.material_key_for("cool-widget", 901, "uploads2/a.mp4")
    other = mm.material_key_for("cool-widget", 901, "uploads2/b.mp4")

    assert first == second
    assert first != other
    assert len(first) == 64


def test_get_material_detail_returns_latest_snapshot_and_history(monkeypatch):
    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)
    material_key = "a" * 64
    older = {
        "id": 1,
        "snapshot_date": date(2026, 5, 21),
        "snapshot_at": datetime(2026, 5, 21, 5, 0, 2),
        "snapshot_slot": "0500",
        "ranking_snapshot_date": date(2026, 5, 20),
        "material_key": material_key,
        "product_code": "cool-widget",
        "rank_position": 7,
        "product_name": "Cool Widget",
        "product_url": "https://shop.example/products/cool-widget",
        "mk_product_id": 901,
        "mk_product_name": "MK Cool",
        "mk_product_link": "https://shop.example/products/cool-widget-rjc",
        "video_name": "winner.mp4",
        "video_path": "uploads2/winner.mp4",
        "video_image_path": "uploads2/winner.jpg",
        "cumulative_90_spend": 12000,
        "video_ads_count": 12,
        "video_author": "Alice",
        "video_upload_time": "2026-05-20T10:00:00",
        "video_duration_seconds": 15.5,
        "mk_video_metadata_json": "{}",
        "created_at": None,
        "updated_at": None,
    }
    latest = dict(
        older,
        id=2,
        snapshot_date=date(2026, 5, 22),
        snapshot_at=datetime(2026, 5, 22, 5, 0, 2),
        snapshot_slot="0500",
        cumulative_90_spend=12800,
        video_ads_count=16,
    )

    def fake_query_one(sql, args=()):
        assert "FROM mingkong_material_daily_snapshots" in sql
        assert args == (material_key,)
        return latest

    def fake_query(sql, args=()):
        assert "FROM mingkong_material_daily_snapshots" in sql
        assert "ORDER BY snapshot_at ASC" in sql
        assert args == (material_key,)
        return [older, latest]

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    detail = mm.get_material_detail(material_key)

    assert detail["material"]["video_name"] == "winner.mp4"
    assert detail["material"]["snapshot_at"] == "2026-05-22 05:00:02"
    assert detail["history"][0]["spend_delta"] == 0.0
    assert detail["history"][1]["spend_delta"] == 800.0
    assert detail["summary"] == {
        "history_count": 2,
        "first_snapshot_at": "2026-05-21 05:00:02",
        "latest_snapshot_at": "2026-05-22 05:00:02",
        "min_cumulative_90_spend": 12000.0,
        "max_cumulative_90_spend": 12800.0,
    }


def test_media_search_code_for_adds_rjc_suffix_once():
    assert mm.media_search_code_for("cool-widget") == "cool-widget-rjc"
    assert mm.media_search_code_for("cool-widget-rjc") == "cool-widget-rjc"
    assert mm.media_search_code_for("Cool_Widget_RJC") == "cool_widget-rjc"
    assert mm.media_search_code_for("") == ""


def test_material_range_bounds_supports_named_ranges(monkeypatch):
    monkeypatch.setattr(mm, "_today", lambda: date(2026, 5, 20))

    assert mm._material_range_bounds("this_week") == ("2026-05-18", "2026-05-24")
    assert mm._material_range_bounds("last_week") == ("2026-05-11", "2026-05-17")
    assert mm._material_range_bounds("this_month") == ("2026-05-01", "2026-05-31")
    assert mm._material_range_bounds("last_month") == ("2026-04-01", "2026-04-30")
    assert mm._material_range_bounds("") is None


def test_material_snapshot_identity_uses_latest_successful_run(monkeypatch):
    captured = []

    def fake_query_one(sql, args=()):
        captured.append((sql, args))
        return {
            "snapshot_date": date(2026, 5, 20),
            "snapshot_at": datetime(2026, 5, 20, 6, 0, 12),
            "snapshot_slot": "0600",
        }

    monkeypatch.setattr(mm, "query_one", fake_query_one)

    identity = mm._material_snapshot_identity()

    assert identity["snapshot_date"] == "2026-05-20"
    assert identity["snapshot_at"] == "2026-05-20 06:00:12"
    assert identity["snapshot_slot"] == "0600"
    assert "mingkong_material_sync_runs" in captured[0][0]
    assert "status = 'success'" in captured[0][0]


def test_latest_top500_products_use_latest_dianxiaomi_snapshot(monkeypatch):
    calls = []

    monkeypatch.setattr(
        mm,
        "query_one",
        lambda sql, args=(): {"snapshot_date": date(2026, 5, 17)},
    )

    def fake_query(sql, args=()):
        calls.append((sql, args))
        return [
            {
                "rank_position": 1,
                "product_id": "gid-1",
                "product_name": "Cool Widget",
                "product_url": "https://shop.example/products/cool-widget-rjc",
                "store": "7662984",
                "sales_count": 9,
                "order_count": 8,
                "revenue_main": "123.45",
            }
        ]

    monkeypatch.setattr(mm, "query", fake_query)

    snapshot, rows = mm.latest_top_products(limit=500)

    assert snapshot == "2026-05-17"
    assert rows[0]["product_code"] == "cool-widget"
    assert rows[0]["shopify_product_id"] == "gid-1"
    assert "ORDER BY rank_position ASC" in calls[0][0]
    assert calls[0][1] == ("2026-05-17", 500)


def test_flatten_mingkong_materials_keeps_all_visible_videos():
    product = {
        "id": 901,
        "product_name": "MK Cool",
        "product_links": ["https://shop.example/products/cool-widget-rjc"],
        "main_image": "uploads2/main.jpg",
        "videos": [
            {
                "name": "a.mp4",
                "path": "./medias/uploads2/a.mp4",
                "spends": "1.5万",
                "ads_count": 3,
            },
            {
                "name": "hidden.mp4",
                "path": "uploads2/h.mp4",
                "hidden": True,
                "spends": "999",
            },
            {
                "name": "b.mp4",
                "path": "uploads2/b.mp4",
                "image_path": "uploads2/b.jpg",
                "spends": "20",
                "ads_count": 1,
            },
        ],
    }

    rows = mm.flatten_materials_for_product(
        source_product={
            "product_code": "cool-widget",
            "rank_position": 1,
            "shopify_product_id": "gid-1",
            "product_name": "Cool Widget",
            "product_url": "https://shop.example/products/cool-widget-rjc",
        },
        mk_product=product,
    )

    assert [row["video_path"] for row in rows] == ["uploads2/a.mp4", "uploads2/b.mp4"]
    assert rows[0]["cumulative_90_spend"] == 15000.0
    assert rows[0]["video_spends_text"] == "1.5万"
    assert rows[0]["material_key"] == mm.material_key_for(
        "cool-widget",
        901,
        "uploads2/a.mp4",
    )
    assert rows[0]["mk_product_link"] == "https://shop.example/products/cool-widget-rjc"
    assert rows[1]["video_image_path"] == "uploads2/b.jpg"


def test_product_video_aggregate_stats_counts_pathless_non_hidden_rows():
    product = {
        "id": 2919,
        "videos": [
            {"name": "playable.mp4", "path": "uploads2/playable.mp4", "spends": "18.13万", "ads_count": 237},
            {"name": "ai-image.png", "path": "", "spends": "0", "ads_count": 39},
            {"name": "hidden.mp4", "path": "uploads2/hidden.mp4", "hidden": True, "spends": "999", "ads_count": 999},
        ],
    }

    stats = mm.product_video_aggregate_stats(product)
    playable_rows = mm.flatten_materials_for_product(
        source_product={
            "product_code": "21-fitness-resistance-bands-4-tube-pedal-ankle-puller",
            "rank_position": 1,
            "shopify_product_id": "gid-1",
            "product_name": "Fitness Bands",
            "product_url": "https://shop.example/products/21-fitness-resistance-bands-4-tube-pedal-ankle-puller",
        },
        mk_product=product,
    )

    assert stats == {
        "video_count": 2,
        "total_90_spend": 181300.0,
        "total_ads": 276,
    }
    assert len(playable_rows) == 1


def test_select_mingkong_product_requires_exact_result_product_code():
    rjc_suffix_result = {
        "id": 901,
        "product_links": ["https://shop.example/products/cool-widget-rjc"],
        "videos": [
            {
                "name": "rjc.mp4",
                "path": "uploads2/rjc.mp4",
                "spends": "9.9万",
                "ads_count": 99,
            }
        ],
    }
    exact_result = {
        "id": 902,
        "product_links": ["https://shop.example/products/cool-widget"],
        "videos": [
            {
                "name": "exact.mp4",
                "path": "uploads2/exact.mp4",
                "spends": "10",
                "ads_count": 1,
            }
        ],
    }

    selected = mm._select_mingkong_product(
        [rjc_suffix_result, exact_result],
        "cool-widget",
    )

    assert selected["id"] == 902
    assert mm._select_mingkong_product([rjc_suffix_result], "cool-widget") is None


def test_material_library_recovers_spend_from_raw_metadata_when_numeric_row_is_zero(
    monkeypatch,
):
    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def fake_query_one(sql, args=()):
        if "FROM mingkong_material_daily_snapshots" in sql and "GROUP BY" in sql:
            return {
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
            }
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 1}
        if "mingkong_material_sync_runs" in sql:
            return {
                "status": "success",
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
                "summary_json": "{}",
            }
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        return [
            {
                "id": 1,
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
                "ranking_snapshot_date": date(2026, 5, 17),
                "material_key": "abc",
                "product_code": "fitness-band",
                "rank_position": 7,
                "video_name": "2026.03.03.mp4",
                "video_path": "uploads2/winner.mp4",
                "video_image_path": "uploads2/winner.jpg",
                "cumulative_90_spend": 0,
                "video_ads_count": 12,
                "mk_video_metadata_json": '{"spends":"3.05万"}',
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    result = mm.list_material_library(keyword="fitness-band")

    item = result["items"][0]
    assert item["video_spends"] == 30500.0
    assert item["cumulative_90_spend"] == 30500.0
    assert item["video_spends_text"] == "3.05万"


def test_material_library_includes_yesterday_spend_delta(monkeypatch):
    captured = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def fake_query_one(sql, args=()):
        captured.append(("query_one", sql, args))
        if "FROM mingkong_material_daily_snapshots" in sql and "GROUP BY" in sql:
            return {
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
            }
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 1}
        if "mingkong_material_sync_runs" in sql:
            return {
                "status": "success",
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
                "summary_json": "{}",
            }
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        captured.append(("query", sql, args))
        if "SELECT snapshot_date, snapshot_at, snapshot_slot" in sql:
            return [
                {
                    "snapshot_date": date(2026, 5, 17),
                    "snapshot_at": datetime(2026, 5, 17, 18, 2, 0),
                    "snapshot_slot": "1800",
                }
            ]
        if "material_key IN" in sql:
            return [
                {
                    "material_key": "abc",
                    "cumulative_90_spend": 11800,
                    "mk_video_metadata_json": "{}",
                }
            ]
        return [
            {
                "id": 1,
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
                "ranking_snapshot_date": date(2026, 5, 17),
                "material_key": "abc",
                "product_code": "fitness-band",
                "rank_position": 7,
                "video_name": "2026.03.03.mp4",
                "video_path": "uploads2/winner.mp4",
                "video_image_path": "uploads2/winner.jpg",
                "cumulative_90_spend": 12300,
                "video_ads_count": 12,
                "mk_video_metadata_json": "{}",
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    result = mm.list_material_library(keyword="fitness-band")

    item = result["items"][0]
    assert item["current_cumulative_90_spend"] == 12300.0
    assert item["previous_cumulative_90_spend"] == 11800.0
    assert item["yesterday_spend_delta"] == 500.0
    assert item["previous_snapshot_at"] == "2026-05-17 18:02:00"
    assert any("material_key IN" in entry[1] for entry in captured if entry[0] == "query")


def test_build_top100_rows_recovers_spend_from_raw_metadata_when_numeric_row_is_zero():
    rows = mm.build_top100_rows(
        snapshot_date="2026-05-18",
        snapshot_at="2026-05-18 18:00:00",
        previous_snapshot_date=None,
        previous_snapshot_at=None,
        comparison_interval_seconds=None,
        current_rows=[
            {
                "material_key": "fresh",
                "cumulative_90_spend": 0,
                "video_ads_count": 12,
                "rank_position": 1,
                "mk_video_metadata_json": '{"spends":"3.05万"}',
            }
        ],
        previous_by_key={},
        previous_top100_keys=set(),
    )

    assert rows[0]["current_cumulative_90_spend"] == 30500.0
    assert rows[0]["yesterday_spend_delta"] == 30500.0


def test_build_top100_rows_defaults_to_top300():
    rows = mm.build_top100_rows(
        snapshot_date="2026-05-18",
        snapshot_at="2026-05-18 18:00:00",
        previous_snapshot_date=None,
        previous_snapshot_at=None,
        comparison_interval_seconds=None,
        current_rows=[
            {
                "material_key": f"material-{index:03d}",
                "cumulative_90_spend": 1000 - index,
                "video_ads_count": 1,
                "rank_position": index,
            }
            for index in range(1, 306)
        ],
        previous_by_key={},
        previous_top100_keys=set(),
    )

    assert len(rows) == 300
    assert {row["rank_position"] for row in rows} == set(range(1, 301))


def test_build_top100_rows_marks_new_entry_and_clamps_negative_delta():
    current = [
        {
            "material_key": "fresh",
            "cumulative_90_spend": 500.0,
            "video_ads_count": 4,
            "rank_position": 1,
        },
        {
            "material_key": "old",
            "cumulative_90_spend": 150.0,
            "video_ads_count": 2,
            "rank_position": 2,
        },
        {
            "material_key": "reset",
            "cumulative_90_spend": 10.0,
            "video_ads_count": 9,
            "rank_position": 3,
        },
    ]
    previous_by_key = {
        "old": {"cumulative_90_spend": 100.0},
        "reset": {"cumulative_90_spend": 30.0},
    }
    previous_top100_keys = {"old"}

    rows = mm.build_top100_rows(
        snapshot_date="2026-05-18",
        snapshot_at="2026-05-18 18:00:00",
        previous_snapshot_date="2026-05-17",
        previous_snapshot_at="2026-05-17 18:02:00",
        comparison_interval_seconds=86280,
        current_rows=current,
        previous_by_key=previous_by_key,
        previous_top100_keys=previous_top100_keys,
        limit=100,
    )

    assert rows[0]["material_key"] == "fresh"
    assert rows[0]["yesterday_spend_delta"] == 500.0
    assert rows[0]["is_new_material"] is True
    assert rows[0]["is_new_top100_entry"] is True
    assert rows[0]["snapshot_at"] == "2026-05-18 18:00:00"
    assert rows[0]["previous_snapshot_at"] == "2026-05-17 18:02:00"
    assert rows[0]["comparison_interval_seconds"] == 86280
    assert rows[0]["rank_position"] == 1
    assert rows[0]["display_position"] == 1
    assert rows[1]["material_key"] == "reset"
    assert rows[1]["yesterday_spend_delta"] == 0.0
    assert rows[1]["is_new_top100_entry"] is True
    assert rows[2]["material_key"] == "old"
    assert rows[2]["yesterday_spend_delta"] == 50.0
    assert rows[2]["is_new_top100_entry"] is False


def test_choose_previous_snapshot_prefers_candidate_closest_to_24_hours():
    chosen = mm.choose_previous_snapshot_for_24h(
        "2026-05-19 18:03:00",
        [
            {
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 6, 2, 0),
                "snapshot_slot": "0600",
            },
            {
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 1, 0),
                "snapshot_slot": "1800",
            },
            {
                "snapshot_date": date(2026, 5, 17),
                "snapshot_at": datetime(2026, 5, 17, 18, 0, 0),
                "snapshot_slot": "1800",
            },
        ],
    )

    assert chosen is not None
    assert chosen["snapshot_at"] == "2026-05-18 18:01:00"
    assert chosen["snapshot_slot"] == "1800"
    assert chosen["comparison_interval_seconds"] == 86520


def test_previous_material_snapshot_prefers_compatible_source_count(monkeypatch):
    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def fake_query(sql, args=()):
        if "JOIN mingkong_material_sync_runs" in sql:
            return [
                {
                    "snapshot_date": date(2026, 5, 20),
                    "snapshot_at": datetime(2026, 5, 20, 13, 27, 11),
                    "snapshot_slot": "1700",
                    "source_product_count": 500,
                    "source_product_limit": 500,
                },
                {
                    "snapshot_date": date(2026, 5, 20),
                    "snapshot_at": datetime(2026, 5, 20, 6, 0, 12),
                    "snapshot_slot": "0600",
                    "source_product_count": 300,
                    "source_product_limit": 300,
                },
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(mm, "query", fake_query)

    chosen = mm._previous_material_snapshot_for(
        snapshot_date="2026-05-21",
        snapshot_at="2026-05-21 05:00:02",
        min_source_product_count=500,
        min_source_product_limit=500,
    )

    assert chosen["snapshot_at"] == "2026-05-20 13:27:11"
    assert chosen["source_product_count"] == 500


def test_build_top100_rows_does_not_inflate_untracked_products():
    rows = mm.build_top100_rows(
        snapshot_date="2026-05-21",
        snapshot_at="2026-05-21 05:00:02",
        previous_snapshot_date="2026-05-20",
        previous_snapshot_at="2026-05-20 13:27:11",
        comparison_interval_seconds=55971,
        current_rows=[
            {
                "material_key": "unknown-product-video",
                "product_code": "newly-tracked-product",
                "cumulative_90_spend": 100000.0,
                "video_ads_count": 9,
                "rank_position": 1,
            },
            {
                "material_key": "new-video-on-known-product",
                "product_code": "known-product",
                "cumulative_90_spend": 1200.0,
                "video_ads_count": 2,
                "rank_position": 2,
            },
            {
                "material_key": "matched-video",
                "product_code": "matched-product",
                "cumulative_90_spend": 900.0,
                "video_ads_count": 1,
                "rank_position": 3,
            },
        ],
        previous_by_key={"matched-video": {"cumulative_90_spend": 700.0}},
        previous_product_codes={"known-product", "matched-product"},
        previous_top100_keys=set(),
        limit=100,
    )

    by_key = {row["material_key"]: row for row in rows}
    assert by_key["unknown-product-video"]["yesterday_spend_delta"] == 0.0
    assert by_key["new-video-on-known-product"]["yesterday_spend_delta"] == 1200.0
    assert by_key["matched-video"]["yesterday_spend_delta"] == 200.0
    assert rows[0]["material_key"] == "new-video-on-known-product"


def test_generate_daily_top100_builds_top300_archive(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        mm,
        "_latest_snapshot_identity",
        lambda table, snapshot_date=None: {
            "snapshot_date": "2026-05-18",
            "snapshot_at": "2026-05-18 18:00:00",
            "snapshot_slot": "1800",
        },
    )
    monkeypatch.setattr(
        mm,
        "_snapshot_run_metadata",
        lambda snapshot_at: {"source_product_count": 500, "source_product_limit": 500},
    )
    monkeypatch.setattr(mm, "_previous_material_snapshot_for", lambda **kwargs: None)
    monkeypatch.setattr(
        mm,
        "_snapshot_rows_by_date",
        lambda snapshot_date, snapshot_at=None: [
            {
                "material_key": f"material-{index:03d}",
                "cumulative_90_spend": 1000 - index,
                "video_ads_count": 1,
                "rank_position": index,
            }
            for index in range(1, 306)
        ],
    )
    monkeypatch.setattr(mm, "_previous_top100_keys", lambda snapshot_date, snapshot_at=None: set())

    def fake_replace(rows):
        captured["rows"] = rows
        return len(rows)

    monkeypatch.setattr(mm, "_replace_top100_rows", fake_replace)

    result = mm.generate_daily_top100("2026-05-18", "2026-05-18 18:00:00")

    assert len(captured["rows"]) == 300
    assert result["top100_count"] == 300
    assert result["top300_count"] == 300


def test_select_mingkong_product_prefers_spend_over_video_count_after_exact_match():
    items = [
        {
            "id": 1,
            "product_links": ["https://shop.example/products/cool-widget"],
            "videos": [
                {"path": "uploads2/low-a.mp4", "spends": "1", "ads_count": 1},
                {"path": "uploads2/low-b.mp4", "spends": "1", "ads_count": 1},
                {"path": "uploads2/low-c.mp4", "spends": "1", "ads_count": 1},
            ],
        },
        {
            "id": 2,
            "product_links": ["https://shop.example/products/cool-widget"],
            "videos": [
                {"path": "uploads2/high.mp4", "spends": "99", "ads_count": 9},
            ],
        },
    ]

    selected = mm._select_mingkong_product(items, "cool-widget")

    assert selected["id"] == 2


def test_fetch_mingkong_product_detail_supplies_video_spends_for_snapshot():
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 0,
                "data": {
                    "item": {
                        "id": 901,
                        "product_name": "MK Cool Detail",
                        "videos": [
                            {
                                "name": "winner.mp4",
                                "path": "uploads2/winner.mp4",
                                "spends": "7.26万",
                                "ads_count": 26,
                            }
                        ],
                    }
                },
            }

    class FakeSession:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    selected_from_search = {
        "id": 901,
        "product_name": "MK Cool",
        "product_links": ["https://shop.example/products/cool-widget"],
        "videos": [
            {
                "name": "winner.mp4",
                "path": "uploads2/winner.mp4",
                "ads_count": 26,
            }
        ],
    }

    detail = mm._fetch_mingkong_product_detail(
        FakeSession(),
        base_url="https://os.wedev.vip",
        headers={"Authorization": "Bearer token"},
        mk_product=selected_from_search,
        timeout_seconds=20,
    )
    rows = mm.flatten_materials_for_product(
        source_product={
            "product_code": "cool-widget",
            "rank_position": 1,
            "shopify_product_id": "gid-1",
            "product_name": "Cool Widget",
            "product_url": "https://shop.example/products/cool-widget",
        },
        mk_product=detail,
    )

    assert calls[0][0] == "https://os.wedev.vip/api/marketing/medias/901"
    assert detail["product_links"] == ["https://shop.example/products/cool-widget"]
    assert rows[0]["cumulative_90_spend"] == 72600.0
    assert rows[0]["video_spends_text"] == "7.26万"


def test_search_mingkong_items_retries_once_after_auto_login_refresh(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeSession:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            if len(calls) == 1:
                return FakeResponse({"is_guest": True, "message": "登录已失效"})
            return FakeResponse({"data": {"items": [{"id": 901}]}})

    refreshes = []
    monkeypatch.setattr(
        mm,
        "_refresh_mingkong_headers_after_login",
        lambda product_code, timeout_seconds: refreshes.append((product_code, timeout_seconds))
        or {"Authorization": "Bearer refreshed"},
    )

    items = mm._search_mingkong_items(
        FakeSession(),
        base_url="https://os.wedev.vip",
        headers={"Authorization": "Bearer expired"},
        product_code="cool-widget",
        timeout_seconds=20,
    )

    assert items == [{"id": 901}]
    assert refreshes == [("cool-widget", 20)]
    assert calls[1][1]["headers"] == {"Authorization": "Bearer refreshed"}


def test_search_mingkong_items_does_not_loop_when_refreshed_credentials_still_expired(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"is_guest": True, "message": "登录已失效"}

    class FakeSession:
        def __init__(self):
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            return FakeResponse()

    session = FakeSession()
    monkeypatch.setattr(mm, "_refresh_mingkong_headers_after_login", lambda *args, **kwargs: {"Cookie": "new"})

    try:
        mm._search_mingkong_items(
            session,
            base_url="https://os.wedev.vip",
            headers={"Cookie": "old"},
            product_code="cool-widget",
            timeout_seconds=20,
        )
    except RuntimeError as exc:
        assert str(exc) == "Mingkong credentials expired"
    else:
        raise AssertionError("expected expired credentials error")

    assert session.calls == 2


def test_cache_local_cover_for_material_writes_local_media_object():
    calls = []

    class FakeResponse:
        status_code = 200
        content = b"cover-bytes"
        headers = {"content-type": "image/jpeg"}

        def raise_for_status(self):
            return None

    class FakeSession:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    writes = []

    row = mm.cache_local_cover_for_material(
        {
            "material_key": "a" * 64,
            "video_image_path": "./medias/uploads2/winner.jpg",
        },
        session=FakeSession(),
        base_url="https://os.wedev.vip",
        headers={"Authorization": "Bearer token"},
        timeout_seconds=10,
        storage_exists_fn=lambda object_key: False,
        write_bytes_fn=lambda object_key, payload: writes.append((object_key, payload)) or Path("/tmp/cover.jpg"),
    )

    assert row["local_cover_object_key"] == (
        "artifacts/mingkong-material-covers/aa/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg"
    )
    assert row["cover_cache_error"] is None
    assert writes == [(row["local_cover_object_key"], b"cover-bytes")]
    assert calls[0][0] == "https://os.wedev.vip/medias/uploads2/winner.jpg"
    assert calls[0][1]["headers"]["Accept"].startswith("image/")


def test_cache_local_cover_for_material_records_failure_without_raising():
    class BrokenSession:
        def get(self, url, **kwargs):
            raise RuntimeError("network down")

    row = mm.cache_local_cover_for_material(
        {
            "material_key": "b" * 64,
            "video_image_path": "uploads2/missing.jpg",
        },
        session=BrokenSession(),
        base_url="https://os.wedev.vip",
        headers={"Authorization": "Bearer token"},
        timeout_seconds=10,
        storage_exists_fn=lambda object_key: False,
        write_bytes_fn=lambda object_key, payload: None,
    )

    assert row["local_cover_object_key"] is None
    assert row["cover_cache_error"] == "network down"


def test_upsert_snapshot_rows_writes_duplicate_update(monkeypatch):
    writes = []

    monkeypatch.setattr(mm, "execute", lambda sql, args=(): writes.append((sql, args)) or 1)

    rows = [
        {
            "material_key": "abc",
            "product_code": "cool-widget",
            "rank_position": 7,
            "shopify_product_id": "gid-1",
            "product_name": "Cool Widget",
            "product_url": "https://shop.example/products/cool-widget",
            "mk_product_id": 901,
            "mk_product_name": "MK Cool",
            "mk_product_link": "https://shop.example/products/cool-widget-rjc",
            "main_image": "uploads2/main.jpg",
            "video_name": "winner.mp4",
            "video_path": "uploads2/winner.mp4",
            "video_image_path": "uploads2/winner.jpg",
            "local_cover_object_key": "artifacts/mingkong-material-covers/ab/abc.jpg",
            "cover_cache_error": None,
            "cumulative_90_spend": 12000.0,
            "video_ads_count": 9,
            "video_author": "Bob",
            "video_upload_time": "2026-05-17T10:00:00",
            "video_duration_seconds": 12.5,
            "mk_video_metadata": {"spends": 12000},
        }
    ]

    count = mm.upsert_snapshot_rows(
        run_id=42,
        snapshot_date="2026-05-18",
        ranking_snapshot_date="2026-05-17",
        rows=rows,
    )

    assert count == 1
    assert "INSERT INTO mingkong_material_daily_snapshots" in writes[0][0]
    assert "ON DUPLICATE KEY UPDATE" in writes[0][0]
    assert writes[0][1][0] == "2026-05-18"
    assert writes[0][1][3] == "abc"
    assert "local_cover_object_key" in writes[0][0]
    assert writes[0][1][-1] == '{"spends": 12000}'


def test_record_product_status_writes_product_aggregate_columns(monkeypatch):
    writes = []

    monkeypatch.setattr(mm, "execute", lambda sql, args=(): writes.append((sql, args)) or 1)

    mm.record_product_status(
        run_id=42,
        snapshot_date="2026-05-20",
        snapshot_at="2026-05-20 05:00:00",
        snapshot_slot="0500",
        ranking_snapshot_date="2026-05-18",
        source_product={
            "product_code": "21-fitness-resistance-bands-4-tube-pedal-ankle-puller",
            "rank_position": 1,
            "shopify_product_id": "gid-1",
            "product_name": "Fitness Bands",
            "product_url": "https://shop.example/products/21-fitness-resistance-bands-4-tube-pedal-ankle-puller",
        },
        status="success",
        material_count=49,
        video_count=55,
        path_video_count=49,
        total_90_spend=181314.0,
        total_ads=276,
        mk_product={
            "id": 2919,
            "product_name": "健身脚蹬拉力器",
            "product_links": ["https://shop.example/products/21-fitness-resistance-bands-4-tube-pedal-ankle-puller"],
        },
    )

    sql, args = writes[0]
    assert "INSERT INTO mingkong_material_products" in sql
    for column in ["video_count", "path_video_count", "total_90_spend", "total_ads"]:
        assert column in sql
    assert 55 in args
    assert 49 in args
    assert 181314.0 in args
    assert 276 in args


def test_list_material_library_serializes_latest_snapshot(monkeypatch):
    captured = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def fake_query_one(sql, args=()):
        captured.append(("query_one", sql, args))
        if "FROM mingkong_material_daily_snapshots" in sql and "GROUP BY" in sql:
            return {
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
            }
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 1}
        if "mingkong_material_sync_runs" in sql:
            return {
                "status": "success",
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
                "summary_json": '{"processed": 300}',
            }
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        captured.append(("query", sql, args))
        return [
            {
                "id": 1,
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
                "ranking_snapshot_date": date(2026, 5, 17),
                "material_key": "abc",
                "product_code": "cool-widget",
                "rank_position": 7,
                "video_name": "winner.mp4",
                "video_path": "uploads2/winner.mp4",
                "video_image_path": "uploads2/winner.jpg",
                "local_cover_object_key": "artifacts/mingkong-material-covers/ab/abc.jpg",
                "cover_cached_at": None,
                "cover_cache_error": None,
                "cumulative_90_spend": 12000,
                "video_ads_count": 9,
                "mk_video_metadata_json": '{"video_path":"uploads2/winner.mp4"}',
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    result = mm.list_material_library(keyword="winner", page=1, page_size=100)

    assert result["snapshot"] == "2026-05-18"
    assert result["snapshot_at"] == "2026-05-18 18:00:00"
    assert result["total"] == 1
    assert result["run_summary"]["summary"] == {"processed": 300}
    assert result["items"][0]["video_spends"] == 12000.0
    assert result["items"][0]["local_cover_url"] == (
        "/medias/object?object_key=artifacts%2Fmingkong-material-covers%2Fab%2Fabc.jpg"
    )
    assert result["items"][0]["mk_video_metadata"] == {"video_path": "uploads2/winner.mp4"}
    assert any(
        "ORDER BY s.cumulative_90_spend DESC" in item[1]
        for item in captured
        if item[0] == "query"
    )


def test_list_material_library_keyword_matches_product_code_rjc_variants(monkeypatch):
    captured = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def fake_query_one(sql, args=()):
        captured.append(("query_one", sql, args))
        if "FROM mingkong_material_daily_snapshots" in sql and "GROUP BY" in sql:
            return {
                "snapshot_date": date(2026, 5, 22),
                "snapshot_at": datetime(2026, 5, 22, 5, 0, 0),
                "snapshot_slot": "0500",
            }
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 1}
        if "mingkong_material_sync_runs" in sql:
            return {
                "status": "success",
                "snapshot_date": date(2026, 5, 22),
                "snapshot_at": datetime(2026, 5, 22, 5, 0, 0),
                "snapshot_slot": "0500",
                "summary_json": "{}",
            }
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        captured.append(("query", sql, args))
        return []

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    mm.list_material_library(keyword="cool-widget-rjc", page=1, page_size=100)

    count_args = next(args for kind, sql, args in captured if kind == "query_one" and "COUNT(*) AS cnt" in sql)
    assert "%cool-widget%" in count_args
    assert "%cool-widget-rjc%" in count_args


def test_list_material_library_keyword_matches_video_filename_and_uses_same_filter(monkeypatch):
    captured = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def fake_query_one(sql, args=()):
        captured.append(("query_one", sql, args))
        if "FROM mingkong_material_daily_snapshots" in sql and "GROUP BY" in sql:
            return {
                "snapshot_date": date(2026, 5, 22),
                "snapshot_at": datetime(2026, 5, 22, 5, 0, 0),
                "snapshot_slot": "0500",
            }
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 1}
        if "mingkong_material_sync_runs" in sql:
            return {
                "status": "success",
                "snapshot_date": date(2026, 5, 22),
                "snapshot_at": datetime(2026, 5, 22, 5, 0, 0),
                "snapshot_slot": "0500",
                "summary_json": "{}",
            }
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        captured.append(("query", sql, args))
        return []

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    mm.list_material_library(keyword="family-memory-card-game.mp4", page=1, page_size=100)

    count_sql = next(sql for kind, sql, args in captured if kind == "query_one" and "COUNT(*) AS cnt" in sql)
    list_sql = next(sql for kind, sql, args in captured if kind == "query" and "ORDER BY s.cumulative_90_spend DESC" in sql)
    assert "s.video_name LIKE %s" in count_sql
    assert "s.video_path LIKE %s" in count_sql
    assert "s.video_name LIKE %s" in list_sql
    assert "s.video_path LIKE %s" in list_sql


def test_list_material_snapshot_options_lists_successful_material_runs(monkeypatch):
    captured = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def fake_query(sql, args=()):
        captured.append((sql, args))
        assert "FROM mingkong_material_sync_runs" in sql
        assert "status = 'success'" in sql
        return [
            {
                "snapshot_date": date(2026, 6, 1),
                "snapshot_at": datetime(2026, 6, 1, 5, 0, 4),
                "snapshot_slot": "0500",
                "material_count": 4920,
            }
        ]

    monkeypatch.setattr(mm, "query", fake_query)

    result = mm.list_material_snapshot_options(limit=60)

    assert result == {
        "items": [
            {
                "snapshot": "2026-06-01",
                "snapshot_at": "2026-06-01 05:00:04",
                "snapshot_slot": "0500",
                "material_count": 4920,
            }
        ],
        "default_snapshot": "2026-06-01",
    }
    assert captured[0][1] == (60,)


def test_list_material_library_all_without_keyword_reads_all_historical_snapshots(monkeypatch):
    captured = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)
    monkeypatch.setattr(mm, "_enrich_cached_ad_statuses", lambda items: items)
    monkeypatch.setattr(mm, "_enrich_material_yesterday_delta", lambda items, **_: items)

    def fake_query_one(sql, args=()):
        captured.append(("query_one", sql, args))
        if "COUNT(*) AS cnt" in sql:
            assert "BETWEEN" not in sql
            assert "snapshot_date = %s" not in sql
            return {"cnt": 1}
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        captured.append(("query", sql, args))
        assert "BETWEEN" not in sql
        assert "snapshot_date = %s" not in sql
        return [
            {
                "id": 1,
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
                "ranking_snapshot_date": date(2026, 5, 17),
                "material_key": "abc",
                "product_code": "cool-widget",
                "rank_position": 7,
                "product_name": "Cool Widget",
                "product_url": "https://shop.example/products/cool-widget",
                "mk_product_id": 901,
                "mk_product_name": "MK Cool",
                "mk_product_link": "https://shop.example/products/cool-widget-rjc",
                "video_name": "winner.mp4",
                "video_path": "uploads2/winner.mp4",
                "video_image_path": "uploads2/winner.jpg",
                "local_cover_object_key": "",
                "cover_cached_at": None,
                "cover_cache_error": None,
                "cumulative_90_spend": 12000,
                "video_ads_count": 9,
                "mk_video_metadata_json": "{}",
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    result = mm.list_material_library(range_key="all", page=1, page_size=100)

    assert result["range"] == "all"
    assert result["snapshot"] == ""
    assert result["total"] == 1
    assert result["items"][0]["video_name"] == "winner.mp4"
    assert any(
        "GROUP BY s.material_key" in sql
        for kind, sql, _args in captured
        if kind == "query"
    )


def test_list_material_library_all_keyword_uses_live_search_and_rjc_variant(monkeypatch):
    searched = []
    fetched_ids = []

    keyword = "scratch-free-5-finger-wash-mitt-rjc"
    base_code = "scratch-free-5-finger-wash-mitt"

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)
    monkeypatch.setattr(mm, "_enrich_cached_ad_statuses", lambda items: items)
    monkeypatch.setattr(mm, "query_one", lambda sql, args=(): (_ for _ in ()).throw(AssertionError(sql)))
    monkeypatch.setattr(mm, "query", lambda sql, args=(): (_ for _ in ()).throw(AssertionError(sql)))
    monkeypatch.setattr(mm, "_mk_headers", lambda: {"Authorization": "Bearer test"})
    monkeypatch.setattr(mm, "_mk_base_url", lambda: "https://mk.example")

    def fake_search(session, *, base_url, headers, product_code, timeout_seconds, allow_login_refresh=True):
        searched.append(product_code)
        if product_code == keyword:
            return [
                {
                    "id": 3754,
                    "product_name": "五指洗车手套 RJC",
                    "product_links": [f"https://shop.example/products/{keyword}"],
                }
            ]
        if product_code == base_code:
            return [
                {
                    "id": 3337,
                    "product_name": "五指洗车手套",
                    "product_links": [f"https://shop.example/products/{base_code}"],
                }
            ]
        return []

    def fake_fetch(session, *, base_url, headers, mk_product, timeout_seconds, allow_login_refresh=True):
        fetched_ids.append(int(mk_product["id"]))
        if int(mk_product["id"]) == 3337:
            return {
                **mk_product,
                "main_image": "https://cdn.example/base.jpg",
                "videos": [
                    {
                        "name": "2026.03.20-五指洗车手套-原素材-指派-李文龙.mp4",
                        "path": "uploads2/2026.03.20-五指洗车手套-原素材-指派-李文龙.mp4",
                        "image_path": "uploads2/base.jpg",
                        "spends": "8.04万",
                        "ads_count": 29,
                        "author": "李文龙",
                    }
                ],
            }
        return {
            **mk_product,
            "main_image": "https://cdn.example/rjc.jpg",
            "videos": [
                {
                    "name": "2026.04.22-scratch-free-5-finger-wash-mitt-AI图片素材-11-陈绍坤.png",
                    "path": "uploads2/localized.png",
                    "image_path": "uploads2/localized.jpg",
                    "spends": "295",
                    "ads_count": 2,
                    "author": "陈绍坤",
                }
            ],
        }

    monkeypatch.setattr(mm, "_search_mingkong_items", fake_search)
    monkeypatch.setattr(mm, "_fetch_mingkong_product_detail", fake_fetch)

    result = mm.list_material_library(
        range_key="all",
        keyword=keyword,
        page=1,
        page_size=100,
    )

    assert result["range"] == "all"
    assert result["snapshot"] == ""
    assert keyword in searched
    assert base_code in searched
    assert set(fetched_ids) == {3337, 3754}
    assert result["total"] == 2
    names = [item["video_name"] for item in result["items"]]
    assert "2026.03.20-五指洗车手套-原素材-指派-李文龙.mp4" in names
    original = next(
        item for item in result["items"]
        if item["mk_product_id"] == 3337
    )
    assert original["product_code"] == base_code
    assert round(original["video_spends"], 2) == 80400.0
    assert original["video_author"] == "李文龙"


def test_list_material_library_range_sorts_by_video_90_day_spend_first(monkeypatch):
    captured = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)
    monkeypatch.setattr(mm, "_today", lambda: date(2026, 5, 20))
    monkeypatch.setattr(mm, "_enrich_material_yesterday_delta", lambda items, **_: items)

    def fake_query_one(sql, args=()):
        captured.append(("query_one", sql, args))
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 1}
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        captured.append(("query", sql, args))
        return [
            {
                "id": 1,
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
                "ranking_snapshot_date": date(2026, 5, 17),
                "material_key": "abc",
                "product_code": "cool-widget",
                "rank_position": 7,
                "video_name": "winner.mp4",
                "video_path": "uploads2/winner.mp4",
                "video_image_path": "uploads2/winner.jpg",
                "local_cover_object_key": "",
                "cover_cached_at": None,
                "cover_cache_error": None,
                "cumulative_90_spend": 12000,
                "video_ads_count": 9,
                "mk_video_metadata_json": "{}",
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    result = mm.list_material_library(range_key="this_week", page=1, page_size=100)

    assert result["total"] == 1
    assert any(
        "ORDER BY s.cumulative_90_spend DESC" in item[1]
        for item in captured
        if item[0] == "query"
    )


def test_list_material_library_range_uses_row_snapshot_for_yesterday_delta(monkeypatch):
    captured = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)
    monkeypatch.setattr(mm, "_today", lambda: date(2026, 5, 20))
    monkeypatch.setattr(mm, "_enrich_cached_ad_statuses", lambda items: items)

    def current_row():
        return {
            "id": 1,
            "snapshot_date": date(2026, 5, 20),
            "snapshot_at": datetime(2026, 5, 20, 5, 0, 0),
            "snapshot_slot": "0500",
            "ranking_snapshot_date": date(2026, 5, 19),
            "material_key": "abc",
            "product_code": "cool-widget",
            "rank_position": 7,
            "video_name": "winner.mp4",
            "video_path": "uploads2/winner.mp4",
            "video_image_path": "uploads2/winner.jpg",
            "local_cover_object_key": "",
            "cover_cached_at": None,
            "cover_cache_error": None,
            "cumulative_90_spend": 12000,
            "video_ads_count": 9,
            "mk_video_metadata_json": "{}",
            "created_at": None,
            "updated_at": None,
        }

    def fake_query_one(sql, args=()):
        captured.append(("query_one", sql, args))
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 1}
        if "FROM mingkong_material_sync_runs" in sql:
            return {
                "snapshot_at": datetime(2026, 5, 20, 5, 0, 0),
                "snapshot_slot": "0500",
                "ranking_snapshot_date": date(2026, 5, 19),
                "status": "success",
                "source_product_count": 300,
                "source_product_limit": 300,
            }
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        captured.append(("query", sql, args))
        if "SELECT s.*, COALESCE" in sql:
            return [current_row()]
        if "SELECT s.snapshot_date, s.snapshot_at, s.snapshot_slot" in sql:
            return [
                {
                    "snapshot_date": date(2026, 5, 19),
                    "snapshot_at": datetime(2026, 5, 19, 5, 1, 0),
                    "snapshot_slot": "0500",
                    "source_product_count": 300,
                    "source_product_limit": 300,
                    "ranking_snapshot_date": date(2026, 5, 18),
                }
            ]
        if "material_key IN" in sql:
            return [
                {
                    "material_key": "abc",
                    "cumulative_90_spend": 11500,
                    "mk_video_metadata_json": "{}",
                }
            ]
        if "SELECT DISTINCT product_code" in sql:
            return [{"product_code": "cool-widget"}]
        raise AssertionError(sql)

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    result = mm.list_material_library(range_key="this_week", page=1, page_size=100)

    item = result["items"][0]
    assert item["current_cumulative_90_spend"] == 12000.0
    assert item["previous_cumulative_90_spend"] == 11500.0
    assert item["yesterday_spend_delta"] == 500.0
    assert item["previous_snapshot_at"] == "2026-05-19 05:01:00"


def test_list_material_library_enriches_from_cached_ad_status(monkeypatch):
    captured = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def fake_query_one(sql, args=()):
        captured.append(("query_one", sql, args))
        if "FROM mingkong_material_daily_snapshots" in sql and "GROUP BY" in sql:
            return {
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
            }
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 1}
        if "mingkong_material_sync_runs" in sql:
            return {
                "status": "success",
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
                "summary_json": "{}",
            }
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        captured.append(("query", sql, args))
        if "FROM mingkong_material_ad_status_cache" in sql:
            if "status_scope = %s" in sql and args and args[0] == "product":
                return [
                    {
                        "status_scope": "product",
                        "lookup_hash": mm.status_lookup_hash("cool-widget-rjc"),
                        "lookup_key": "cool-widget-rjc",
                        "product_code": "cool-widget-rjc",
                        "media_product_id": 7,
                        "media_item_id": None,
                        "has_local_match": 1,
                        "has_running_ad": 1,
                        "ad_spend_usd": 123.45,
                        "latest_activity_at": datetime(2026, 5, 18, 12, 0, 0),
                        "summary_json": '{"source":"daily"}',
                        "refreshed_at": datetime(2026, 5, 18, 12, 5, 0),
                    }
                ]
            return [
                {
                    "status_scope": "material",
                    "lookup_hash": mm.status_lookup_hash("uploads2/winner.mp4"),
                    "lookup_key": "uploads2/winner.mp4",
                    "product_code": "cool-widget-rjc",
                    "media_product_id": 7,
                    "media_item_id": 11,
                    "has_local_match": 1,
                    "has_running_ad": 0,
                    "ad_spend_usd": 0,
                    "latest_activity_at": None,
                    "summary_json": '{"source":"media_item_mk_bindings"}',
                    "refreshed_at": datetime(2026, 5, 18, 12, 5, 0),
                }
            ]
        return [
            {
                "id": 1,
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
                "ranking_snapshot_date": date(2026, 5, 17),
                "material_key": "abc",
                "product_code": "cool-widget",
                "rank_position": 7,
                "video_name": "winner.mp4",
                "video_path": "/medias/uploads2/winner.mp4",
                "video_image_path": "uploads2/winner.jpg",
                "cumulative_90_spend": 12000,
                "video_ads_count": 9,
                "mk_video_metadata_json": "{}",
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    result = mm.list_material_library(keyword="winner", page=1, page_size=100)
    item = result["items"][0]

    assert item["media_search_code"] == "cool-widget-rjc"
    assert item["media_search_url"] == "/medias/?q=cool-widget-rjc"
    assert item["has_local_product_running_ad"] is True
    assert item["has_local_material_in_library"] is True
    assert item["has_local_material_running_ad"] is True
    assert item["product_ad_status"]["media_product_id"] == 7
    assert item["material_ad_status"]["media_item_id"] == 11
    assert item["material_ad_status"]["has_running_ad"] is False
    assert any("mingkong_material_ad_status_cache" in entry[1] for entry in captured if entry[0] == "query")


def test_enrich_cached_ad_statuses_attaches_product_ai_evaluation_result(monkeypatch):
    def fake_query(sql, args=()):
        if "FROM mingkong_material_ad_status_cache" in sql:
            if args and args[0] == "product":
                return [
                    {
                        "status_scope": "product",
                        "lookup_hash": mm.status_lookup_hash("cool-widget-rjc"),
                        "lookup_key": "cool-widget-rjc",
                        "product_code": "cool-widget-rjc",
                        "media_product_id": 7,
                        "media_item_id": None,
                        "has_local_match": 1,
                        "has_running_ad": 0,
                        "ad_spend_usd": 0,
                        "latest_activity_at": None,
                        "summary_json": "{}",
                        "refreshed_at": datetime(2026, 5, 18, 12, 0, 0),
                    }
                ]
            return []
        if "FROM media_products" in sql and "ai_evaluation_result" in sql:
            assert args == (7,)
            return [
                {
                    "id": 7,
                    "ai_evaluation_result": "\u9002\u5408\u63a8\u5e7f",
                    "ai_evaluation_detail": '{"countries":[{"lang":"DE","is_suitable":true}]}',
                }
            ]
        if "FROM ai_evaluation_runs" in sql or "FROM ai_country_evaluations" in sql:
            return []
        if "FROM media_items" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(mm, "query", fake_query)

    items = [
        {
            "product_code": "cool-widget",
            "video_path": "/medias/uploads2/winner.mp4",
        }
    ]

    enriched = mm._enrich_cached_ad_statuses(items)

    status = enriched[0]["product_ad_status"]
    assert status["media_product_id"] == 7
    assert status["ai_evaluation_result"] == "\u9002\u5408\u63a8\u5e7f"
    assert status["ai_evaluation_detail"] == '{"countries":[{"lang":"DE","is_suitable":true}]}'


def test_enrich_cached_ad_statuses_attaches_latest_fine_ai_evaluation_result(monkeypatch):
    def fake_query(sql, args=()):
        if "FROM mingkong_material_ad_status_cache" in sql:
            if args and args[0] == "product":
                return [
                    {
                        "status_scope": "product",
                        "lookup_hash": mm.status_lookup_hash("cool-widget-rjc"),
                        "lookup_key": "cool-widget-rjc",
                        "product_code": "cool-widget-rjc",
                        "media_product_id": 7,
                        "media_item_id": None,
                        "has_local_match": 1,
                        "has_running_ad": 0,
                        "ad_spend_usd": 0,
                        "latest_activity_at": None,
                        "summary_json": "{}",
                        "refreshed_at": datetime(2026, 5, 18, 12, 0, 0),
                    }
                ]
            return []
        if "FROM media_products" in sql and "ai_evaluation_result" in sql:
            return []
        if "FROM ai_evaluation_runs" in sql:
            assert args == (7,)
            return [
                {
                    "id": 9,
                    "evaluation_run_id": "eval_latest",
                    "product_id": 7,
                    "status": "completed",
                    "summary_json": '{"overall_recommendation":"GO"}',
                    "frontend_json": '{"decision_groups":{"go":["DE"]}}',
                    "progress_json": "{}",
                    "created_at": datetime(2026, 5, 22, 10, 0, 0),
                    "updated_at": datetime(2026, 5, 22, 10, 2, 0),
                    "completed_at": datetime(2026, 5, 22, 10, 2, 0),
                },
                {
                    "id": 8,
                    "evaluation_run_id": "eval_old",
                    "product_id": 7,
                    "status": "completed",
                    "summary_json": '{"overall_recommendation":"HOLD"}',
                    "frontend_json": "{}",
                    "progress_json": "{}",
                    "created_at": datetime(2026, 5, 21, 10, 0, 0),
                    "updated_at": datetime(2026, 5, 21, 10, 2, 0),
                    "completed_at": datetime(2026, 5, 21, 10, 2, 0),
                },
            ]
        if "FROM ai_country_evaluations" in sql:
            assert args == ("eval_latest",)
            return [
                {
                    "evaluation_run_id": "eval_latest",
                    "country_code": "DE",
                    "country_name": "Germany",
                    "status": "completed",
                    "scores_json": '{"overall_score":82}',
                    "decision_json": '{"final_decision":"GO"}',
                    "full_result_json": '{"status":"completed","country_code":"DE","country_name_zh":"德国","scores":{"overall_score":82},"decision":{"final_decision":"GO"}}',
                    "error_message": "",
                }
            ]
        if "FROM media_items" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(mm, "query", fake_query)

    enriched = mm._enrich_cached_ad_statuses([
        {
            "product_code": "cool-widget",
            "video_path": "/medias/uploads2/winner.mp4",
        }
    ])

    fine_ai = enriched[0]["product_ad_status"]["fine_ai_evaluation"]
    assert fine_ai["has_result"] is True
    assert fine_ai["evaluation_run_id"] == "eval_latest"
    assert fine_ai["status"] == "completed"
    assert fine_ai["summary"]["overall_recommendation"] == "GO"
    assert fine_ai["frontend"]["decision_groups"]["go"] == ["DE"]
    assert fine_ai["countries"]["DE"]["decision"]["final_decision"] == "GO"
    assert fine_ai["countries"]["DE"]["scores"]["overall_score"] == 82


def test_material_library_marks_video_in_library_only_within_matched_product(monkeypatch):
    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def fake_query_one(sql, args=()):
        if "FROM mingkong_material_daily_snapshots" in sql and "GROUP BY" in sql:
            return {
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
            }
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 2}
        if "mingkong_material_sync_runs" in sql:
            return {
                "status": "success",
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
                "summary_json": "{}",
            }
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        if "FROM mingkong_material_ad_status_cache" in sql:
            if args and args[0] == "product":
                return [
                    {
                        "status_scope": "product",
                        "lookup_hash": mm.status_lookup_hash("cool-widget-rjc"),
                        "lookup_key": "cool-widget-rjc",
                        "product_code": "cool-widget-rjc",
                        "media_product_id": 7,
                        "media_item_id": None,
                        "has_local_match": 1,
                        "has_running_ad": 1,
                        "ad_spend_usd": 42,
                        "latest_activity_at": None,
                        "summary_json": "{}",
                        "refreshed_at": datetime(2026, 5, 18, 12, 0, 0),
                    },
                    {
                        "status_scope": "product",
                        "lookup_hash": mm.status_lookup_hash("other-widget-rjc"),
                        "lookup_key": "other-widget-rjc",
                        "product_code": "other-widget-rjc",
                        "media_product_id": 8,
                        "media_item_id": None,
                        "has_local_match": 1,
                        "has_running_ad": 0,
                        "ad_spend_usd": 0,
                        "latest_activity_at": None,
                        "summary_json": "{}",
                        "refreshed_at": datetime(2026, 5, 18, 12, 0, 0),
                    },
                ]
            return []
        if "FROM media_items" in sql:
            return [
                {
                    "media_item_id": 11,
                    "media_product_id": 7,
                    "product_code": "cool-widget-rjc",
                    "filename": "winner.mp4",
                    "display_name": "winner.mp4",
                    "object_key": "7/medias/winner.mp4",
                    "created_at": datetime(2026, 5, 18, 13, 0, 0),
                }
            ]
        return [
            {
                "id": 1,
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
                "ranking_snapshot_date": date(2026, 5, 17),
                "material_key": "abc",
                "product_code": "cool-widget",
                "rank_position": 7,
                "video_name": "winner.mp4",
                "video_path": "/medias/uploads2/winner.mp4",
                "video_image_path": "uploads2/winner.jpg",
                "cumulative_90_spend": 12000,
                "video_ads_count": 9,
                "mk_video_metadata_json": "{}",
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": 2,
                "snapshot_date": date(2026, 5, 18),
                "snapshot_at": datetime(2026, 5, 18, 18, 0, 0),
                "snapshot_slot": "1800",
                "ranking_snapshot_date": date(2026, 5, 17),
                "material_key": "def",
                "product_code": "other-widget",
                "rank_position": 8,
                "video_name": "winner.mp4",
                "video_path": "/medias/uploads2/winner.mp4",
                "video_image_path": "uploads2/winner.jpg",
                "cumulative_90_spend": 8000,
                "video_ads_count": 3,
                "mk_video_metadata_json": "{}",
                "created_at": None,
                "updated_at": None,
            },
        ]

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    result = mm.list_material_library(keyword="winner", page=1, page_size=100)
    by_code = {item["product_code"]: item for item in result["items"]}

    assert by_code["cool-widget"]["has_local_material_in_library"] is True
    assert by_code["cool-widget"]["material_ad_status"]["media_item_id"] == 11
    assert by_code["cool-widget"]["material_ad_status"]["summary"]["source"] == "media_items_legacy_product_scope"
    assert by_code["other-widget"]["has_local_product_in_library"] is True
    assert by_code["other-widget"]["has_local_product_running_ad"] is False
    assert by_code["other-widget"]["has_local_material_in_library"] is False
    assert by_code["other-widget"]["material_ad_status"]["media_item_id"] is None


def test_refresh_ad_status_cache_materializes_product_and_material_rows(monkeypatch):
    writes = []
    finishes = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)
    monkeypatch.setattr(mm.scheduled_tasks, "start_run", lambda task_code: 101)
    monkeypatch.setattr(
        mm.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: finishes.append((run_id, kwargs)),
    )

    def fake_query(sql, args=()):
        if "SELECT DISTINCT product_code" in sql:
            return [{"product_code": "cool-widget"}]
        if "SELECT DISTINCT video_path" in sql:
            return [{"video_path": "/medias/uploads2/winner.mp4"}]
        raise AssertionError(sql)

    def fake_query_one(sql, args=()):
        if "FROM media_products" in sql:
            return {"id": 7, "product_code": "cool-widget-rjc", "name": "Cool Widget"}
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return {
                "ad_spend_usd": 88.5,
                "latest_activity_at": date(2026, 5, 18),
            }
        if "FROM meta_ad_realtime_daily_campaign_metrics" in sql:
            return {
                "ad_spend_usd": 12.5,
                "latest_activity_at": datetime(2026, 5, 18, 12, 0, 0),
            }
        if "FROM media_item_mk_bindings" in sql:
            return {
                "media_item_id": 11,
                "media_product_id": 7,
                "product_code": "cool-widget-rjc",
                "pushed_at": datetime(2026, 5, 18, 11, 0, 0),
                "push_success_count": 0,
            }
        raise AssertionError(sql)

    monkeypatch.setattr(mm, "query", fake_query)
    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "execute", lambda sql, args=(): writes.append((sql, args)) or 1)

    summary = mm.refresh_ad_status_cache()

    assert summary["product_statuses"] == 1
    assert summary["material_statuses"] == 1
    assert len(writes) == 2
    assert all("INSERT INTO mingkong_material_ad_status_cache" in sql for sql, _args in writes)
    assert writes[0][1][0] == "product"
    assert writes[0][1][2] == "cool-widget-rjc"
    assert writes[0][1][7] == 1
    assert writes[1][1][0] == "material"
    assert writes[1][1][2] == "uploads2/winner.mp4"
    assert writes[1][1][8] == 11
    assert finishes[0][0] == 101
    assert finishes[0][1]["status"] == "success"


def test_list_yesterday_top100_orders_new_entries_first(monkeypatch):
    captured = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def fake_query_one(sql, args=()):
        captured.append(("query_one", sql, args))
        if "FROM mingkong_material_daily_top100" in sql and "GROUP BY" in sql:
            return {
                "snapshot_date": "2026-05-18",
                "snapshot_at": "2026-05-18 18:00:00",
                "snapshot_slot": "1800",
            }
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 1}
        if "mingkong_material_sync_runs" in sql:
            return None
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        captured.append(("query", sql, args))
        return [
            {
                "id": 1,
                "snapshot_date": "2026-05-18",
                "snapshot_at": "2026-05-18 18:00:00",
                "snapshot_slot": "1800",
                "previous_snapshot_date": "2026-05-17",
                "previous_snapshot_at": "2026-05-17 18:01:00",
                "previous_snapshot_slot": "1800",
                "comparison_interval_seconds": 86340,
                "material_key": "abc",
                "rank_position": 4,
                "display_position": 1,
                "video_name": "fresh.mp4",
                "video_path": "uploads2/fresh.mp4",
                "local_cover_object_key": "artifacts/mingkong-material-covers/ab/abc.jpg",
                "cover_cached_at": None,
                "cover_cache_error": None,
                "current_cumulative_90_spend": 1000,
                "yesterday_spend_delta": 250,
                "is_new_material": 0,
                "is_new_top100_entry": 1,
                "mk_video_metadata_json": "{}",
                "created_at": None,
            }
        ]

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    # 1. Test new_entry_first sort
    result_new = mm.list_yesterday_top100(page=1, page_size=100, sort_order="new_entry_first")
    assert result_new["snapshot"] == "2026-05-18"
    assert result_new["items"][0]["is_new_top100_entry"] is True
    assert any(
        "ORDER BY is_new_top100_entry DESC, yesterday_spend_delta DESC" in item[1]
        for item in captured
        if item[0] == "query"
    )

    # 2. Test normal sort (yesterday spend first)
    captured.clear()
    result_normal = mm.list_yesterday_top100(page=1, page_size=100, sort_order="normal")
    assert result_normal["snapshot"] == "2026-05-18"
    assert any(
        "ORDER BY yesterday_spend_delta DESC," in item[1]
        for item in captured
        if item[0] == "query"
    )


def test_list_yesterday_top100_keyword_uses_shared_material_filter(monkeypatch):
    captured = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def fake_query_one(sql, args=()):
        captured.append(("query_one", sql, args))
        if "FROM mingkong_material_daily_top100" in sql and "GROUP BY" in sql:
            return {
                "snapshot_date": "2026-05-18",
                "snapshot_at": "2026-05-18 18:00:00",
                "snapshot_slot": "1800",
            }
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 1}
        if "mingkong_material_sync_runs" in sql:
            return None
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        captured.append(("query", sql, args))
        return []

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    result = mm.list_yesterday_top100(keyword="baseball-cap-organizer-rjc", page=1, page_size=100)

    assert result["total"] == 1
    count_sql = next(sql for kind, sql, _args in captured if kind == "query_one" and "COUNT(*) AS cnt" in sql)
    list_sql = next(sql for kind, sql, _args in captured if kind == "query" and "ORDER BY is_new_top100_entry" in sql)
    count_args = next(args for kind, sql, args in captured if kind == "query_one" and "COUNT(*) AS cnt" in sql)
    assert "t.product_code LIKE %s" in count_sql
    assert "t.video_name LIKE %s" in count_sql
    assert "t.video_path LIKE %s" in count_sql
    assert "t.product_code LIKE %s" in list_sql
    assert "%baseball-cap-organizer%" in count_args
    assert "%baseball-cap-organizer-rjc%" in count_args


def test_list_yesterday_top100_filters_library_status_before_pagination(monkeypatch):
    captured = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def top_row(material_key, product_code, video_name):
        return {
            "id": 1 if material_key == "imported-video" else 2,
            "snapshot_date": "2026-05-18",
            "snapshot_at": "2026-05-18 18:00:00",
            "snapshot_slot": "1800",
            "previous_snapshot_date": "2026-05-17",
            "previous_snapshot_at": "2026-05-17 18:01:00",
            "previous_snapshot_slot": "1800",
            "comparison_interval_seconds": 86340,
            "material_key": material_key,
            "product_code": product_code,
            "product_url": f"https://shop.example/products/{product_code}",
            "mk_product_id": 901,
            "mk_product_name": product_code.title(),
            "mk_product_link": f"https://shop.example/products/{product_code}-rjc",
            "video_name": video_name,
            "video_path": f"uploads2/{video_name}",
            "local_cover_object_key": "",
            "cover_cached_at": None,
            "cover_cache_error": None,
            "current_cumulative_90_spend": 1000,
            "previous_cumulative_90_spend": 800,
            "yesterday_spend_delta": 200,
            "video_ads_count": 5,
            "is_new_material": 0,
            "is_new_top100_entry": 0,
            "mk_video_metadata_json": "{}",
            "created_at": None,
        }

    def fake_query_one(sql, args=()):
        captured.append(("query_one", sql, args))
        if "FROM mingkong_material_daily_top100" in sql and "GROUP BY" in sql:
            return {
                "snapshot_date": "2026-05-18",
                "snapshot_at": "2026-05-18 18:00:00",
                "snapshot_slot": "1800",
            }
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 2}
        if "mingkong_material_sync_runs" in sql:
            return None
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        captured.append(("query", sql, args))
        if "SELECT t.*" in sql and "FROM mingkong_material_daily_top100 t" in sql:
            return [
                top_row("imported-video", "cool-widget", "imported.mp4"),
                top_row("missing-video", "fresh-widget", "missing.mp4"),
            ]
        raise AssertionError(sql)

    def fake_enrich(items):
        for item in items:
            imported = item["material_key"] == "imported-video"
            item["product_ad_status"] = {"has_local_match": True}
            item["material_ad_status"] = {"has_local_match": imported}
            item["has_local_product_in_library"] = True
            item["has_local_material_in_library"] = imported
        return items

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)
    monkeypatch.setattr(mm, "_enrich_cached_ad_statuses", fake_enrich)

    result = mm.list_yesterday_top100(
        page=1,
        page_size=1,
        library_status="video_not_imported",
    )

    assert result["total"] == 1
    assert [item["material_key"] for item in result["items"]] == ["missing-video"]
    list_sql = next(sql for kind, sql, _args in captured if kind == "query" and "SELECT t.*" in sql)
    assert "LIMIT %s OFFSET %s" not in list_sql


def test_list_yesterday_top100_attaches_fine_ai_by_video_card_without_product_link(monkeypatch):
    material_key = "b" * 64

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)
    monkeypatch.setattr(mm, "_status_cache_by_hash", lambda scope, lookup_hashes: {})
    monkeypatch.setattr(mm, "_legacy_material_rows_by_product", lambda product_ids: {})

    def fake_query_one(sql, args=()):
        if "FROM mingkong_material_daily_top100" in sql and "GROUP BY" in sql:
            return {
                "snapshot_date": "2026-05-26",
                "snapshot_at": "2026-05-26 18:00:00",
                "snapshot_slot": "1800",
            }
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 1}
        if "mingkong_material_sync_runs" in sql:
            return None
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        if "SELECT t.*" in sql and "FROM mingkong_material_daily_top100 t" in sql:
            return [
                {
                    "id": 1,
                    "snapshot_date": "2026-05-26",
                    "snapshot_at": "2026-05-26 18:00:00",
                    "snapshot_slot": "1800",
                    "previous_snapshot_date": "2026-05-25",
                    "previous_snapshot_at": "2026-05-25 18:00:00",
                    "previous_snapshot_slot": "1800",
                    "comparison_interval_seconds": 86400,
                    "ranking_snapshot_date": "2026-05-26",
                    "rank_position": 1,
                    "display_position": 1,
                    "material_key": material_key,
                    "product_code": "linkless-card",
                    "product_url": "",
                    "mk_product_link": "",
                    "mk_product_id": 901,
                    "mk_product_name": "Linkless Card",
                    "video_name": "same-video.mp4",
                    "video_path": "uploads2/same-video.mp4",
                    "video_image_path": "uploads2/same-video.jpg",
                    "local_cover_object_key": "artifacts/mingkong-material-covers/bb/card.jpg",
                    "current_cumulative_90_spend": 1200,
                    "previous_cumulative_90_spend": 800,
                    "yesterday_spend_delta": 400,
                    "video_ads_count": 5,
                    "is_new_material": 0,
                    "is_new_top100_entry": 1,
                    "mk_video_metadata_json": "{}",
                    "created_at": None,
                }
            ]
        if "FROM mingkong_fine_ai_auto_evaluations" in sql:
            return []
        if "FROM ai_evaluation_runs" in sql:
            return [
                {
                    "id": 9,
                    "evaluation_run_id": "eval_same_video",
                    "product_id": 0,
                    "status": "completed",
                    "summary_json": '{"overall_recommendation":"GO"}',
                    "frontend_json": "{}",
                    "progress_json": "{}",
                    "metadata_json": (
                        '{"source_type":"external_product_link",'
                        '"external_product_link":"https://shop.example/products/old-link",'
                        '"external_card_video":{"path":"uploads2/same-video.mp4"}}'
                    ),
                    "created_at": "2026-05-26 09:00:00",
                    "updated_at": "2026-05-26 09:01:00",
                    "completed_at": "2026-05-26 09:01:00",
                    "failed_at": None,
                }
            ]
        if "FROM ai_country_evaluations" in sql:
            return [
                {
                    "evaluation_run_id": "eval_same_video",
                    "country_code": "DE",
                    "country_name": "Germany",
                    "status": "completed",
                    "scores_json": '{"overall_score":82}',
                    "decision_json": '{"final_decision":"GO"}',
                    "full_result_json": (
                        '{"country_code":"DE","country_name":"Germany",'
                        '"status":"completed","scores":{"overall_score":82},'
                        '"decision":{"final_decision":"GO"}}'
                    ),
                    "error_message": "",
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    result = mm.list_yesterday_top100(page=1, page_size=100)

    fine_ai = result["items"][0]["product_ad_status"]["fine_ai_evaluation"]
    assert fine_ai["evaluation_run_id"] == "eval_same_video"
    assert fine_ai["has_result"] is True
    assert fine_ai["countries"]["DE"]["decision"]["final_decision"] == "GO"


def test_enrich_cards_looks_up_fine_ai_video_runs_without_database_sort(monkeypatch):
    item = {
        "material_key": "c" * 64,
        "product_code": "sortless-card",
        "video_path": "uploads2/sortless-video.mp4",
    }
    monkeypatch.setattr(mm, "_status_cache_by_hash", lambda scope, lookup_hashes: {})
    monkeypatch.setattr(mm, "_legacy_material_rows_by_product", lambda product_ids: {})

    def fake_query(sql, args=()):
        if "FROM mingkong_fine_ai_auto_evaluations" in sql:
            return []
        if "FROM ai_evaluation_runs" in sql:
            if "JSON_UNQUOTE(JSON_EXTRACT" in sql and "ORDER BY" in sql:
                raise RuntimeError("sort memory")
            return [
                {
                    "id": 19,
                    "evaluation_run_id": "eval_sortless",
                    "product_id": 0,
                    "status": "completed",
                    "summary_json": "{}",
                    "frontend_json": "{}",
                    "progress_json": "{}",
                    "metadata_json": (
                        '{"source_type":"external_product_link",'
                        '"external_product_link":"https://shop.example/products/old-link",'
                        '"external_card_video":{"path":"uploads2/sortless-video.mp4"}}'
                    ),
                    "created_at": "2026-05-26 09:00:00",
                    "updated_at": "2026-05-26 09:01:00",
                    "completed_at": "2026-05-26 09:01:00",
                    "failed_at": None,
                }
            ]
        if "FROM ai_country_evaluations" in sql:
            return [
                {
                    "evaluation_run_id": "eval_sortless",
                    "country_code": "DE",
                    "country_name": "Germany",
                    "status": "completed",
                    "scores_json": '{"overall_score":82}',
                    "decision_json": '{"final_decision":"GO"}',
                    "full_result_json": (
                        '{"country_code":"DE","country_name":"Germany",'
                        '"status":"completed","scores":{"overall_score":82},'
                        '"decision":{"final_decision":"GO"}}'
                    ),
                    "error_message": "",
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(mm, "query", fake_query)

    enriched = mm._enrich_cached_ad_statuses([item])

    fine_ai = enriched[0]["product_ad_status"]["fine_ai_evaluation"]
    assert fine_ai["evaluation_run_id"] == "eval_sortless"
    assert fine_ai["has_result"] is True


def test_run_daily_snapshot_marks_material_run_failed_on_fatal_error(monkeypatch):
    writes = []
    finishes = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)
    monkeypatch.setattr(mm.scheduled_tasks, "start_run", lambda code: 77)
    monkeypatch.setattr(
        mm.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: finishes.append((run_id, kwargs)),
    )
    monkeypatch.setattr(
        mm,
        "latest_top_products",
        lambda limit=500: (
            "2026-05-17",
            [{"product_code": "cool-widget", "rank_position": 1}],
        ),
    )
    monkeypatch.setattr(
        mm,
        "create_or_reuse_run",
        lambda **kwargs: {"id": 42, "status": "running"},
    )
    monkeypatch.setattr(mm, "_mk_headers", lambda: (_ for _ in ()).throw(RuntimeError("no token")))
    monkeypatch.setattr(mm, "execute", lambda sql, args=(): writes.append((sql, args)) or 1)

    try:
        mm.run_daily_snapshot(source_limit=300, sleep_seconds=0, snapshot_date="2026-05-18")
    except RuntimeError as exc:
        assert str(exc) == "no token"
    else:
        raise AssertionError("run_daily_snapshot should raise the fatal error")

    assert any("UPDATE mingkong_material_sync_runs" in sql and "status='failed'" in sql for sql, _ in writes)
    assert any(args[-1] == 42 for _, args in writes)
    assert finishes == [(77, {"status": "failed", "error_message": "no token"})]
