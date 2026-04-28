from __future__ import annotations

from datetime import datetime

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
    assert scope.excluded_shopify_ids == {"333"}


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
        excluded_shopify_ids=set(),
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
        excluded_shopify_ids=set(),
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


def test_normalize_dianxiaomi_order_keeps_requested_site_from_order_line_url():
    scope = oa.DianxiaomiProductScope(
        by_shopify_id={},
        excluded_shopify_ids=set(),
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


def test_normalize_dianxiaomi_order_skips_smartgearx_scope():
    scope = oa.DianxiaomiProductScope(
        by_shopify_id={},
        excluded_shopify_ids={"333"},
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

    assert result == {"affected": 1, "rows": 1}
    assert "INSERT INTO dianxiaomi_order_lines" in captured["sql"]
    assert any('"id": "9001"' in str(arg) for arg in captured["args"])
    assert captured["committed"] is True
