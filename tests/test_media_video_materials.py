from __future__ import annotations

from datetime import datetime

import appcore.media_video_materials as mvm
import tools.generate_today_recommendations as gen


def _video_row(**overrides):
    row = {
        "id": 11,
        "product_id": 7,
        "lang": "en",
        "filename": "2026.05.13-widget-demo.mp4",
        "display_name": "Widget Demo",
        "object_key": "media/items/widget demo.mp4",
        "thumbnail_path": "thumb.jpg",
        "cover_object_key": "cover.jpg",
        "duration_seconds": 12.5,
        "file_size": 1024,
        "pushed_at": None,
        "latest_push_id": None,
        "created_at": datetime(2026, 5, 13, 12, 0, 0),
        "product_name": "Widget",
        "product_code": "widget-rjc",
        "product_mk_id": 123,
        "owner_username": "admin",
        "binding_id": 9,
        "mk_product_id": 456,
        "mk_product_name": "MK Widget",
        "mk_video_path": "materials/widget.mp4",
        "mk_video_name": "widget.mp4",
        "mk_video_image_path": "materials/widget.jpg",
        "mk_video_metadata_json": '{"spends": 88}',
        "bound_by": 1,
        "bound_at": datetime(2026, 5, 13, 12, 5, 0),
        "push_success_count": 1,
    }
    row.update(overrides)
    return row


def test_list_video_materials_filters_and_serializes(monkeypatch):
    calls = []

    def fake_query_one(sql, args=()):
        calls.append(("query_one", sql, args))
        return {"c": 1}

    def fake_query(sql, args=()):
        calls.append(("query", sql, args))
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return [{
                "product_id": 7,
                "normalized_campaign_code": "widget-rjc-campaign",
                "campaign_name": "Widget Campaign",
                "ad_account_id": "act_1253003326160754",
                "ad_account_name": "Omurio",
                "activity_date": "2026-05-13",
                "spend_usd": 88,
                "id": 100,
            }]
        return [_video_row()]

    monkeypatch.setattr(mvm, "query_one", fake_query_one)
    monkeypatch.setattr(mvm, "query", fake_query)

    payload = mvm.list_video_materials(
        keyword="123",
        lang="EN",
        ad_plan_status="has",
        page=2,
        page_size=25,
    )

    assert payload["total"] == 1
    assert payload["page"] == 2
    assert payload["page_size"] == 25
    assert payload["items"][0]["has_ad_plan"] is True
    assert payload["items"][0]["ad_plan_status"] == "has"
    assert payload["items"][0]["mk_binding"]["mk_video_path"] == "materials/widget.mp4"
    assert payload["items"][0]["video_url"] == "/medias/object?object_key=media%2Fitems%2Fwidget%20demo.mp4"
    assert payload["items"][0]["ad_plan_detail"]["code"] == "widget-rjc-campaign"
    assert payload["items"][0]["ad_plan_detail"]["ad_account_id"] == "1253003326160754"

    list_sql = calls[1][1]
    list_args = calls[1][2]
    assert "i.filename LIKE %s" in list_sql
    assert "p.product_code LIKE %s" in list_sql
    assert "p.mk_id=%s" in list_sql
    assert "meta_ad_daily_campaign_metrics" not in list_sql
    assert "i.lang=%s" in list_sql
    assert "media_push_logs" in list_sql
    assert list_args[-2:] == (25, 25)
    assert "en" in list_args
    assert 123 in list_args

    ad_sql = calls[2][1]
    ad_args = calls[2][2]
    assert "FROM meta_ad_daily_campaign_metrics" in ad_sql
    assert "m.product_id IN" in ad_sql
    assert "m.normalized_campaign_code IN" in ad_sql
    assert 7 in ad_args
    assert "widget-rjc" in ad_args


def test_list_video_materials_batches_ad_plan_details_for_current_page(monkeypatch):
    calls = []

    def fake_query_one(sql, args=()):
        calls.append(("query_one", sql, args))
        return {"c": 2}

    def fake_query(sql, args=()):
        calls.append(("query", sql, args))
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return [
                {
                    "product_id": 8,
                    "normalized_campaign_code": "fallback-code-rjc",
                    "campaign_name": "Campaign by Code",
                    "ad_account_id": "act_999",
                    "ad_account_name": "Code Account",
                    "activity_date": "2026-05-13",
                    "spend_usd": 22,
                    "id": 200,
                },
                {
                    "product_id": 7,
                    "normalized_campaign_code": "widget-rjc-campaign",
                    "campaign_name": "Widget Campaign",
                    "ad_account_id": "act_1253003326160754",
                    "ad_account_name": "Omurio",
                    "activity_date": "2026-05-12",
                    "spend_usd": 88,
                    "id": 100,
                },
            ]
        return [
            _video_row(product_id=7, product_code="widget-rjc", push_success_count=1),
            _video_row(id=12, product_id=8, product_code="fallback-code-rjc", push_success_count=1),
        ]

    monkeypatch.setattr(mvm, "query_one", fake_query_one)
    monkeypatch.setattr(mvm, "query", fake_query)

    payload = mvm.list_video_materials(page_size=100)

    assert [item["ad_plan_detail"]["code"] for item in payload["items"]] == [
        "widget-rjc-campaign",
        "fallback-code-rjc",
    ]
    assert payload["items"][0]["ad_plan_detail"]["ad_account_id"] == "1253003326160754"
    assert payload["items"][1]["ad_plan_detail"]["ad_account_id"] == "999"
    assert len([call for call in calls if call[0] == "query" and "FROM meta_ad_daily_campaign_metrics" in call[1]]) == 1


def test_serialize_video_material_includes_campaign_detail_link():
    item = mvm.serialize_video_material(_video_row(
        ad_campaign_code="glow-go-rjc",
        ad_campaign_name="Glow Go Campaign",
        ad_account_id="act_1253003326160754",
        ad_account_name="Omurio",
    ))

    detail = item["ad_plan_detail"]
    assert detail["level"] == "campaign"
    assert detail["code"] == "glow-go-rjc"
    assert detail["name"] == "Glow Go Campaign"
    assert detail["ad_account_id"] == "1253003326160754"
    assert detail["url"] == (
        "/order-analytics?tab=ads&ads_level=campaign&ads_code=glow-go-rjc"
        "&ads_name=Glow+Go+Campaign&ad_account_id=1253003326160754"
    )


def test_serialize_video_material_falls_back_to_product_code_for_ad_plan_link():
    item = mvm.serialize_video_material(_video_row(
        product_code="fallback-product-rjc",
        product_name="Fallback Product",
        ad_campaign_code=None,
        ad_campaign_name=None,
        ad_account_id=None,
        push_success_count=1,
    ))

    assert item["ad_plan_detail"]["code"] == "fallback-product-rjc"
    assert "ads_code=fallback-product-rjc" in item["ad_plan_detail"]["url"]
    assert "ads_name=Fallback+Product" in item["ad_plan_detail"]["url"]


def test_list_video_materials_defaults_to_page_size_100(monkeypatch):
    calls = []

    def fake_query_one(sql, args=()):
        calls.append(("query_one", sql, args))
        return {"c": 0}

    def fake_query(sql, args=()):
        calls.append(("query", sql, args))
        return []

    monkeypatch.setattr(mvm, "query_one", fake_query_one)
    monkeypatch.setattr(mvm, "query", fake_query)

    payload = mvm.list_video_materials()

    assert payload["page"] == 1
    assert payload["page_size"] == 100
    assert calls[1][2][-2:] == (100, 0)


def test_existing_english_material_identity_matches_names_and_bound_paths(monkeypatch):
    def fake_query(sql, args=()):
        if "FROM media_item_mk_bindings" in sql:
            return [{
                "mk_video_path": "mk/videos/existing-bound.mp4",
                "mk_video_name": "Bound Name.mp4",
            }]
        return [{
            "filename": "Existing Name.mp4",
            "display_name": "Pretty Existing.mp4",
            "object_key": "media/items/Object Name.mp4",
        }]

    monkeypatch.setattr(mvm, "query", fake_query)

    identity = mvm.existing_english_material_identity()

    assert "existing name.mp4" in identity["names"]
    assert "pretty existing.mp4" in identity["names"]
    assert "object name.mp4" in identity["names"]
    assert "bound name.mp4" in identity["names"]
    assert "mk/videos/existing-bound.mp4" in identity["paths"]
    assert mvm.is_existing_english_material(
        video_path="mk/videos/existing-bound.mp4",
        video_name="fresh.mp4",
        identity=identity,
    )
    assert mvm.is_existing_english_material(
        video_path="mk/videos/fresh.mp4",
        video_name="Bound Name.mp4",
        identity=identity,
    )


def test_bind_mk_material_upserts_normalized_binding(monkeypatch):
    captured = {}

    monkeypatch.setattr(mvm, "get_video_material", lambda item_id: {"id": item_id, "product_id": 7})

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args

    monkeypatch.setattr(mvm, "execute", fake_execute)
    monkeypatch.setattr(mvm, "query_one", lambda sql, args=(): _video_row(
        mk_video_path="folder/video.mp4",
        mk_video_name="video.mp4",
        mk_video_image_path="folder/poster.jpg",
    ))

    item = mvm.bind_mk_material(
        media_item_id=11,
        mk_product_id=456,
        mk_product_name="MK Widget",
        mk_video_path="/medias/folder/video.mp4",
        mk_video_name="video.mp4",
        mk_video_image_path="/folder/poster.jpg",
        mk_video_metadata={"spends": 10},
        bound_by=1,
    )

    assert "ON DUPLICATE KEY UPDATE" in captured["sql"]
    assert captured["args"][3] == "folder/video.mp4"
    assert captured["args"][5] == "folder/poster.jpg"
    assert item["mk_binding"]["mk_video_path"] == "folder/video.mp4"


def test_search_mk_materials_filters_by_video_filename(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "items": [{
                        "id": 456,
                        "product_name": "MK Widget",
                        "product_links": ["https://example.com/products/widget-rjc"],
                        "videos": [
                            {"name": "skip.mp4", "path": "mk/skip.mp4", "spends": 999, "ads_count": 9},
                            {"name": "Needle.mp4", "path": "mk/needle.mp4", "spends": 10, "ads_count": 1},
                            {"name": "hidden.mp4", "path": "mk/hidden.mp4", "hidden": True},
                        ],
                    }]
                }
            }

    def fake_get(url, params, headers, timeout):
        captured.update({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(mvm.pushes, "build_localized_texts_headers", lambda: {"Authorization": "Bearer test"})
    monkeypatch.setattr(mvm.pushes, "get_localized_texts_base_url", lambda: "https://mk.example")
    monkeypatch.setattr(mvm.requests, "get", fake_get)

    items = mvm.search_mk_materials(keyword="needle.mp4", limit=5, page=2, timeout=7)

    assert captured["url"] == "https://mk.example/api/marketing/medias"
    assert captured["params"]["q"] == "needle.mp4"
    assert captured["params"]["page"] == 2
    assert captured["timeout"] == 7
    assert [item["video_name"] for item in items] == ["Needle.mp4"]
    assert items[0]["mk_product_id"] == 456


def test_collect_candidates_excludes_existing_english_mk_videos(monkeypatch):
    class Args:
        snapshot_date = "2026-05-12"
        source_limit = 500
        timeout_seconds = 5
        max_materials_per_product = 5
        request_delay_seconds = 0

    monkeypatch.setattr(gen, "_load_rankings", lambda snapshot_date, limit: [{
        "product_id": "100",
        "product_name": "Widget",
        "product_url": "https://shop.example/products/widget-rjc",
        "rank_position": 1,
        "sales_count": 20,
        "order_count": 18,
        "revenue_main": "1000",
    }])
    monkeypatch.setattr(gen, "_build_headers", lambda: {})
    monkeypatch.setattr(gen, "_mk_base_url", lambda: "https://mk.example")
    monkeypatch.setattr(gen.media_video_materials, "existing_english_material_identity", lambda: {
        "names": {"existing.mp4"},
        "paths": {"mk/existing.mp4"},
    })
    monkeypatch.setattr(gen, "_search_mk_items", lambda *args, **kwargs: [{
        "id": 456,
        "product_name": "MK Widget",
        "product_links": ["https://shop.example/products/widget-rjc"],
        "videos": [
            {"name": "existing.mp4", "path": "mk/existing.mp4", "spends": 500, "ads_count": 8},
            {"name": "fresh.mp4", "path": "mk/fresh.mp4", "spends": 100, "ads_count": 2},
        ],
    }])

    snapshot_date, candidates, stats = gen.collect_candidates(Args())

    assert snapshot_date == "2026-05-12"
    assert stats["mk_existing_english_video_excluded"] == 1
    assert [video["path"] for video in candidates[0]["videos"]] == ["mk/fresh.mp4"]
