"""订单级利润聚合测试。"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from appcore import order_analytics as oa
from appcore.order_analytics.order_profit_aggregation import (
    _derive_order_status,
    _format_order_row,
    get_order_profit_detail,
    get_order_profit_list,
    get_order_profit_summary_for_window,
)


# -------- _derive_order_status --------

def test_derive_status_all_ok():
    assert _derive_order_status(ok_count=3, incomplete_count=0) == "ok"


def test_derive_status_all_incomplete():
    assert _derive_order_status(ok_count=0, incomplete_count=2) == "incomplete"


def test_derive_status_partially_complete():
    assert _derive_order_status(ok_count=2, incomplete_count=1) == "partially_complete"


def test_derive_status_no_data():
    assert _derive_order_status(ok_count=0, incomplete_count=0) == "no_data"


# -------- _format_order_row --------

def test_format_order_row_complete():
    row = {
        "dxm_package_id": "pkg123",
        "paid_at": datetime(2026, 5, 4, 10, 0, 0),
        "business_date": date(2026, 5, 4),
        "buyer_country": "DE",
        "platform": "shopify",
        "site_code": "newjoy",
        "line_count": 3,
        "ok_count": 3,
        "incomplete_count": 0,
        "line_amount_total": 60.0,
        "shipping_alloc_total": 6.99,
        "revenue_total": 66.99,
        "shopify_fee_total": 3.65,
        "ad_cost_total": 12.0,
        "purchase_total": 5.0,
        "shipping_cost_total": 8.0,
        "return_reserve_total": 0.67,
        "profit_total": 37.67,
    }
    out = _format_order_row(row)
    assert out["dxm_package_id"] == "pkg123"
    assert out["status"] == "ok"
    assert out["line_count"] == 3
    assert out["revenue_total_usd"] == 66.99
    assert out["profit_total_usd"] == 37.67


def test_format_order_row_partial():
    row = {
        "dxm_package_id": "pkg999",
        "ok_count": 1,
        "incomplete_count": 2,
        "line_count": 3,
    }
    out = _format_order_row(row)
    assert out["status"] == "partially_complete"


# -------- get_order_profit_list --------

def test_list_basic_query(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {
                "dxm_package_id": "pkg1",
                "paid_at": datetime(2026, 5, 4),
                "business_date": date(2026, 5, 4),
                "buyer_country": "US",
                "platform": "shopify",
                "site_code": "newjoy",
                "line_count": 1,
                "ok_count": 1,
                "incomplete_count": 0,
                "line_amount_total": 29.95,
                "shipping_alloc_total": 6.99,
                "revenue_total": 36.94,
                "shopify_fee_total": 1.65,
                "ad_cost_total": 5.0,
                "purchase_total": 8.8,
                "shipping_cost_total": 19.6,
                "return_reserve_total": 0.37,
                "profit_total": 1.52,
            }
        ]

    monkeypatch.setattr(oa, "query", fake_query)

    result = get_order_profit_list(
        date_from=date(2026, 5, 1), date_to=date(2026, 5, 4), limit=50, offset=0
    )
    assert len(result) == 1
    assert result[0]["status"] == "ok"
    assert "GROUP BY d.dxm_package_id" in captured["sql"]
    assert "ORDER BY paid_at DESC" in captured["sql"]
    assert captured["args"] == (date(2026, 5, 1), date(2026, 5, 4), 50, 0)


def test_list_with_status_ok_filter_uses_having(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    get_order_profit_list(
        date_from=date(2026, 5, 1), date_to=date(2026, 5, 4),
        status="ok", limit=10, offset=0,
    )
    assert "HAVING incomplete_count = 0" in captured["sql"]


def test_list_with_status_incomplete_filter(monkeypatch):
    captured = {}
    def fake(sql, args=()):
        captured["sql"] = sql
        return []
    monkeypatch.setattr(oa, "query", fake)
    get_order_profit_list(
        date_from=date(2026, 5, 1), date_to=date(2026, 5, 4),
        status="incomplete", limit=10, offset=0,
    )
    assert "HAVING ok_count = 0" in captured["sql"]


def test_list_with_status_partial_filter(monkeypatch):
    captured = {}
    def fake(sql, args=()):
        captured["sql"] = sql
        return []
    monkeypatch.setattr(oa, "query", fake)
    get_order_profit_list(
        date_from=date(2026, 5, 1), date_to=date(2026, 5, 4),
        status="partially_complete", limit=10, offset=0,
    )
    assert "HAVING ok_count > 0 AND incomplete_count > 0" in captured["sql"]


# -------- get_order_profit_detail --------

def test_detail_returns_summary_and_lines(monkeypatch):
    state = {"call": 0}

    def fake_query(sql, args=()):
        state["call"] += 1
        if state["call"] == 1:
            # 第一次：summary 查询
            return [{
                "dxm_package_id": "pkg1",
                "paid_at": datetime(2026, 5, 4),
                "business_date": date(2026, 5, 4),
                "buyer_country": "US",
                "platform": "shopify",
                "site_code": "newjoy",
                "line_count": 2,
                "ok_count": 2,
                "incomplete_count": 0,
                "line_amount_total": 49.9,
                "shipping_alloc_total": 5.0,
                "revenue_total": 54.9,
                "shopify_fee_total": 1.67,
                "ad_cost_total": 10.0,
                "purchase_total": 5.0,
                "shipping_cost_total": 10.0,
                "return_reserve_total": 0.55,
                "profit_total": 27.68,
            }]
        else:
            # 第二次：lines 查询
            return [
                {"id": 1, "product_code": "abc", "profit_usd": 13.84, "status": "ok"},
                {"id": 2, "product_code": "def", "profit_usd": 13.84, "status": "ok"},
            ]

    monkeypatch.setattr(oa, "query", fake_query)
    result = get_order_profit_detail("pkg1")
    assert result["dxm_package_id"] == "pkg1"
    assert result["status"] == "ok"
    assert result["line_count"] == 2
    assert len(result["lines"]) == 2


def test_detail_returns_none_for_unknown_package(monkeypatch):
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    assert get_order_profit_detail("unknown_pkg") is None


def test_detail_handles_empty_string():
    """空 dxm_package_id 直接返回 None，不查 DB。"""
    assert get_order_profit_detail("") is None
    assert get_order_profit_detail(None) is None


# -------- get_order_profit_summary_for_window --------

def test_summary_window_returns_buckets(monkeypatch):
    def fake_query_one(sql, args=()):
        if "FROM dianxiaomi_order_lines d INNER JOIN order_profit_lines" in sql and "status_per_order" not in sql:
            return {
                "total_orders": 100,
                "ok_lines": 80,
                "incomplete_lines": 20,
                "revenue_total": 5000.0,
                "profit_total": 800.0,
            }
        if "status_per_order" in sql:
            return {"orders_ok": 70, "orders_incomplete": 25, "orders_partial": 5}
        return None

    monkeypatch.setattr(oa, "query_one", fake_query_one)
    result = get_order_profit_summary_for_window(
        date_from=date(2026, 5, 1), date_to=date(2026, 5, 4)
    )
    assert result["total_orders"] == 100
    assert result["orders_ok"] == 70
    assert result["orders_incomplete"] == 25
    assert result["orders_partial"] == 5
    assert result["profit_total_usd"] == 800.0
