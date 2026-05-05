from __future__ import annotations


def test_build_refresh_product_shopify_sku_response_rejects_missing_shopifyid_before_fetch():
    from web.services.media_shopify_sku_refresh import build_refresh_product_shopify_sku_response

    called = []

    result = build_refresh_product_shopify_sku_response(
        42,
        {"id": 42, "shopifyid": "   "},
        fetch_shopify_and_dxm_fn=lambda: called.append("fetch"),
        build_pair_rows_fn=lambda *args: {},
        update_product_fn=lambda *args, **kwargs: called.append("update"),
        replace_product_skus_fn=lambda *args, **kwargs: called.append("replace"),
        list_product_skus_fn=lambda pid: [],
        list_xmyc_unit_prices_fn=lambda skus: {},
        get_configured_rmb_per_usd_fn=lambda: 7.0,
        serialize_product_skus_fn=lambda *args, **kwargs: [],
    )

    assert result.status_code == 400
    assert result.payload["error"] == "missing_shopifyid"
    assert called == []


def test_build_refresh_product_shopify_sku_response_maps_fetch_error():
    from web.services.media_shopify_sku_refresh import build_refresh_product_shopify_sku_response

    result = build_refresh_product_shopify_sku_response(
        42,
        {"id": 42, "shopifyid": "SP1"},
        fetch_shopify_and_dxm_fn=lambda: (_ for _ in ()).throw(RuntimeError("cdp closed")),
        build_pair_rows_fn=lambda *args: {},
        update_product_fn=lambda *args, **kwargs: None,
        replace_product_skus_fn=lambda *args, **kwargs: None,
        list_product_skus_fn=lambda pid: [],
        list_xmyc_unit_prices_fn=lambda skus: {},
        get_configured_rmb_per_usd_fn=lambda: 7.0,
        serialize_product_skus_fn=lambda *args, **kwargs: [],
    )

    assert result.status_code == 502
    assert result.payload == {"error": "fetch_failed", "message": "店小秘数据拉取失败：cdp closed"}


def test_build_refresh_product_shopify_sku_response_maps_missing_shopify_product():
    from web.services.media_shopify_sku_refresh import build_refresh_product_shopify_sku_response

    result = build_refresh_product_shopify_sku_response(
        42,
        {"id": 42, "shopifyid": "SP1"},
        fetch_shopify_and_dxm_fn=lambda: ([{"shopify_product_id": "SP2"}], {}),
        build_pair_rows_fn=lambda shopify_products, dxm_index: {"SP2": []},
        update_product_fn=lambda *args, **kwargs: None,
        replace_product_skus_fn=lambda *args, **kwargs: None,
        list_product_skus_fn=lambda pid: [],
        list_xmyc_unit_prices_fn=lambda skus: {},
        get_configured_rmb_per_usd_fn=lambda: 7.0,
        serialize_product_skus_fn=lambda *args, **kwargs: [],
    )

    assert result.status_code == 404
    assert result.payload["error"] == "shopify_product_not_found"
    assert "SP1" in result.payload["message"]


def test_build_refresh_product_shopify_sku_response_updates_product_and_serializes_skus():
    from web.services.media_shopify_sku_refresh import build_refresh_product_shopify_sku_response

    captured = {}
    product = {
        "id": 42,
        "shopifyid": "SP1",
        "purchase_price": "10",
        "packet_cost_estimated": "2",
        "packet_cost_actual": "3",
        "standalone_shipping_fee": "4",
    }
    pairs = [
        {"shopify_variant_id": "V1", "dianxiaomi_sku_code": "D1"},
        {"shopify_variant_id": "V2", "dianxiaomi_sku_code": ""},
    ]
    fresh_skus = [{"dianxiaomi_sku": "DX1", "shopify_price": "9.99"}]
    xmyc_index = {"DX1": {"unit_price": "12.34"}}

    def fake_list_product_skus(pid):
        captured["list_pid"] = pid
        return fresh_skus

    result = build_refresh_product_shopify_sku_response(
        42,
        product,
        fetch_shopify_and_dxm_fn=lambda: (
            [{"shopify_product_id": "SP1", "shopify_title": " New Title "}],
            {"sku": "index"},
        ),
        build_pair_rows_fn=lambda shopify_products, dxm_index: {"SP1": pairs},
        update_product_fn=lambda pid, **kwargs: captured.update(update=(pid, kwargs)),
        replace_product_skus_fn=lambda pid, rows, **kwargs: captured.update(
            replace=(pid, rows, kwargs)
        ),
        list_product_skus_fn=fake_list_product_skus,
        list_xmyc_unit_prices_fn=lambda skus: captured.update(xmyc_skus=skus) or xmyc_index,
        get_configured_rmb_per_usd_fn=lambda: 7.2,
        serialize_product_skus_fn=lambda rows, **kwargs: captured.update(
            serialize=(rows, kwargs)
        )
        or [{"sku": "serialized"}],
    )

    assert result.status_code == 200
    assert result.payload == {
        "ok": True,
        "shopify_title": "New Title",
        "skus": [{"sku": "serialized"}],
        "summary": {"variant_pairs": 2, "pairs_with_dxm": 1},
    }
    assert captured["update"] == (42, {"shopify_title": "New Title"})
    assert captured["replace"] == (42, pairs, {"source": "manual"})
    assert captured["list_pid"] == 42
    assert captured["xmyc_skus"] == ["DX1"]
    assert captured["serialize"] == (
        fresh_skus,
        {
            "cost_inputs": {
                "purchase_price": "10",
                "packet_cost_estimated": "2",
                "packet_cost_actual": "3",
                "standalone_shipping_fee": "4",
            },
            "rmb_per_usd": 7.2,
            "xmyc_index": xmyc_index,
        },
    )
