from __future__ import annotations

from datetime import date, datetime

from appcore import order_analytics as oa
from appcore.order_analytics.realtime import (
    _build_order_profit_status_label,
    _derive_order_profit_status,
    _derive_refund_status,
    _get_realtime_order_profit_details,
    _is_refund_like_state,
    _resolve_refund_deduction,
)


def test_resolve_refund_deduction_uses_actual_partial_refund():
    assert _resolve_refund_deduction(
        total_revenue=100.0,
        refund_amount_usd=12.5,
        order_state="paid",
    ) == 12.5


def test_resolve_refund_deduction_caps_refund_to_total_revenue():
    assert _resolve_refund_deduction(
        total_revenue=100.0,
        refund_amount_usd=150.0,
        order_state="paid",
    ) == 100.0


def test_resolve_refund_deduction_uses_full_revenue_for_refund_state_without_amount():
    assert _resolve_refund_deduction(
        total_revenue=88.0,
        refund_amount_usd=0,
        order_state="refunded",
    ) == 88.0


def test_refund_state_detects_english_and_chinese_values():
    assert _is_refund_like_state("refund success") is True
    assert _is_refund_like_state("cancelled") is True
    assert _is_refund_like_state("已退款") is True
    assert _is_refund_like_state("已取消") is True
    assert _is_refund_like_state("paid") is False


def test_derive_refund_status():
    assert _derive_refund_status(total_revenue=100, refund_deduction=0) == "none"
    assert _derive_refund_status(total_revenue=100, refund_deduction=20) == "partial_refund"
    assert _derive_refund_status(total_revenue=100, refund_deduction=100) == "full_refund"


def test_derive_order_profit_status():
    assert _derive_order_profit_status(line_count=2, ok_count=2, incomplete_count=0) == "ok"
    assert _derive_order_profit_status(line_count=2, ok_count=1, incomplete_count=1) == "partially_complete"
    assert _derive_order_profit_status(line_count=2, ok_count=0, incomplete_count=2) == "incomplete"
    assert _derive_order_profit_status(line_count=0, ok_count=0, incomplete_count=0) == "not_computed"


def test_build_order_profit_status_label():
    assert _build_order_profit_status_label("ok", "none") == "完整"
    assert _build_order_profit_status_label("partially_complete", "none") == "部分完整"
    assert _build_order_profit_status_label("incomplete", "none") == "不完整"
    assert _build_order_profit_status_label("not_computed", "none") == "未核算"
    assert _build_order_profit_status_label("ok", "full_refund") == "完整 / 全额退款"
    assert _build_order_profit_status_label("ok", "partial_refund") == "完整 / 部分退款"


def test_get_realtime_order_profit_details_aggregates_costs_and_refunds(monkeypatch):
    target = date(2026, 5, 6)
    day_start = datetime(2026, 5, 5, 16, 0)
    data_until = datetime(2026, 5, 6, 12, 0)
    calls = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        assert "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id" in sql
        assert "MAX(COALESCE(d.refund_amount_usd, 0)) AS refund_amount_usd" in sql
        assert args == (target, data_until)
        return [
            {
                "site_code": "newjoy",
                "dxm_package_id": "PKG-DE",
                "dxm_order_id": "DXM-DE",
                "package_number": "PN-DE",
                "order_state": "paid",
                "buyer_country": "DE",
                "buyer_country_name": "Germany",
                "order_time": datetime(2026, 5, 6, 10, 30),
                "line_count": 2,
                "profit_line_count": 1,
                "profit_ok_count": 1,
                "profit_incomplete_count": 0,
                "units": 3,
                "product_revenue": 100.0,
                "shipping_revenue": 10.0,
                "total_revenue": 110.0,
                "refund_amount_usd": 12.0,
                "purchase_cost": 30.0,
                "logistics_cost": 8.0,
                "ad_cost": 11.0,
                "stored_shopify_fee_total": 5.75,
                "skus": "SKU-A / SKU-B",
                "product_names": "Product A / Product B",
            },
            {
                "site_code": "omurio",
                "dxm_package_id": "PKG-US",
                "dxm_order_id": "DXM-US",
                "package_number": "PN-US",
                "order_state": "refunded",
                "buyer_country": "US",
                "buyer_country_name": "United States",
                "order_time": datetime(2026, 5, 6, 9, 15),
                "line_count": 1,
                "profit_line_count": 1,
                "profit_ok_count": 1,
                "profit_incomplete_count": 0,
                "units": 1,
                "product_revenue": 50.0,
                "shipping_revenue": 5.0,
                "total_revenue": 55.0,
                "refund_amount_usd": 0.0,
                "purchase_cost": 10.0,
                "logistics_cost": 5.0,
                "ad_cost": 2.0,
                "stored_shopify_fee_total": 1.68,
                "skus": "SKU-US",
                "product_names": "Product US",
            },
        ]

    monkeypatch.setattr(oa, "query", fake_query)

    details = _get_realtime_order_profit_details(target, day_start, data_until)

    assert len(calls) == 1
    de_order = details[0]
    assert de_order["shopify_platform_fee_usd"] == 3.05
    assert de_order["international_card_fee_usd"] == 1.1
    assert de_order["currency_conversion_fee_usd"] == 1.65
    assert de_order["shopify_fee_total_usd"] == 5.8
    assert de_order["stored_shopify_fee_total_usd"] == 5.75
    assert de_order["refund_deduction_usd"] == 12.0
    assert de_order["ad_cost_usd"] == 11.0
    assert de_order["order_profit_usd"] == 43.2
    assert de_order["profit_status"] == "ok"
    assert de_order["refund_status"] == "partial_refund"
    assert de_order["status_label"] == "完整 / 部分退款"
    assert de_order["shopify_tier"] == "D"
    assert de_order["presentment_currency"] == "EUR"

    us_order = details[1]
    assert us_order["refund_deduction_usd"] == 55.0
    assert us_order["refund_status"] == "full_refund"
    assert us_order["profit_status"] == "ok"
    assert us_order["order_profit_usd"] == -18.68
