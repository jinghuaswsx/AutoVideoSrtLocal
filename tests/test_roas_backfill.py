from datetime import datetime
from decimal import Decimal
import json

import pytest

from appcore import roas_backfill as mod


def test_shopify_pricing_modes_picks_top_frequency(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda *a, **kw: [
        {"product_id": 100, "lineitem_price": Decimal("29.95"), "shipping": Decimal("6.99"), "freq": 502},
        {"product_id": 100, "lineitem_price": Decimal("29.95"), "shipping": Decimal("10.99"), "freq": 90},
        {"product_id": 100, "lineitem_price": Decimal("31.10"), "shipping": Decimal("6.99"), "freq": 8},
        {"product_id": 200, "lineitem_price": Decimal("19.99"), "shipping": None, "freq": 5},
    ])
    out = mod._shopify_pricing_modes()
    by_pid = {r["product_id"]: r for r in out}
    assert by_pid[100]["price"] == Decimal("29.95")
    assert by_pid[100]["shipping"] == Decimal("6.99")
    assert by_pid[100]["sample_size"] == 502 + 90 + 8
    assert by_pid[200]["price"] == Decimal("19.99")
    assert by_pid[200]["shipping"] is None


def test_backfill_shopify_default_uses_coalesce(monkeypatch):
    captured = []
    monkeypatch.setattr(mod, "query", lambda *a, **kw: [
        {"product_id": 5, "lineitem_price": Decimal("10"), "shipping": Decimal("3"), "freq": 100},
    ])
    monkeypatch.setattr(mod, "execute", lambda sql, params: captured.append((sql, params)))
    result = mod.backfill_shopify_fields()
    assert result == {"candidates": 1, "updated": 1}
    sql, params = captured[0]
    assert "COALESCE" in sql
    assert params == (Decimal("10"), Decimal("3"), 5)


def test_backfill_shopify_force_overwrites(monkeypatch):
    captured = []
    monkeypatch.setattr(mod, "query", lambda *a, **kw: [
        {"product_id": 7, "lineitem_price": Decimal("12"), "shipping": Decimal("4"), "freq": 5},
    ])
    monkeypatch.setattr(mod, "execute", lambda sql, params: captured.append((sql, params)))
    mod.backfill_shopify_fields(force=True)
    sql, params = captured[0]
    assert "COALESCE" not in sql
    assert "standalone_price=%s" in sql
    assert params == (Decimal("12"), Decimal("4"), 7)


def test_backfill_shopify_dry_run_does_not_write(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda *a, **kw: [
        {"product_id": 1, "lineitem_price": Decimal("9"), "shipping": Decimal("2"), "freq": 3},
    ])

    def must_not_call(*a, **kw):
        raise AssertionError("execute should not be called in dry-run")

    monkeypatch.setattr(mod, "execute", must_not_call)
    result = mod.backfill_shopify_fields(dry_run=True)
    assert result == {"candidates": 1, "updated": 0}


def test_query_logistic_fees_by_pid(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda sql, params=None: [
        {"product_id": 1, "logistic_fee": 61.6},
        {"product_id": 1, "logistic_fee": 62.1},
        {"product_id": 2, "logistic_fee": 80.0},
        {"product_id": 3, "logistic_fee": 12.5},
    ])
    from datetime import datetime
    fees = mod._query_logistic_fees_by_pid(
        {1, 2, 3},
        datetime(2026, 4, 2),
        datetime(2026, 5, 2),
    )
    assert sorted(fees[1]) == [61.6, 62.1]
    assert fees[2] == [80.0]
    assert fees[3] == [12.5]
    assert 4 not in fees


def test_dianxiaomi_shop_groups_picks_dominant_shop(monkeypatch):
    rows = {
        "pids": [{"id": 100}, {"id": 200}],
        "pairs": [
            {"product_id": 100, "dxm_shop_id": "shopA", "n": 30},
            {"product_id": 100, "dxm_shop_id": "shopB", "n": 5},
            {"product_id": 200, "dxm_shop_id": "shopA", "n": 50},
        ],
    }
    call = {"i": 0}

    def fake_query(sql, params=None):
        call["i"] += 1
        if "FROM media_products" in sql:
            return rows["pids"]
        return rows["pairs"]

    monkeypatch.setattr(mod, "query", fake_query)
    pid_to_shop, shop_to_pids = mod._dianxiaomi_shop_groups(force=False)
    assert pid_to_shop[100] == "shopA"
    assert pid_to_shop[200] == "shopA"
    assert shop_to_pids["shopA"] == {100, 200}


def test_dianxiaomi_shop_groups_force_skips_null_filter(monkeypatch):
    captured = {}

    def fake_query(sql, params=None):
        captured.setdefault("sqls", []).append(sql)
        if "FROM media_products" in sql:
            return [{"id": 1}]
        return []

    monkeypatch.setattr(mod, "query", fake_query)
    mod._dianxiaomi_shop_groups(force=True)
    media_products_sql = next(s for s in captured["sqls"] if "FROM media_products" in s)
    assert "packet_cost_actual IS NULL" not in media_products_sql
    assert "1 = 1" in media_products_sql


def test_sku_to_pid_map_builds_lookup(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda sql, params=None: [
        {"product_id": 1, "product_sku": "sku-1box", "product_display_sku": "sku-1box", "n": 100},
        {"product_id": 1, "product_sku": "sku-1box", "product_display_sku": "115-1", "n": 5},
        {"product_id": 2, "product_sku": "WW-1", "product_display_sku": "WW-1", "n": 10},
    ])
    mapping = mod._sku_to_pid_map({1, 2})
    assert mapping["sku-1box"] == 1
    assert mapping["115-1"] == 1
    assert mapping["WW-1"] == 2


def test_sku_to_pid_map_empty_returns_empty(monkeypatch):
    assert mod._sku_to_pid_map(set()) == {}


def test_backfill_parcel_costs_end_to_end(monkeypatch):
    monkeypatch.setattr(mod, "_dianxiaomi_shop_groups", lambda force: (
        {1: "shopA", 2: "shopA"},
        {"shopA": {1, 2}},
    ))
    # _query_logistic_fees_by_pid → SQL 聚合 logistic_fee
    monkeypatch.setattr(mod, "query", lambda sql, params=None: [
        {"product_id": 1, "logistic_fee": 61.6},
        {"product_id": 1, "logistic_fee": 62.1},
        {"product_id": 1, "logistic_fee": 80.0},
        {"product_id": 2, "logistic_fee": 30.0},
        {"product_id": 2, "logistic_fee": 32.0},
    ])

    writes = []
    monkeypatch.setattr(mod, "execute", lambda sql, params: writes.append(params))

    fixed_now = datetime(2026, 5, 4, 12, 0, 0)
    result = mod.backfill_parcel_costs_via_dxm(
        days=30,
        now_func=lambda: fixed_now,
    )
    assert result["candidates"] == 2
    assert result["shops"] == 1
    assert result["with_fees"] == 2
    assert result["updated"] == 2
    assert result["window_start"] == "2026-04-02"
    assert result["window_end"] == "2026-05-02"
    pid1 = next(p for p in writes if p[2] == 1)
    pid2 = next(p for p in writes if p[2] == 2)
    # product 1 fees: 61.6, 62.1, 80.0 -> median 62.1
    assert pid1[0] == pytest.approx(62.1)
    assert pid1[1] == pytest.approx(62.1)
    # product 2 fees: 30, 32 -> median 31.0
    assert pid2[0] == pytest.approx(31.0)


def test_backfill_parcel_costs_force_uses_overwrite_sql(monkeypatch):
    monkeypatch.setattr(mod, "_dianxiaomi_shop_groups", lambda force: (
        {1: "shopA"}, {"shopA": {1}},
    ))
    monkeypatch.setattr(mod, "query", lambda sql, params=None: [
        {"product_id": 1, "logistic_fee": 50.0},
    ])

    captured_sqls = []
    monkeypatch.setattr(mod, "execute", lambda sql, params: captured_sqls.append(sql))
    mod.backfill_parcel_costs_via_dxm(
        force=True,
        now_func=lambda: datetime(2026, 5, 4),
    )
    assert any("packet_cost_estimated=%s" in s and "COALESCE" not in s for s in captured_sqls)


def test_backfill_parcel_costs_no_candidates_returns_zero(monkeypatch):
    monkeypatch.setattr(mod, "_dianxiaomi_shop_groups", lambda force: ({}, {}))
    result = mod.backfill_parcel_costs_via_dxm()
    assert result == {"candidates": 0, "shops": 0, "with_fees": 0, "updated": 0}


def test_fetch_first_shopify_variant_price_uses_first_variant(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "variants": [
                    {"id": 11, "sku": "FIRST", "price": 2999},
                    {"id": 22, "sku": "SECOND", "price": 1999},
                ]
            }).encode("utf-8")

    monkeypatch.setattr(mod, "urlopen", lambda req, timeout: FakeResponse())

    out = mod.fetch_first_shopify_variant_price("https://shop.test/products/item.js")

    assert out["value"] == Decimal("29.99")
    assert out["variant_id"] == 11
    assert out["sku"] == "FIRST"
    assert out["source"] == "shopify_product_js_first_variant"


def test_shopify_product_js_urls_include_product_link_localized_and_default(monkeypatch):
    from appcore import product_link_domains

    monkeypatch.setattr(
        product_link_domains,
        "resolve_product_page_url_rows",
        lambda product, lang: [{"url": "https://newjoyloo.com/products/item-rjc"}],
    )
    product = {
        "id": 10,
        "product_code": "item-rjc",
        "product_link": "https://shop.test/products/item",
        "localized_links_json": json.dumps({
            "fr": {"newjoyloo.com": "https://newjoyloo.com/fr/products/item-rjc"},
        }),
    }

    urls = mod._shopify_product_js_urls(product)

    assert urls == [
        "https://shop.test/products/item.js",
        "https://newjoyloo.com/products/item-rjc.js",
        "https://newjoyloo.com/fr/products/item-rjc.js",
    ]


def test_backfill_complete_product_roas_estimates_missing_inputs_and_sources(monkeypatch):
    products = [
        {
            "id": 1,
            "product_code": "portable-car-urinal-bucket-rjc",
            "product_link": None,
            "localized_links_json": None,
            "standalone_price": None,
            "standalone_shipping_fee": None,
            "purchase_price": None,
            "packet_cost_estimated": None,
            "packet_cost_actual": None,
            "package_length_cm": None,
            "package_width_cm": None,
            "package_height_cm": None,
            "roas_inputs_source_json": None,
        },
        {
            "id": 2,
            "product_code": "actual-source-rjc",
            "product_link": None,
            "localized_links_json": None,
            "standalone_price": Decimal("39.99"),
            "standalone_shipping_fee": None,
            "purchase_price": None,
            "packet_cost_estimated": None,
            "packet_cost_actual": None,
            "package_length_cm": Decimal("20"),
            "package_width_cm": Decimal("8"),
            "package_height_cm": Decimal("6"),
            "roas_inputs_source_json": None,
        },
    ]
    writes = []

    monkeypatch.setattr(mod, "_active_roas_products", lambda **kwargs: products)
    monkeypatch.setattr(mod, "_variant_prices_by_pid", lambda product_ids: {})
    monkeypatch.setattr(mod, "_shopify_price_modes_by_pid", lambda: {})
    monkeypatch.setattr(mod, "_purchase_costs_by_pid", lambda product_ids: {
        2: {
            "value": Decimal("12.34"),
            "basis": "actual",
            "source": "dianxiaomi_yuncang_skus_median",
            "sample_size": 3,
        },
    })
    monkeypatch.setattr(mod, "_logistic_costs_by_pid", lambda *args, **kwargs: {
        2: {
            "value": Decimal("23.45"),
            "basis": "actual",
            "source": "dianxiaomi_logistic_fee_median",
            "sample_size": 5,
            "window_start": "2026-05-01",
            "window_end": "2026-05-31",
        },
    })
    monkeypatch.setattr(mod, "_shipping_fees_by_pid", lambda product_ids: {
        2: {
            "value": Decimal("7.77"),
            "basis": "actual",
            "source": "shopify_orders_average_shipping",
            "sample_size": 4,
        },
    })
    monkeypatch.setattr(mod, "_shopify_product_js_urls", lambda product: ["https://shop.test/products/%s.js" % product["product_code"]])
    monkeypatch.setattr(mod, "execute", lambda sql, params: writes.append((sql, params)))

    def fetch_price(url, *, timeout_s):
        assert timeout_s == 12
        if "portable-car" in url:
            return {"value": Decimal("29.99"), "source": "shopify_product_js_first_variant", "url": url}
        return None

    result = mod.backfill_complete_product_roas(
        rmb_per_usd=Decimal("7"),
        product_ids=[1, 2],
        fetch_price_fn=fetch_price,
        now_func=lambda: datetime(2026, 6, 12, 10, 0, 0),
    )

    assert result["products_total"] == 2
    assert result["completed"] == 2
    assert result["missing_price"] == 0
    assert result["estimated_price"] == 0
    assert result["estimated_purchase"] == 1
    assert result["estimated_packet"] == 1
    assert result["estimated_shipping"] == 1
    assert result["default_dimensions"] == 1
    assert result["updated"] == 2
    assert "products" not in result
    assert len(writes) == 2

    first_sql, first_params = writes[0]
    assert "roas_inputs_source_json=%s" in first_sql
    assert first_params[-1] == 1
    assert Decimal(str(first_params[0])) == Decimal("29.99")
    assert Decimal(str(first_params[1])) == Decimal("20.99")
    assert Decimal(str(first_params[2])) == Decimal("41.99")
    assert Decimal(str(first_params[3])) == Decimal("6.99")
    first_source = json.loads(first_params[-2])
    assert first_source["standalone_price"]["basis"] == "actual"
    assert first_source["purchase_price"]["basis"] == "estimated"
    assert first_source["packet_cost_estimated"]["basis"] == "estimated"
    assert first_source["standalone_shipping_fee"]["source"] == "default_6_99"
    assert first_source["package_length_cm"]["basis"] == "default"
    assert first_source["calculation"]["effective_basis"] == "estimated"
    assert first_source["calculation"]["effective_roas"] is not None

    second_source = json.loads(writes[1][1][-2])
    assert second_source["purchase_price"]["basis"] == "actual"
    assert second_source["packet_cost_actual"]["basis"] == "actual"
    assert second_source["packet_cost_estimated"]["source"] == "packet_cost_actual_mirror"
    assert second_source["standalone_shipping_fee"]["basis"] == "actual"


def test_backfill_complete_product_roas_uses_estimated_price_fallback(monkeypatch):
    products = [
        {
            "id": 1,
            "product_code": "missing-price-rjc",
            "product_link": None,
            "localized_links_json": None,
            "standalone_price": None,
            "standalone_shipping_fee": None,
            "purchase_price": None,
            "packet_cost_estimated": None,
            "packet_cost_actual": None,
            "package_length_cm": None,
            "package_width_cm": None,
            "package_height_cm": None,
            "roas_inputs_source_json": None,
        },
        {
            "id": 2,
            "product_code": "price-source-rjc",
            "product_link": None,
            "localized_links_json": None,
            "standalone_price": Decimal("49.99"),
            "standalone_shipping_fee": None,
            "purchase_price": None,
            "packet_cost_estimated": None,
            "packet_cost_actual": None,
            "package_length_cm": None,
            "package_width_cm": None,
            "package_height_cm": None,
            "roas_inputs_source_json": None,
        },
    ]
    writes = []

    monkeypatch.setattr(mod, "_active_roas_products", lambda **kwargs: products)
    monkeypatch.setattr(mod, "_variant_prices_by_pid", lambda product_ids: {})
    monkeypatch.setattr(mod, "_shopify_price_modes_by_pid", lambda: {})
    monkeypatch.setattr(mod, "_purchase_costs_by_pid", lambda product_ids: {})
    monkeypatch.setattr(mod, "_logistic_costs_by_pid", lambda *args, **kwargs: {})
    monkeypatch.setattr(mod, "_shipping_fees_by_pid", lambda product_ids: {})
    monkeypatch.setattr(mod, "_shopify_product_js_urls", lambda product: [])
    monkeypatch.setattr(mod, "execute", lambda sql, params: writes.append((sql, params)))

    result = mod.backfill_complete_product_roas(
        rmb_per_usd=Decimal("7"),
        fetch_price_fn=lambda *args, **kwargs: None,
        now_func=lambda: datetime(2026, 6, 12, 10, 0, 0),
    )

    assert result["completed"] == 2
    assert result["missing_price"] == 0
    assert result["estimated_price"] == 1
    first_source = json.loads(writes[0][1][-2])
    assert Decimal(str(writes[0][1][0])) == Decimal("49.99")
    assert first_source["standalone_price"]["basis"] == "estimated"
    assert first_source["standalone_price"]["source"] == "active_product_price_median"
    assert first_source["standalone_price"]["sample_size"] == 1


def test_active_roas_products_filters_product_ids_and_codes(monkeypatch):
    captured = {}

    def fake_query(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(mod, "query", fake_query)

    mod._active_roas_products(product_ids=[3, 4], product_codes=["a-rjc", "b-rjc"])

    assert "id IN (%s,%s)" in captured["sql"]
    assert "product_code IN (%s,%s)" in captured["sql"]
    assert "product_link" in captured["sql"]
    assert captured["params"] == (3, 4, "a-rjc", "b-rjc")
