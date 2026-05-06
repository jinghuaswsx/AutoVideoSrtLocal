from __future__ import annotations


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

    payload = build_products_list_response(
        {
            "keyword": "  box cutter  ",
            "archived": "yes",
            "page": "3",
            "xmyc_match": "matched",
            "roas_status": "complete",
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
        get_configured_rmb_per_usd_fn=lambda: 7.1,
        serialize_product_fn=serialize_product,
    )

    assert calls["list_products"] == {
        "keyword": "box cutter",
        "archived": True,
        "offset": 40,
        "limit": 20,
        "xmyc_match": "matched",
        "roas_status": "complete",
    }
    assert calls["xmyc_skus"] == ["sku-a", "sku-b"]
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
    assert serialized[1][3]["lang_coverage"] == {"de": 1}


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
        serialize_product_fn=lambda *args, **kwargs: {},
    )

    assert captured["keyword"] == ""
    assert captured["archived"] is False
    assert captured["offset"] == 0
    assert captured["xmyc_match"] == "all"
    assert captured["roas_status"] == "all"
    assert payload == {"items": [], "total": 0, "page": 1, "page_size": 20}
