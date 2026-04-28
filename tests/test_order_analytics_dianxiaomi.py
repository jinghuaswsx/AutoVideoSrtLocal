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
