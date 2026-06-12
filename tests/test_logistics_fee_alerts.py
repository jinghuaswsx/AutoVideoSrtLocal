from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal


def _rows():
    return [
        {
            "business_date": date(2026, 6, 12),
            "paid_at": datetime(2026, 6, 12, 9, 30),
            "site_code": "newjoy",
            "dxm_order_line_id": 1,
            "dxm_package_id": "PKG-A",
            "dxm_order_id": "DX-A",
            "package_number": "PN-A",
            "product_id": 101,
            "product_code": "high-ship-rjc",
            "product_name": "高运费产品",
            "sku": "SKU-A",
            "quantity": 1,
            "revenue_usd": Decimal("20.00"),
            "shipping_cost_usd": Decimal("5.00"),
            "status": "ok",
            "missing_fields": "[]",
            "cost_basis": '{"shipping_cost_source":"order_logistic_fee","estimated_fields":[]}',
        },
        {
            "business_date": date(2026, 6, 12),
            "paid_at": datetime(2026, 6, 12, 10, 0),
            "site_code": "newjoy",
            "dxm_order_line_id": 2,
            "dxm_package_id": "PKG-B",
            "dxm_order_id": "DX-B",
            "package_number": "PN-B",
            "product_id": 101,
            "product_code": "high-ship-rjc",
            "product_name": "高运费产品",
            "sku": "SKU-B",
            "quantity": 2,
            "revenue_usd": Decimal("40.00"),
            "shipping_cost_usd": Decimal("12.00"),
            "status": "ok",
            "missing_fields": "[]",
            "cost_basis": '{"shipping_cost_source":"product_actual","estimated_fields":[]}',
        },
        {
            "business_date": date(2026, 6, 12),
            "paid_at": datetime(2026, 6, 12, 11, 0),
            "site_code": "omurio",
            "dxm_order_line_id": 3,
            "dxm_package_id": "PKG-C",
            "dxm_order_id": "DX-C",
            "package_number": "PN-C",
            "product_id": 202,
            "product_code": "estimated-ship-rjc",
            "product_name": "估算物流产品",
            "sku": "SKU-C",
            "quantity": 1,
            "revenue_usd": Decimal("10.00"),
            "shipping_cost_usd": Decimal("2.00"),
            "status": "incomplete",
            "missing_fields": '["shipping_cost"]',
            "cost_basis": '{"shipping_cost_source":null,"estimated_fields":["shipping_cost"],"shipping_fallback_ratio":0.2}',
        },
        {
            "business_date": date(2026, 6, 12),
            "paid_at": datetime(2026, 6, 12, 12, 0),
            "site_code": "newjoy",
            "dxm_order_line_id": 4,
            "dxm_package_id": "PKG-D",
            "dxm_order_id": "DX-D",
            "package_number": "PN-D",
            "product_id": 303,
            "product_code": "normal-rjc",
            "product_name": "正常产品",
            "sku": "SKU-D",
            "quantity": 1,
            "revenue_usd": Decimal("50.00"),
            "shipping_cost_usd": Decimal("5.00"),
            "status": "ok",
            "missing_fields": "[]",
            "cost_basis": '{"shipping_cost_source":"product_estimated","estimated_fields":[]}',
        },
    ]


def test_list_logistics_fee_alert_products_groups_over_threshold():
    from appcore.order_analytics import logistics_fee_alerts

    result = logistics_fee_alerts.list_product_alerts(
        start_date="2026-06-12",
        end_date="2026-06-12",
        threshold_pct=20,
        query_fn=lambda _sql, _args=(): _rows(),
    )

    assert result["summary"]["product_count"] == 2
    assert result["summary"]["alert_line_count"] == 3
    first = result["products"][0]
    assert first["product_id"] == 101
    assert first["alert_line_count"] == 2
    assert first["package_count"] == 2
    assert first["revenue_usd"] == 60.0
    assert first["shipping_cost_usd"] == 17.0
    assert first["shipping_ratio_pct"] == 28.33
    assert first["max_shipping_ratio_pct"] == 30.0
    assert first["shipping_source_counts"] == {
        "order_logistic_fee": 1,
        "product_actual": 1,
    }
    second = result["products"][1]
    assert second["product_id"] == 202
    assert second["shipping_source_counts"] == {"estimated": 1}


def test_list_logistics_fee_alert_products_respects_higher_threshold():
    from appcore.order_analytics import logistics_fee_alerts

    result = logistics_fee_alerts.list_product_alerts(
        start_date="2026-06-12",
        end_date="2026-06-12",
        threshold_pct=30,
        query_fn=lambda _sql, _args=(): _rows(),
    )

    assert [row["product_id"] for row in result["products"]] == [101]
    assert result["products"][0]["alert_line_count"] == 1
    assert result["products"][0]["shipping_ratio_pct"] == 30.0


def test_list_logistics_fee_alert_products_treats_legacy_packet_cost_as_estimated():
    from appcore.order_analytics import logistics_fee_alerts

    row = dict(_rows()[0])
    row["product_id"] = 404
    row["missing_fields"] = '["packet_cost"]'
    row["cost_basis"] = "{}"

    result = logistics_fee_alerts.list_product_alerts(
        start_date="2026-06-12",
        end_date="2026-06-12",
        threshold_pct=20,
        query_fn=lambda _sql, _args=(): [row],
    )

    assert result["products"][0]["shipping_source_counts"] == {"estimated": 1}


def test_list_logistics_fee_alert_order_details_filters_product():
    from appcore.order_analytics import logistics_fee_alerts

    result = logistics_fee_alerts.list_product_order_alerts(
        product_id=101,
        start_date="2026-06-12",
        end_date="2026-06-12",
        threshold_pct=20,
        query_fn=lambda _sql, _args=(): _rows(),
    )

    assert result["product"]["product_id"] == 101
    assert result["summary"]["alert_line_count"] == 2
    assert [row["dxm_order_line_id"] for row in result["orders"]] == [2, 1]
    assert result["orders"][0]["shipping_ratio_pct"] == 30.0


def test_logistics_fee_alert_routes_forward_params(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_products(**kwargs):
        captured["products"] = kwargs
        return {
            "period": {"start_date": "2026-06-11", "end_date": "2026-06-12"},
            "threshold_pct": 30.0,
            "summary": {"product_count": 0, "alert_line_count": 0},
            "products": [],
            "page": {"page": 1, "page_size": 100, "total": 0, "pages": 0},
        }

    monkeypatch.setattr(
        "web.routes.order_analytics.oa.list_logistics_fee_alert_products",
        fake_products,
    )

    response = authed_client_no_db.get(
        "/order-analytics/logistics-alert/data?start_date=2026-06-11&end_date=2026-06-12&threshold_pct=30&page=2&page_size=50"
    )

    assert response.status_code == 200
    assert captured["products"]["start_date"] == "2026-06-11"
    assert captured["products"]["end_date"] == "2026-06-12"
    assert captured["products"]["threshold_pct"] == 30.0
    assert captured["products"]["page"] == 2
    assert captured["products"]["page_size"] == 50


def test_logistics_fee_alert_page_renders(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics/logistics-alert")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="panelLogisticsAlert"' in html
    assert "物流费预警" in html


def test_logistics_fee_alert_detail_route_forwards_product(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_details(**kwargs):
        captured["details"] = kwargs
        return {
            "period": {"start_date": "2026-06-11", "end_date": "2026-06-12"},
            "threshold_pct": 20.0,
            "product": {"product_id": 101},
            "summary": {"alert_line_count": 0},
            "orders": [],
            "page": {"page": 1, "page_size": 100, "total": 0, "pages": 0},
        }

    monkeypatch.setattr(
        "web.routes.order_analytics.oa.list_logistics_fee_alert_order_details",
        fake_details,
    )

    response = authed_client_no_db.get(
        "/order-analytics/logistics-alert/products/101/data?start_date=2026-06-11&end_date=2026-06-12&threshold_pct=20"
    )

    assert response.status_code == 200
    assert captured["details"]["product_id"] == 101
    assert captured["details"]["threshold_pct"] == 20.0


def test_logistics_fee_alert_product_page_renders(authed_client_no_db):
    response = authed_client_no_db.get(
        "/order-analytics/logistics-alert/products/101?start_date=2026-06-11&end_date=2026-06-12&threshold_pct=20"
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="logisticsAlertDetailBody"' in html
    assert "返回物流费预警" in html
