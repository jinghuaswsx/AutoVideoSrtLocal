from __future__ import annotations

from decimal import Decimal

from appcore import product_price_shipping_fill as mod


def test_candidate_english_urls_prefers_product_link_and_dedupes_resolved_links():
    product = {
        "id": 1,
        "product_code": "demo-rjc",
        "product_link": "https://source.example/products/demo?variant=123",
    }

    rows = mod.candidate_english_urls(
        product,
        resolve_product_page_url_rows_fn=lambda product, lang: [
            {"domain": "newjoyloo.com", "url": "https://newjoyloo.com/products/demo-rjc"},
            {"domain": "source.example", "url": "https://source.example/products/demo"},
        ],
    )

    assert rows == [
        {"source": "product_link", "url": "https://source.example/products/demo"},
        {"source": "resolved_en:newjoyloo.com", "url": "https://newjoyloo.com/products/demo-rjc"},
    ]


def test_inspect_product_price_uses_first_sku_price_in_range():
    def fake_fetch(product, timeout_seconds):
        assert product["product_link"] == "https://example.com/products/demo"
        return [
            {"shopify_variant_id": "v1", "shopify_sku": "sku1", "shopify_price": "12.95"},
            {"shopify_variant_id": "v2", "shopify_sku": "sku2", "shopify_price": "19.95"},
        ]

    result = mod.inspect_product_price(
        {"id": 5, "product_code": "demo", "product_link": "https://example.com/products/demo"},
        fetch_sku_rows_fn=fake_fetch,
        resolve_product_page_url_rows_fn=lambda product, lang: [],
        sleep_fn=lambda seconds: None,
    )

    assert result["status"] == "ok"
    assert result["price"] == "12.95"
    assert result["shipping_fee"] == "7.00"
    assert result["variant_id"] == "v1"
    assert result["sku"] == "sku1"


def test_inspect_product_price_skips_out_of_range_first_sku():
    result = mod.inspect_product_price(
        {"id": 5, "product_code": "demo", "product_link": "https://example.com/products/demo"},
        fetch_sku_rows_fn=lambda product, timeout_seconds: [{"shopify_price": "9.99"}],
        resolve_product_page_url_rows_fn=lambda product, lang: [],
        sleep_fn=lambda seconds: None,
    )

    assert result["status"] == "out_of_range"
    assert result["price"] == "9.99"


def test_fill_product_price_shipping_dry_run_does_not_write():
    products = [
        {
            "id": 7,
            "product_code": "demo",
            "product_link": "https://example.com/products/demo",
            "standalone_price": None,
            "tk_sale_price": None,
            "standalone_shipping_fee": None,
        }
    ]

    def must_not_execute(sql, params):
        raise AssertionError("dry-run must not write")

    summary = mod.fill_product_price_shipping(
        dry_run=True,
        query_fn=lambda sql, args: products,
        execute_fn=must_not_execute,
        resolve_product_page_url_rows_fn=lambda product, lang: [],
        fetch_sku_rows_fn=lambda product, timeout_seconds: [{"shopify_price": "29.95"}],
        sleep_fn=lambda seconds: None,
        max_workers=1,
    )

    assert summary["scanned"] == 1
    assert summary["price_candidates"] == 1
    assert summary["would_update"] == 1
    assert summary["updated"] == 0
    assert summary["samples"]["updates"][0]["fields"] == [
        "standalone_price",
        "tk_sale_price",
        "standalone_shipping_fee",
    ]


def test_fill_product_price_shipping_default_uses_coalesce_update():
    products = [
        {
            "id": 8,
            "product_code": "demo",
            "product_link": "https://example.com/products/demo",
            "standalone_price": Decimal("12.95"),
            "tk_sale_price": None,
            "standalone_shipping_fee": None,
        }
    ]
    writes = []

    summary = mod.fill_product_price_shipping(
        dry_run=False,
        query_fn=lambda sql, args: products,
        execute_fn=lambda sql, params: writes.append((sql, params)) or 1,
        resolve_product_page_url_rows_fn=lambda product, lang: [],
        fetch_sku_rows_fn=lambda product, timeout_seconds: [{"shopify_price": "17.95"}],
        sleep_fn=lambda seconds: None,
        max_workers=1,
    )

    assert summary["updated"] == 1
    sql, params = writes[0]
    assert "COALESCE(standalone_price" in sql
    assert params == (Decimal("17.95"), Decimal("17.95"), Decimal("7.00"), 8)
    assert summary["samples"]["updates"][0]["fields"] == [
        "tk_sale_price",
        "standalone_shipping_fee",
    ]


def test_fill_product_price_shipping_force_overwrites_all_three_fields():
    products = [
        {
            "id": 9,
            "product_code": "demo",
            "product_link": "https://example.com/products/demo",
            "standalone_price": Decimal("12.95"),
            "tk_sale_price": Decimal("12.95"),
            "standalone_shipping_fee": Decimal("8.00"),
        }
    ]
    writes = []

    summary = mod.fill_product_price_shipping(
        force=True,
        dry_run=False,
        query_fn=lambda sql, args: products,
        execute_fn=lambda sql, params: writes.append((sql, params)) or 1,
        resolve_product_page_url_rows_fn=lambda product, lang: [],
        fetch_sku_rows_fn=lambda product, timeout_seconds: [{"shopify_price": "18.95"}],
        sleep_fn=lambda seconds: None,
        max_workers=1,
    )

    assert summary["updated"] == 1
    sql, params = writes[0]
    assert "COALESCE" not in sql
    assert "tk_sale_price=%s" in sql
    assert params == (Decimal("18.95"), Decimal("18.95"), Decimal("7.00"), 9)
