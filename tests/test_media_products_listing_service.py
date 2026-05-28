from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_products_list_flask_response_returns_payload(authed_client_no_db):
    from web.services.media_products_listing import products_list_flask_response

    payload = {"items": [{"id": 1}], "total": 1, "page": 1, "page_size": 20}
    with authed_client_no_db.application.app_context():
        response = products_list_flask_response(payload)

    assert response.get_json() == payload


def test_build_products_list_response_enriches_rows_and_preserves_filters():
    from web.services.media_products_listing import build_products_list_response

    calls = {}
    serialized = []

    def list_products(_user_id, **kwargs):
        calls["list_products"] = kwargs
        return ([{"id": 2, "name": "P2"}, {"id": 1, "name": "P1"}], 42)

    def serialize_product(row, items_count, cover_item_id, **kwargs):
        serialized.append((row["id"], items_count, cover_item_id, kwargs))
        return {
            "id": row["id"],
            "items_count": items_count,
            "cover_item_id": cover_item_id,
            "skus": kwargs["skus"],
            "xmyc_keys": sorted(kwargs["xmyc_index"]),
        }

    def list_xmyc_unit_prices(skus):
        calls["xmyc_skus"] = list(skus)
        return {
            "sku-a": {"unit_price": 1},
            "sku-b": {"unit_price": 2},
        }

    def get_latest_sku_actual_roas(skus):
        calls["actual_roas_skus"] = list(skus)
        return {"sku-a": {"value": 2.1}}

    payload = build_products_list_response(
        {
            "keyword": "  box cutter  ",
            "archived": "yes",
            "page": "3",
            "xmyc_match": "matched",
            "roas_status": "complete",
            "delivery_status": "active",
        },
        list_products_fn=list_products,
        count_items_by_product_fn=lambda pids: {1: 5, 2: 7},
        count_raw_sources_by_product_fn=lambda pids: {1: 2, 2: 3},
        first_thumb_item_by_product_fn=lambda pids: {1: 101, 2: 202},
        list_item_filenames_by_product_fn=lambda pids, limit_per: {1: ["a.mp4"], 2: ["b.mp4"]},
        lang_coverage_by_product_fn=lambda pids: {1: {"de": 1}, 2: {"fr": 1}},
        get_product_covers_batch_fn=lambda pids: {1: {"en": "cover1"}, 2: {"en": "cover2"}},
        list_product_skus_batch_fn=lambda pids: {
            1: [{"dianxiaomi_sku": "sku-b"}],
            2: [{"dianxiaomi_sku": "sku-a"}, {"dianxiaomi_sku": "sku-b"}],
        },
        list_xmyc_unit_prices_fn=list_xmyc_unit_prices,
        get_latest_sku_actual_roas_fn=get_latest_sku_actual_roas,
        get_configured_rmb_per_usd_fn=lambda: 7.1,
        get_product_ad_summary_cache_fn=lambda pids: {
            1: {"delivery_status": "stopped", "overall_roas": 1.2},
            2: {"delivery_status": "active", "overall_roas": 2.4},
        },
        get_product_lang_ad_summary_cache_fn=lambda pids: {
            1: {"de": {"pushed_video_count": 0}},
            2: {"fr": {"pushed_video_count": 2}},
        },
        serialize_product_fn=serialize_product,
    )

    assert calls["list_products"] == {
        "keyword": "box cutter",
        "archived": True,
        "offset": 40,
        "limit": 20,
        "xmyc_match": "matched",
        "roas_status": "complete",
        "delivery_status": "active",
    }
    assert calls["xmyc_skus"] == ["sku-a", "sku-b"]
    assert calls["actual_roas_skus"] == ["sku-a", "sku-b"]
    assert payload == {
        "items": [
            {
                "id": 2,
                "items_count": 7,
                "cover_item_id": 202,
                "skus": [{"dianxiaomi_sku": "sku-a"}, {"dianxiaomi_sku": "sku-b"}],
                "xmyc_keys": ["sku-a", "sku-b"],
            },
            {
                "id": 1,
                "items_count": 5,
                "cover_item_id": 101,
                "skus": [{"dianxiaomi_sku": "sku-b"}],
                "xmyc_keys": ["sku-a", "sku-b"],
            },
        ],
        "total": 42,
        "page": 3,
        "page_size": 20,
    }
    assert serialized[0][3]["raw_sources_count"] == 3
    assert serialized[0][3]["roas_rmb_per_usd"] == 7.1
    assert serialized[0][3]["sku_actual_roas_index"] == {"sku-a": {"value": 2.1}}
    assert serialized[0][3]["ad_summary"] == {"delivery_status": "active", "overall_roas": 2.4}
    assert serialized[0][3]["lang_ad_summary"] == {"fr": {"pushed_video_count": 2}}
    assert serialized[1][3]["lang_coverage"] == {"de": 1}
    assert serialized[1][3]["ad_summary"] == {"delivery_status": "stopped", "overall_roas": 1.2}
    assert serialized[1][3]["lang_ad_summary"] == {"de": {"pushed_video_count": 0}}


def test_build_products_list_response_defaults_invalid_filters():
    from web.services.media_products_listing import build_products_list_response

    captured = {}

    def list_products(_user_id, **kwargs):
        captured.update(kwargs)
        return ([], 0)

    payload = build_products_list_response(
        {"xmyc_match": "bad", "roas_status": "bad"},
        list_products_fn=list_products,
        count_items_by_product_fn=lambda pids: {},
        count_raw_sources_by_product_fn=lambda pids: {},
        first_thumb_item_by_product_fn=lambda pids: {},
        list_item_filenames_by_product_fn=lambda pids, limit_per: {},
        lang_coverage_by_product_fn=lambda pids: {},
        get_product_covers_batch_fn=lambda pids: {},
        list_product_skus_batch_fn=lambda pids: {},
        list_xmyc_unit_prices_fn=lambda skus: {},
        get_configured_rmb_per_usd_fn=lambda: 6.83,
        get_product_ad_summary_cache_fn=lambda pids: {},
        get_product_lang_ad_summary_cache_fn=lambda pids: {},
        serialize_product_fn=lambda *args, **kwargs: {},
    )

    assert captured["keyword"] == ""
    assert captured["archived"] is False
    assert captured["offset"] == 0
    assert captured["xmyc_match"] == "all"
    assert captured["roas_status"] == "all"
    assert captured["delivery_status"] == "all"
    assert payload == {"items": [], "total": 0, "page": 1, "page_size": 20}


def test_build_products_list_response_defaults_invalid_delivery_status():
    from web.services.media_products_listing import build_products_list_response

    captured = {}

    def list_products(_user_id, **kwargs):
        captured.update(kwargs)
        return ([], 0)

    build_products_list_response(
        {"delivery_status": "paused"},
        list_products_fn=list_products,
        count_items_by_product_fn=lambda pids: {},
        count_raw_sources_by_product_fn=lambda pids: {},
        first_thumb_item_by_product_fn=lambda pids: {},
        list_item_filenames_by_product_fn=lambda pids, limit_per: {},
        lang_coverage_by_product_fn=lambda pids: {},
        get_product_covers_batch_fn=lambda pids: {},
        list_product_skus_batch_fn=lambda pids: {},
        list_xmyc_unit_prices_fn=lambda skus: {},
        get_configured_rmb_per_usd_fn=lambda: 6.83,
        get_product_ad_summary_cache_fn=lambda pids: {},
        get_product_lang_ad_summary_cache_fn=lambda pids: {},
        serialize_product_fn=lambda *args, **kwargs: {},
    )

    assert captured["delivery_status"] == "all"


def test_serialize_product_skus_includes_actual_roas_snapshot():
    spec = importlib.util.spec_from_file_location(
        "medias_serializers_under_test",
        ROOT / "web" / "routes" / "medias" / "_serializers.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    rows = [{"id": 1, "dianxiaomi_sku": "SKU-A"}]

    out = module._serialize_product_skus(
        rows,
        sku_actual_roas_index={"SKU-A": {"value": 2.1, "fee_source": "real"}},
    )

    assert out[0]["actual_breakeven_roas"] == {"value": 2.1, "fee_source": "real"}
