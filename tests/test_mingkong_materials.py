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
    assert any("product_total_90_spend DESC" in item[1] for item in captured if item[0] == "query")


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

    result = mm.list_yesterday_top100(page=1, page_size=100)

    assert result["snapshot"] == "2026-05-18"
    assert result["snapshot_at"] == "2026-05-18 18:00:00"
    assert result["previous_snapshot"] == "2026-05-17"
    assert result["previous_snapshot_at"] == "2026-05-17 18:01:00"
    assert result["items"][0]["video_spends"] == 1000.0
    assert result["items"][0]["local_cover_url"] == (
        "/medias/object?object_key=artifacts%2Fmingkong-material-covers%2Fab%2Fabc.jpg"
    )
    assert result["items"][0]["is_new_top100_entry"] is True
    assert any(
        "is_new_top100_entry DESC, yesterday_spend_delta DESC" in item[1]
        for item in captured
        if item[0] == "query"
    )


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
