from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from appcore import order_analytics as oa


def test_extract_shopify_product_id_from_line_prefers_product_id():
    line = {"productId": "8560559554733", "productUrl": "https://example.com/products/demo"}

    assert oa.extract_dianxiaomi_shopify_product_id(line) == "8560559554733"


def test_extract_shopify_product_id_from_url_fallback():
    line = {"productUrl": "https://admin.shopify.com/store/demo/products/8560559554733"}

    assert oa.extract_dianxiaomi_shopify_product_id(line) == "8560559554733"


def test_build_dianxiaomi_product_scope_excludes_smartgearx(monkeypatch):
    monkeypatch.setattr(
        oa,
        "query",
        lambda sql, args=(): [
            {
                "id": 1,
                "product_code": "newjoy-demo",
                "shopifyid": "111",
                "product_link": None,
                "localized_links_json": '{"en":"https://newjoyloo.com/products/newjoy-demo"}',
            },
            {
                "id": 2,
                "product_code": "omurio-demo",
                "shopifyid": "222",
                "product_link": "https://omurio.com/products/omurio-demo",
                "localized_links_json": "{}",
            },
            {
                "id": 3,
                "product_code": "smart-demo",
                "shopifyid": "333",
                "product_link": "https://smartgearx.com/products/smart-demo",
                "localized_links_json": "{}",
            },
        ],
    )

    scope = oa.build_dianxiaomi_product_scope(["newjoy", "omurio"])

    assert set(scope.by_shopify_id) == {"111", "222"}
    assert scope.by_shopify_id["111"]["site_code"] == "newjoy"
    assert scope.by_shopify_id["222"]["site_code"] == "omurio"
    assert set(scope.by_handle) == {"newjoy-demo", "omurio-demo"}
    assert scope.excluded_shopify_ids == {"333"}
    assert scope.excluded_handles == {"smart-demo"}


def test_build_dianxiaomi_product_scope_keeps_handle_without_site_link(monkeypatch):
    monkeypatch.setattr(
        oa,
        "query",
        lambda sql, args=(): [
            {
                "id": 9,
                "product_code": "sonic-lens-refresher-rjc",
                "shopifyid": "8560559554733",
                "product_link": None,
                "localized_links_json": "{}",
            },
        ],
    )

    scope = oa.build_dianxiaomi_product_scope(["newjoy", "omurio"])
    product = oa._resolve_dianxiaomi_line_product(
        {
            "productId": "45931658477741",
            "productUrl": "https://newjoyloo.com/products/sonic-lens-refresher",
        },
        "45931658477741",
        scope,
    )

    assert scope.by_handle["sonic-lens-refresher"]["product_id"] == 9
    assert product["product_id"] == 9
    assert product["product_code"] == "sonic-lens-refresher-rjc"
    assert product["site_code"] == "newjoy"


def test_normalize_dianxiaomi_order_matches_product_code_without_rjc_suffix(monkeypatch):
    """订单行 product code 不带 -rjc 时，应匹配素材库带 -rjc 的 product_code。"""

    def fake_query(sql, args=()):
        if "FROM media_product_shopify_ids" in sql:
            return []
        if "FROM media_products" in sql:
            if "WHERE deleted_at IS NULL AND shopifyid IS NOT NULL" in sql:
                return []
            return [
                {
                    "id": 15,
                    "product_code": "sonic-lens-refresher-rjc",
                    "shopifyid": None,
                    "product_link": "https://newjoyloo.com/products/sonic-lens-refresher-rjc",
                    "localized_links_json": "{}",
                },
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(oa, "query", fake_query)

    scope = oa.build_dianxiaomi_product_scope(["newjoy", "omurio"])
    order = {
        "id": "9015",
        "productList": [
            {
                "productId": "45931658477741",
                "productCode": "sonic-lens-refresher",
                "productSku": "45931658477741",
                "quantity": "1",
                "price": "24.95",
                "productUrl": "https://newjoyloo.com/admin/order-line/9015",
            }
        ],
    }

    rows, skipped = oa.normalize_dianxiaomi_order(
        order,
        scope,
        {},
        rmb_per_usd=Decimal("6.83"),
    )

    assert skipped == 0
    assert rows[0]["product_id"] == 15
    assert rows[0]["product_code"] == "sonic-lens-refresher-rjc"
    assert rows[0]["site_code"] == "newjoy"


def test_normalize_dianxiaomi_order_lines_keeps_requested_sites_and_amounts():
    scope = oa.DianxiaomiProductScope(
        by_shopify_id={
            "8560559554733": {
                "product_id": 7,
                "product_code": "demo-product",
                "site_code": "newjoy",
                "shopifyid": "8560559554733",
            }
        },
        by_handle={},
        by_domain_shopify_id={},
        excluded_shopify_ids=set(),
        excluded_handles=set(),
        requested_site_codes={"newjoy", "omurio"},
    )
    order = {
        "id": "9001",
        "shopId": "8477915",
        "shopName": "Joyeloo",
        "orderId": "DXM-1",
        "extendedOrderId": "#1001",
        "packageNumber": "PKG-1",
        "platform": "shopify",
        "state": "paid",
        "buyerName": "Ada",
        "buyerAccount": "ada@example.com",
        "buyerCountry": "US",
        "countryCN": "美国",
        "orderAmount": "17.94",
        "orderUnit": "USD",
        "shipAmount": "4.99",
        "refundAmountUsd": "0",
        "orderCreateTime": "2026-04-27 10:00:00",
        "orderPayTime": "2026-04-27 10:03:00",
        "productList": [
            {
                "productId": "8560559554733",
                "productName": "Demo Product",
                "productSku": "SKU-A",
                "productSubSku": "SUB-A",
                "productDisplaySku": "SKU-A / Red",
                "quantity": "2",
                "price": "12.95",
                "attrListStr": "Red",
            }
        ],
    }
    profits = {"9001": {"amountCNY": "128.00", "logisticFee": "6.50", "profit": "30.10"}}

    rows, skipped = oa.normalize_dianxiaomi_order(order, scope, profits)

    assert skipped == 0
    assert rows[0]["site_code"] == "newjoy"
    assert rows[0]["product_id"] == 7
    assert rows[0]["quantity"] == 2
    assert rows[0]["unit_price"] == 12.95
    assert rows[0]["line_amount"] == 25.9
    assert rows[0]["ship_amount"] == 4.99
    assert rows[0]["amount_with_shipping"] == 17.94
    assert rows[0]["logistic_fee"] == 6.5
    assert rows[0]["order_paid_at"] == datetime(2026, 4, 27, 10, 3)
    assert rows[0]["attribution_time_at"] == datetime(2026, 4, 27, 10, 3)
    assert rows[0]["meta_business_date"].isoformat() == "2026-04-26"
    assert rows[0]["meta_window_start_at"] == datetime(2026, 4, 26, 16, 0)
    assert rows[0]["meta_window_end_at"] == datetime(2026, 4, 27, 16, 0)


def test_normalize_dianxiaomi_order_parses_millisecond_timestamps():
    scope = oa.DianxiaomiProductScope(
        by_shopify_id={
            "8560559554733": {
                "product_id": 7,
                "product_code": "demo-product",
                "site_code": "newjoy",
                "shopifyid": "8560559554733",
            }
        },
        by_handle={},
        by_domain_shopify_id={},
        excluded_shopify_ids=set(),
        excluded_handles=set(),
        requested_site_codes={"newjoy"},
    )
    order = {
        "id": "9001",
        "orderPayTime": 1777272317000,
        "shippedTime": 1777272434610,
        "productList": [{"productId": "8560559554733", "quantity": "1", "price": "10"}],
    }

    rows, skipped = oa.normalize_dianxiaomi_order(order, scope, {})

    assert skipped == 0
    assert rows[0]["order_paid_at"] == datetime(2026, 4, 27, 14, 45, 17)
    assert rows[0]["shipped_at"] == datetime(2026, 4, 27, 14, 47, 14)
    assert rows[0]["meta_business_date"].isoformat() == "2026-04-26"


def test_normalize_dianxiaomi_order_keeps_requested_site_from_order_line_url():
    scope = oa.DianxiaomiProductScope(
        by_shopify_id={},
        by_handle={},
        by_domain_shopify_id={},
        excluded_shopify_ids=set(),
        excluded_handles=set(),
        requested_site_codes={"newjoy", "omurio"},
    )
    order = {
        "id": "9003",
        "productList": [
            {
                "productId": "999",
                "productSku": "OMU",
                "quantity": "1",
                "price": "19.99",
                "productUrl": "https://omurio.com/products/demo",
            }
        ],
    }

    rows, skipped = oa.normalize_dianxiaomi_order(order, scope, {})

    assert skipped == 0
    assert rows[0]["site_code"] == "omurio"
    assert rows[0]["shopify_product_id"] == "999"


def test_normalize_dianxiaomi_order_matches_local_product_code_handle():
    scope = oa.DianxiaomiProductScope(
        by_shopify_id={},
        by_handle={
            "dino-glider-launcher-toy": {
                "product_id": 5,
                "product_code": "dino-glider-launcher-toy-rjc",
                "site_code": "newjoy",
                "shopifyid": "8552296546477",
            }
        },
        by_domain_shopify_id={},
        excluded_shopify_ids=set(),
        excluded_handles=set(),
        requested_site_codes={"newjoy", "omurio"},
    )
    order = {
        "id": "9004",
        "productList": [
            {
                "productId": "44731721711663",
                "productSku": "44731721711663",
                "quantity": "1",
                "price": "13.19",
                "productUrl": "https://shoplarke.com/products/dino-glider-launcher-toy",
            }
        ],
    }

    rows, skipped = oa.normalize_dianxiaomi_order(order, scope, {})

    assert skipped == 0
    assert rows[0]["product_id"] == 5
    assert rows[0]["product_code"] == "dino-glider-launcher-toy-rjc"
    assert rows[0]["site_code"] == "newjoy"


def test_normalize_dianxiaomi_order_skips_smartgearx_scope():
    scope = oa.DianxiaomiProductScope(
        by_shopify_id={},
        by_handle={},
        by_domain_shopify_id={},
        excluded_shopify_ids={"333"},
        excluded_handles=set(),
        requested_site_codes={"newjoy", "omurio"},
    )
    order = {
        "id": "9002",
        "productList": [{"productId": "333", "productSku": "S", "quantity": "1", "price": "9.99"}],
    }

    rows, skipped = oa.normalize_dianxiaomi_order(order, scope, {})

    assert rows == []
    assert skipped == 1


def test_start_and_finish_dianxiaomi_batch_use_expected_sql(monkeypatch):
    calls = []

    class Cursor:
        lastrowid = 42

        def execute(self, sql, args):
            calls.append(("cursor.execute", sql, args))

    class CursorContext(Cursor):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class Conn:
        def cursor(self):
            return CursorContext()

        def commit(self):
            calls.append(("commit", "", ()))

        def close(self):
            calls.append(("close", "", ()))

    monkeypatch.setattr(oa, "get_conn", lambda: Conn())
    monkeypatch.setattr(
        oa,
        "execute",
        lambda sql, args=(): calls.append(("execute", sql, args)) or 1,
    )

    batch_id = oa.start_dianxiaomi_order_import_batch("2026-01-01", "2026-01-02", ["newjoy", "omurio"], 2)
    oa.finish_dianxiaomi_order_import_batch(batch_id, "success", {"inserted_lines": 3})

    assert batch_id == 42
    assert "INSERT INTO dianxiaomi_order_import_batches" in calls[0][1]
    assert calls[0][2] == ("2026-01-01", "2026-01-02", "newjoy,omurio", 2)
    assert "UPDATE dianxiaomi_order_import_batches SET status=%s" in calls[3][1]


def test_upsert_dianxiaomi_order_lines_serializes_json(monkeypatch):
    captured = {}

    class Cursor:
        rowcount = 1

        def execute(self, sql, args):
            captured["sql"] = sql
            captured["args"] = args

    class CursorContext(Cursor):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class Conn:
        def cursor(self):
            return CursorContext()

        def commit(self):
            captured["committed"] = True

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(oa, "get_conn", lambda: Conn())
    monkeypatch.setattr(
        "appcore.order_analytics.dianxiaomi.backfill_purchase_price_snapshot",
        lambda batch_id=None: {"affected": 2},
    )

    result = oa.upsert_dianxiaomi_order_lines(
        42,
        [{
            "site_code": "newjoy",
            "product_id": 7,
            "product_code": "demo",
            "shopify_product_id": "111",
            "dxm_package_id": "9001",
            "raw_order_json": {"id": "9001"},
            "raw_line_json": {"productId": "111"},
            "profit_json": {"profit": "1.23"},
        }],
    )

    assert result == {"affected": 1, "rows": 1, "purchase_price_snapshot_filled": 2}
    assert "INSERT INTO dianxiaomi_order_lines" in captured["sql"]
    assert "meta_business_date" in captured["sql"]
    assert any('"id": "9001"' in str(arg) for arg in captured["args"])
    assert captured["committed"] is True


def test_resolve_dianxiaomi_line_product_prefers_per_domain_shopify_id():
    """当同一个 product_code 在两个域名有不同 shopify ID 时，按订单域名匹配正确产品。"""
    scope = oa.DianxiaomiProductScope(
        by_shopify_id={
            "8552296546477": {
                "product_id": 1,
                "product_code": "shared-handle-rjc",
                "site_code": "newjoy",
                "shopifyid": "8552296546477",
            }
        },
        by_handle={},
        by_domain_shopify_id={
            ("newjoyloo.com", "8552296546477"): {
                "product_id": 1,
                "product_code": "shared-handle-rjc",
                "site_code": "newjoy",
                "shopifyid": "8552296546477",
            },
            ("omurio.com", "999888777666"): {
                "product_id": 2,
                "product_code": "shared-handle-rjc",
                "site_code": "omurio",
                "shopifyid": "999888777666",
            },
        },
        excluded_shopify_ids=set(),
        excluded_handles=set(),
        requested_site_codes={"newjoy", "omurio"},
    )

    # 订单来自 omurio.com，应匹配 omurio per-domain ID
    product = oa._resolve_dianxiaomi_line_product(
        {
            "productId": "999888777666",
            "productUrl": "https://omurio.com/products/shared-handle",
        },
        "999888777666",
        scope,
    )

    assert product is not None
    assert product["product_id"] == 2
    assert product["site_code"] == "omurio"
    assert product["shopifyid"] == "999888777666"


def test_resolve_dianxiaomi_line_product_falls_back_to_legacy_shopify_id():
    """无 per-domain 匹配时回退到旧版 by_shopify_id 匹配。"""
    scope = oa.DianxiaomiProductScope(
        by_shopify_id={
            "111222333": {
                "product_id": 3,
                "product_code": "legacy-only-rjc",
                "site_code": None,
                "shopifyid": "111222333",
            }
        },
        by_handle={},
        by_domain_shopify_id={},
        excluded_shopify_ids=set(),
        excluded_handles=set(),
        requested_site_codes={"newjoy", "omurio"},
    )

    product = oa._resolve_dianxiaomi_line_product(
        {"productId": "111222333", "productUrl": "https://newjoyloo.com/products/legacy-only"},
        "111222333",
        scope,
    )

    assert product is not None
    assert product["product_id"] == 3
    assert product["site_code"] == "newjoy"  # resolve_site 从订单 URL 推断


def test_build_dianxiaomi_product_scope_includes_per_domain_ids(monkeypatch):
    """build 阶段同时查询 media_product_shopify_ids 构建 by_domain_shopify_id。"""
    monkeypatch.setattr(
        oa,
        "query",
        lambda sql, args=(): (
            [
                {
                    "id": 1,
                    "product_code": "shared-handle-rjc",
                    "shopifyid": "8552296546477",
                    "product_link": "https://newjoyloo.com/products/shared-handle",
                    "localized_links_json": "{}",
                },
            ]
            if "media_products " in sql and "media_product_shopify_ids" not in sql
            else [
                {
                    "id": 1,
                    "product_code": "shared-handle-rjc",
                    "domain": "newjoyloo.com",
                    "shopify_product_id": "8552296546477",
                },
                {
                    "id": 2,
                    "product_code": "shared-handle-rjc",
                    "domain": "omurio.com",
                    "shopify_product_id": "999888777666",
                },
            ]
        ),
    )

    scope = oa.build_dianxiaomi_product_scope(["newjoy", "omurio"])

    assert ("newjoyloo.com", "8552296546477") in scope.by_domain_shopify_id
    assert ("omurio.com", "999888777666") in scope.by_domain_shopify_id
    assert scope.by_domain_shopify_id[("omurio.com", "999888777666")]["site_code"] == "omurio"
    # 旧版 by_shopify_id 不受影响
    assert "8552296546477" in scope.by_shopify_id
