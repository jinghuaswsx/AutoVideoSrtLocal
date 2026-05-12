from __future__ import annotations

import tools.generate_today_recommendations as gen


def test_product_handle_normalizes_product_url_and_rjc_suffix():
    assert (
        gen._product_handle("https://example.com/de/products/self-drilling-anchors-screws-rjc?x=1")
        == "self-drilling-anchors-screws"
    )


def test_select_mk_item_prefers_exact_link_and_video_spend():
    items = [
        {
            "id": 10,
            "product_links": ["https://example.com/products/other"],
            "videos": [{"name": "other.mp4", "path": "a.mp4", "spends": "9999", "ads_count": 5}],
        },
        {
            "id": 2,
            "product_links": ["https://example.com/products/self-drilling-anchors-screws-rjc"],
            "videos": [{"name": "exact.mp4", "path": "b.mp4", "spends": "10", "ads_count": 1}],
        },
    ]

    item, videos = gen._select_mk_item(items, "self-drilling-anchors-screws")

    assert item["id"] == 2
    assert videos[0]["name"] == "exact.mp4"


def test_build_rows_expands_selected_product_to_material_rows():
    candidates = [
        {
            "product_key": "abc",
            "product_handle": "abc",
            "shopify_product_id": "100",
            "product_name": "ABC Product",
            "product_url": "https://example.com/products/abc",
            "sales_count": 10,
            "order_count": 9,
            "revenue_main": "CNY 100",
            "rank_position": 3,
            "mk_product_id": 9,
            "mk_product_name": "ABC",
            "mk_total_spends": 30,
            "mk_total_ads": 4,
            "mk_video_count": 2,
            "base_score": 88,
            "texts": [],
            "main_image": "",
            "videos": [
                {"index": 1, "name": "a.mp4", "path": "a.mp4", "image_path": "a.jpg", "spends": 20, "ads_count": 3, "author": "A", "upload_time": "2026-01-01", "duration_seconds": 10},
                {"index": 2, "name": "b.mp4", "path": "b.mp4", "image_path": "b.jpg", "spends": 10, "ads_count": 1, "author": "B", "upload_time": "2026-01-02", "duration_seconds": 11},
            ],
        }
    ]
    selected = [
        {
            "product_key": "abc",
            "overall_score": 91,
            "countries": ["de", "fr"],
            "video_indexes": [2, 1],
            "reason": "strong fit",
        }
    ]

    rows = gen.build_rows(
        selected=selected,
        candidates=candidates,
        ranking_snapshot_date="2026-05-11",
        max_materials_per_product=5,
    )

    assert [row["video_name"] for row in rows] == ["b.mp4", "a.mp4"]
    assert rows[0]["product_recommendation_rank"] == 1
    assert rows[0]["recommended_countries"] == ["de", "fr"]
    assert rows[0]["mk_video_metadata"]["video_path"] == "b.mp4"


def test_build_rows_fills_unselected_materials_up_to_cap():
    candidates = [
        {
            "product_key": "abc",
            "product_handle": "abc",
            "shopify_product_id": "100",
            "product_name": "ABC Product",
            "product_url": "https://example.com/products/abc",
            "sales_count": 10,
            "order_count": 9,
            "revenue_main": "CNY 100",
            "rank_position": 3,
            "mk_product_id": 9,
            "mk_product_name": "ABC",
            "mk_total_spends": 60,
            "mk_total_ads": 6,
            "mk_video_count": 3,
            "base_score": 88,
            "texts": [],
            "main_image": "",
            "videos": [
                {"index": 1, "name": "a.mp4", "path": "a.mp4", "image_path": "a.jpg", "spends": 30, "ads_count": 3, "author": "A", "upload_time": "2026-01-01", "duration_seconds": 10},
                {"index": 2, "name": "b.mp4", "path": "b.mp4", "image_path": "b.jpg", "spends": 20, "ads_count": 2, "author": "B", "upload_time": "2026-01-02", "duration_seconds": 11},
                {"index": 3, "name": "c.mp4", "path": "c.mp4", "image_path": "c.jpg", "spends": 10, "ads_count": 1, "author": "C", "upload_time": "2026-01-03", "duration_seconds": 12},
            ],
        }
    ]
    selected = [
        {
            "product_key": "abc",
            "overall_score": 91,
            "countries": ["de"],
            "video_indexes": [2],
            "reason": "strong fit",
        }
    ]

    rows = gen.build_rows(
        selected=selected,
        candidates=candidates,
        ranking_snapshot_date="2026-05-11",
        max_materials_per_product=3,
    )

    assert [row["video_name"] for row in rows] == ["b.mp4", "a.mp4", "c.mp4"]


def test_resolve_billing_user_id_defaults_to_admin(monkeypatch):
    monkeypatch.setattr(gen, "query_one", lambda sql: {"id": 1})

    assert gen._resolve_billing_user_id(None) == 1
    assert gen._resolve_billing_user_id(9) == 9


def test_select_recommendations_fills_to_target_when_llm_returns_short(monkeypatch):
    class Args:
        no_llm = False
        target_products = 2
        max_materials_per_product = 1
        llm_batch_size = 20
        batch_pick_limit = 2
        provider = "gemini_vertex"
        model = "gemini-3.1-flash-lite-preview"
        user_id = 1

    candidates = [
        {
            "product_key": "a",
            "product_name": "Garden Tool",
            "base_score": 100,
            "videos": [{"index": 1}],
        },
        {
            "product_key": "b",
            "product_name": "Hair Clip",
            "base_score": 90,
            "videos": [{"index": 1}],
        },
    ]

    def fake_call_llm(**kwargs):
        if kwargs["final_pass"]:
            return [
                {
                    "product_key": "a",
                    "overall_score": 88,
                    "countries": ["de"],
                    "video_indexes": [1],
                    "reason": "good",
                }
            ]
        return [{"product_key": "a"}]

    monkeypatch.setattr(gen, "_call_llm", fake_call_llm)

    selected, stats = gen.select_recommendations(Args(), candidates=candidates, countries=["de", "fr"])

    assert stats["mode"] == "llm"
    assert [item["product_key"] for item in selected] == ["a", "b"]


def test_select_recommendations_prefers_products_with_full_material_capacity(monkeypatch):
    class Args:
        no_llm = False
        target_products = 2
        max_materials_per_product = 2
        llm_batch_size = 20
        batch_pick_limit = 2
        provider = "gemini_vertex"
        model = "gemini-3.1-flash-lite-preview"
        user_id = 1

    candidates = [
        {
            "product_key": "thin",
            "product_name": "Thin",
            "base_score": 200,
            "videos": [{"index": 1}],
        },
        {
            "product_key": "full-a",
            "product_name": "Full A",
            "base_score": 100,
            "videos": [{"index": 1}, {"index": 2}],
        },
        {
            "product_key": "full-b",
            "product_name": "Full B",
            "base_score": 90,
            "videos": [{"index": 1}, {"index": 2}],
        },
    ]

    def fake_call_llm(**kwargs):
        return [
            {
                "product_key": "thin",
                "overall_score": 99,
                "countries": ["de"],
                "video_indexes": [1],
                "reason": "high",
            },
            {
                "product_key": "full-a",
                "overall_score": 88,
                "countries": ["de"],
                "video_indexes": [1],
                "reason": "good",
            },
        ]

    monkeypatch.setattr(gen, "_call_llm", fake_call_llm)

    selected, _stats = gen.select_recommendations(Args(), candidates=candidates, countries=["de", "fr"])

    assert [item["product_key"] for item in selected] == ["full-a", "full-b"]
