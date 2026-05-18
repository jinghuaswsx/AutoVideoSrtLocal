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
