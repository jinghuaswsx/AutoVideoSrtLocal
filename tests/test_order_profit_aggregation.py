"""订单级利润聚合测试。"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from appcore import order_analytics as oa
from appcore.order_analytics.order_profit_aggregation import (
    _derive_order_status,
    _format_order_row,
    get_order_profit_detail,
    get_order_profit_incomplete_products,
    get_order_profit_list,
    get_order_profit_loss_alerts,
    get_order_profit_summary_for_window,
    get_order_profit_status_summary,
    list_order_profit_lines,
    list_products_for_manual_match,
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


def test_list_filters_by_product_id(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    get_order_profit_list(
        date_from=date(2026, 5, 1),
        date_to=date(2026, 5, 4),
        product_id=123,
        limit=10,
        offset=5,
    )

    assert "AND p.product_id = %s" in captured["sql"]
    assert captured["args"] == (date(2026, 5, 1), date(2026, 5, 4), 123, 10, 5)


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


def test_detail_normalizes_json_columns_for_lines(monkeypatch):
    state = {"call": 0}

    def fake_query(sql, args=()):
        state["call"] += 1
        if state["call"] == 1:
            return [{
                "dxm_package_id": "pkg1",
                "paid_at": datetime(2026, 5, 4),
                "business_date": date(2026, 5, 4),
                "buyer_country": "US",
                "platform": "shopify",
                "site_code": "newjoy",
                "line_count": 1,
                "ok_count": 0,
                "incomplete_count": 1,
                "line_amount_total": 0,
                "shipping_alloc_total": 0,
                "revenue_total": 0,
                "shopify_fee_total": 0,
                "ad_cost_total": 0,
                "purchase_total": 0,
                "shipping_cost_total": 0,
                "return_reserve_total": 0,
                "profit_total": None,
            }]
        return [{
            "id": 1,
            "product_code": "abc",
            "profit_usd": None,
            "status": "incomplete",
            "missing_fields": '["purchase_price", "shipping_cost"]',
            "cost_basis": '{"shipping_cost_source": "missing"}',
        }]

    monkeypatch.setattr(oa, "query", fake_query)
    result = get_order_profit_detail("pkg1")

    assert result["lines"][0]["missing_fields"] == [
        "purchase_price",
        "shipping_cost",
    ]
    assert result["lines"][0]["cost_basis"] == {"shipping_cost_source": "missing"}


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


def test_summary_window_filters_by_product_id(monkeypatch):
    captured = []

    def fake_query_one(sql, args=()):
        captured.append((sql, args))
        if "status_per_order" not in sql:
            return {
                "total_orders": 10,
                "ok_lines": 8,
                "incomplete_lines": 2,
                "revenue_total": 500.0,
                "profit_total": 80.0,
            }
        return {"orders_ok": 7, "orders_incomplete": 2, "orders_partial": 1}

    monkeypatch.setattr(oa, "query_one", fake_query_one)

    get_order_profit_summary_for_window(
        date_from=date(2026, 5, 1),
        date_to=date(2026, 5, 4),
        product_id=123,
    )

    assert len(captured) == 2
    assert "AND p.product_id = %s" in captured[0][0]
    assert captured[0][1] == (date(2026, 5, 1), date(2026, 5, 4), 123)
    assert "AND p.product_id = %s" in captured[1][0]
    assert captured[1][1] == (date(2026, 5, 1), date(2026, 5, 4), 123)


def test_status_summary_aggregates_line_statuses_and_date_range_unallocated_spend(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        if "GROUP BY status" in sql:
            return [
                {
                    "status": "ok",
                    "n": 2,
                    "revenue": 100,
                    "profit": 25,
                    "shopify_fee": 3,
                    "ad_cost": 10,
                    "purchase": 40,
                    "shipping_cost": 7,
                    "return_reserve": 1,
                    "purchase_fallback_estimated": 0,
                    "purchase_fallback_estimated_lines": 0,
                    "shipping_product_estimated": 2,
                    "shipping_product_estimated_lines": 1,
                    "shipping_fallback_estimated": 0,
                    "shipping_fallback_estimated_lines": 0,
                },
                {
                    "status": "incomplete",
                    "n": 1,
                    "revenue": 50,
                    "profit": 8,
                    "shopify_fee": 2,
                    "ad_cost": 5,
                    "purchase": 5,
                    "shipping_cost": 10,
                    "return_reserve": 0.5,
                    "purchase_fallback_estimated": 5,
                    "purchase_fallback_estimated_lines": 1,
                    "shipping_product_estimated": 0,
                    "shipping_product_estimated_lines": 0,
                    "shipping_fallback_estimated": 10,
                    "shipping_fallback_estimated_lines": 1,
                },
                {"status": "unknown", "n": 9, "revenue": 999, "profit": 999},
            ]
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            captured["unallocated_sql"] = sql
            captured["unallocated_args"] = args
            return [{"unallocated_ad_spend_usd": 12.5}]
        raise AssertionError(f"unexpected query: {sql}")

    monkeypatch.setattr(oa, "query", fake_query)

    payload = get_order_profit_status_summary(
        date_from=date(2026, 5, 1),
        date_to=date(2026, 5, 3),
    )

    assert payload["date_from"] == "2026-05-01"
    assert payload["date_to"] == "2026-05-03"
    assert payload["summary"]["ok"]["lines"] == 2
    assert payload["summary"]["ok"]["profit"] == 25.0
    assert payload["summary"]["incomplete"]["lines"] == 1
    assert payload["unallocated_ad_spend_usd"] == 12.5
    assert payload["margin_pct"] == 25.0
    assert payload["overview"] == {
        "line_count": 3,
        "revenue_usd": 150.0,
        "confirmed_profit_usd": 25.0,
        "estimated_profit_usd": 8.0,
        "unallocated_ad_spend_usd": 12.5,
        "total_profit_usd": 20.5,
        "total_margin_pct": 13.67,
    }
    assert payload["estimate_marks"]["shopify_fee"] == {
        "estimated": True,
        "amount_usd": 5.0,
        "lines": 3,
        "label": "策略 C 估算",
    }
    assert payload["estimate_marks"]["purchase_fallback"]["amount_usd"] == 5.0
    assert payload["estimate_marks"]["purchase_fallback"]["lines"] == 1
    assert payload["estimate_marks"]["shipping_product_estimated"]["amount_usd"] == 2.0
    assert payload["estimate_marks"]["shipping_product_estimated"]["lines"] == 1
    assert payload["estimate_marks"]["shipping_fallback"]["amount_usd"] == 10.0
    assert payload["estimate_marks"]["shipping_fallback"]["lines"] == 1
    assert payload["estimate_marks"]["return_reserve"]["amount_usd"] == 1.5
    assert payload["estimate_marks"]["return_reserve"]["lines"] == 3
    assert payload["estimate_marks"]["unallocated_ad_spend"]["amount_usd"] == 12.5
    assert "product_id IS NULL" in captured["unallocated_sql"]
    assert captured["unallocated_args"] == (date(2026, 5, 1), date(2026, 5, 3))


def test_status_summary_queries_estimated_cost_sources(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        if "GROUP BY status" in sql:
            captured["summary_sql"] = sql
            return []
        if "FROM order_profit_runs" in sql:
            return []
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    get_order_profit_status_summary(
        date_from=date(2026, 5, 1),
        date_to=date(2026, 5, 3),
    )

    sql = captured["summary_sql"]
    assert "JSON_SEARCH(cost_basis, 'one', 'purchase'" in sql
    assert "JSON_SEARCH(cost_basis, 'one', 'shipping_cost'" in sql
    assert "JSON_UNQUOTE(JSON_EXTRACT(cost_basis, '$.shipping_cost_source')) = 'product_estimated'" in sql


def test_incomplete_products_list_is_scoped_and_deduplicated(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {
                "product_id": 7,
                "product_code": "ALPHA-001",
                "product_name": "阿尔法产品",
                "line_count": 3,
                "missing_fields_json": '["purchase_price","packet_cost"]',
                "last_seen": date(2026, 5, 3),
            },
            {
                "product_id": 8,
                "product_code": "BETA-002",
                "product_name": None,
                "line_count": 1,
                "missing_fields_json": None,
                "last_seen": date(2026, 5, 2),
            },
        ]

    monkeypatch.setattr(oa, "query", fake_query)

    products = get_order_profit_incomplete_products(
        date_from=date(2026, 5, 1),
        date_to=date(2026, 5, 3),
    )

    assert "p.status = 'incomplete'" in captured["sql"]
    assert "GROUP BY p.product_id" in captured["sql"]
    assert captured["args"] == (date(2026, 5, 1), date(2026, 5, 3))
    assert products == [
        {
            "product_id": 7,
            "product_code": "ALPHA-001",
            "product_name": "阿尔法产品",
            "display_label": "阿尔法产品 - ALPHA-001",
            "line_count": 3,
            "missing_fields": ["packet_cost", "purchase_price"],
            "last_seen": "2026-05-03",
            "medias_search_url": "/medias/?q=ALPHA-001",
        },
        {
            "product_id": 8,
            "product_code": "BETA-002",
            "product_name": "未命名产品",
            "display_label": "未命名产品 - BETA-002",
            "line_count": 1,
            "missing_fields": [],
            "last_seen": "2026-05-02",
            "medias_search_url": "/medias/?q=BETA-002",
        },
    ]


def test_status_summary_sql_estimates_missing_purchase_and_shipping(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        if "GROUP BY status" in sql:
            captured["sql"] = sql
        if "FROM order_profit_runs" in sql:
            return []
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    get_order_profit_status_summary(
        date_from=date(2026, 5, 1),
        date_to=date(2026, 5, 3),
    )

    sql = captured["sql"]
    assert "purchase_price" in sql
    assert "shipping_cost" in sql
    assert "packet_cost" in sql
    assert "revenue_usd, 0) * 0.10" in sql
    assert "revenue_usd, 0) * 0.20" in sql


def test_status_summary_returns_complete_profit_with_missing_cost_estimates(monkeypatch):
    def fake_query(sql, args=()):
        if "GROUP BY status" in sql:
            return [
                {
                    "status": "ok",
                    "n": 2,
                    "revenue": 100.0,
                    "profit": 25.0,
                    "shopify_fee": 3.0,
                    "ad_cost": 10.0,
                    "purchase": 30.0,
                    "shipping_cost": 20.0,
                    "return_reserve": 1.0,
                    "purchase_actual": 30.0,
                    "purchase_estimate": 0.0,
                    "purchase_with_estimate": 30.0,
                    "shipping_cost_actual": 20.0,
                    "shipping_cost_estimate": 0.0,
                    "shipping_cost_with_estimate": 20.0,
                    "profit_with_estimate": 25.0,
                },
                {
                    "status": "incomplete",
                    "n": 1,
                    "revenue": 200.0,
                    "profit": None,
                    "shopify_fee": 5.0,
                    "ad_cost": 20.0,
                    "purchase": 0.0,
                    "shipping_cost": 0.0,
                    "return_reserve": 2.0,
                    "purchase_actual": 0.0,
                    "purchase_estimate": 20.0,
                    "purchase_with_estimate": 20.0,
                    "shipping_cost_actual": 0.0,
                    "shipping_cost_estimate": 40.0,
                    "shipping_cost_with_estimate": 40.0,
                    "profit_with_estimate": 113.0,
                },
            ]
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return [{"unallocated_ad_spend_usd": 0}]
        raise AssertionError(f"unexpected query: {sql}")

    monkeypatch.setattr(oa, "query", fake_query)

    payload = get_order_profit_status_summary(
        date_from=date(2026, 5, 1),
        date_to=date(2026, 5, 3),
    )

    assert payload["total_revenue_usd"] == 300.0
    assert payload["known_revenue_usd"] == 100.0
    assert payload["unaccounted_revenue_usd"] == 200.0
    assert payload["estimated"]["purchase_usd"] == 20.0
    assert payload["estimated"]["shipping_cost_usd"] == 40.0
    assert payload["estimated"]["total_cost_usd"] == 60.0
    assert payload["estimated"]["profit_usd"] == 113.0
    assert payload["profit_with_estimate_usd"] == 138.0
    assert payload["profit_with_estimate_margin_pct"] == 46.0
    assert payload["purchase_cost_with_estimate_usd"] == 50.0
    assert payload["shipping_cost_with_estimate_usd"] == 60.0


def test_list_order_profit_lines_queries_by_filter(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [{"id": 1, "status": "ok"}]

    monkeypatch.setattr(oa, "query", fake_query)

    rows = list_order_profit_lines(
        date_from=date(2026, 5, 1),
        date_to=date(2026, 5, 2),
        status="ok",
        limit=20,
        offset=5,
    )

    assert rows == [{"id": 1, "status": "ok"}]
    assert "FROM order_profit_lines" in captured["sql"]
    assert "status=%s" in captured["sql"]
    assert captured["args"] == (date(2026, 5, 1), date(2026, 5, 2), "ok", 20, 5)


def test_loss_alerts_sum_negative_profit(monkeypatch):
    def fake_query(sql, args=()):
        assert "profit_usd < 0" in sql
        assert args == (date(2026, 5, 1), date(2026, 5, 2), 10)
        return [
            {"product_id": 1, "profit_usd": -2.25},
            {"product_id": 2, "profit_usd": -1.25},
        ]

    monkeypatch.setattr(oa, "query", fake_query)

    payload = get_order_profit_loss_alerts(
        date_from=date(2026, 5, 1),
        date_to=date(2026, 5, 2),
        limit=10,
    )

    assert payload["loss_count"] == 2
    assert payload["total_loss_usd"] == -3.5


def test_list_products_for_manual_match_queries_active_products(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [{"id": 1, "product_code": "alpha", "name": "Alpha"}]

    monkeypatch.setattr(oa, "query", fake_query)

    rows = list_products_for_manual_match()

    assert rows == [{"id": 1, "product_code": "alpha", "name": "Alpha"}]
    assert "FROM media_products" in captured["sql"]
    assert "deleted_at IS NULL" in captured["sql"]
    assert captured["args"] == ()
