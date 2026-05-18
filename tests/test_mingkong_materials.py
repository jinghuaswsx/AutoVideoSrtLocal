from __future__ import annotations

from datetime import date

import appcore.mingkong_materials as mm


def test_material_key_is_stable_and_path_specific():
    first = mm.material_key_for("cool-widget", 901, "uploads2/a.mp4")
    second = mm.material_key_for("cool-widget", 901, "uploads2/a.mp4")
    other = mm.material_key_for("cool-widget", 901, "uploads2/b.mp4")

    assert first == second
    assert first != other
    assert len(first) == 64


def test_latest_top300_products_use_latest_dianxiaomi_snapshot(monkeypatch):
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

    snapshot, rows = mm.latest_top_products(limit=300)

    assert snapshot == "2026-05-17"
    assert rows[0]["product_code"] == "cool-widget"
    assert rows[0]["shopify_product_id"] == "gid-1"
    assert "ORDER BY rank_position ASC" in calls[0][0]
    assert calls[0][1] == ("2026-05-17", 300)


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
    assert rows[0]["material_key"] == mm.material_key_for(
        "cool-widget",
        901,
        "uploads2/a.mp4",
    )
    assert rows[0]["mk_product_link"] == "https://shop.example/products/cool-widget-rjc"
    assert rows[1]["video_image_path"] == "uploads2/b.jpg"


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
        previous_snapshot_date="2026-05-17",
        current_rows=current,
        previous_by_key=previous_by_key,
        previous_top100_keys=previous_top100_keys,
        limit=100,
    )

    assert rows[0]["material_key"] == "fresh"
    assert rows[0]["yesterday_spend_delta"] == 500.0
    assert rows[0]["is_new_material"] is True
    assert rows[0]["is_new_top100_entry"] is True
    assert rows[0]["rank_position"] == 1
    assert rows[0]["display_position"] == 1
    assert rows[1]["material_key"] == "reset"
    assert rows[1]["yesterday_spend_delta"] == 0.0
    assert rows[1]["is_new_top100_entry"] is True
    assert rows[2]["material_key"] == "old"
    assert rows[2]["yesterday_spend_delta"] == 50.0
    assert rows[2]["is_new_top100_entry"] is False


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
    assert writes[0][1][-1] == '{"spends": 12000}'


def test_list_material_library_serializes_latest_snapshot(monkeypatch):
    captured = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def fake_query_one(sql, args=()):
        captured.append(("query_one", sql, args))
        if "MAX(snapshot_date)" in sql:
            return {"snapshot_date": date(2026, 5, 18)}
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 1}
        if "mingkong_material_sync_runs" in sql:
            return {"status": "success", "summary_json": '{"processed": 300}'}
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        captured.append(("query", sql, args))
        return [
            {
                "id": 1,
                "snapshot_date": date(2026, 5, 18),
                "ranking_snapshot_date": date(2026, 5, 17),
                "material_key": "abc",
                "product_code": "cool-widget",
                "rank_position": 7,
                "video_name": "winner.mp4",
                "video_path": "uploads2/winner.mp4",
                "video_image_path": "uploads2/winner.jpg",
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
    assert result["total"] == 1
    assert result["run_summary"]["summary"] == {"processed": 300}
    assert result["items"][0]["video_spends"] == 12000.0
    assert result["items"][0]["mk_video_metadata"] == {"video_path": "uploads2/winner.mp4"}
    assert any("cumulative_90_spend DESC" in item[1] for item in captured if item[0] == "query")


def test_list_yesterday_top100_orders_new_entries_first(monkeypatch):
    captured = []

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def fake_query_one(sql, args=()):
        captured.append(("query_one", sql, args))
        if "MAX(snapshot_date)" in sql:
            return {"snapshot_date": "2026-05-18"}
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
                "previous_snapshot_date": "2026-05-17",
                "material_key": "abc",
                "rank_position": 4,
                "display_position": 1,
                "video_name": "fresh.mp4",
                "video_path": "uploads2/fresh.mp4",
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
    assert result["previous_snapshot"] == "2026-05-17"
    assert result["items"][0]["video_spends"] == 1000.0
    assert result["items"][0]["is_new_top100_entry"] is True
    assert any(
        "is_new_top100_entry DESC, yesterday_spend_delta DESC" in item[1]
        for item in captured
        if item[0] == "query"
    )
