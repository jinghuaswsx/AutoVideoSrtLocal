from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from appcore import order_analytics as oa


def test_compute_pct_change_normal():
    assert oa._compute_pct_change(120, 100) == 20.0
    assert oa._compute_pct_change(80, 100) == -20.0


def test_compute_pct_change_both_zero():
    assert oa._compute_pct_change(0, 0) == 0.0


def test_compute_pct_change_prev_zero_now_positive():
    # 无法计算百分比时返回 None（前端显示 "新增" 或 "-"）
    assert oa._compute_pct_change(50, 0) is None


def test_compute_pct_change_now_zero_prev_positive():
    assert oa._compute_pct_change(0, 100) == -100.0


def test_compute_pct_change_handles_none_inputs():
    assert oa._compute_pct_change(None, 100) == -100.0
    assert oa._compute_pct_change(100, None) is None
    assert oa._compute_pct_change(None, None) == 0.0


def test_resolve_period_range_full_past_month():
    start, end = oa._resolve_period_range("month", year=2026, month=3, today=date(2026, 4, 26))
    assert start == date(2026, 3, 1)
    assert end == date(2026, 3, 31)


def test_resolve_period_range_current_month_truncates_to_yesterday():
    start, end = oa._resolve_period_range("month", year=2026, month=4, today=date(2026, 4, 26))
    assert start == date(2026, 4, 1)
    assert end == date(2026, 4, 25)  # 昨日


def test_resolve_period_range_iso_week():
    # 2026 ISO week 17 = 2026-04-20 (Mon) ~ 2026-04-26 (Sun)
    start, end = oa._resolve_period_range("week", year=2026, week=17, today=date(2026, 5, 1))
    assert start == date(2026, 4, 20)
    assert end == date(2026, 4, 26)


def test_resolve_period_range_current_week_truncates_to_yesterday():
    start, end = oa._resolve_period_range("week", year=2026, week=17, today=date(2026, 4, 23))
    assert start == date(2026, 4, 20)
    assert end == date(2026, 4, 22)


def test_resolve_period_range_day():
    start, end = oa._resolve_period_range("day", date_str="2026-04-25", today=date(2026, 4, 26))
    assert start == date(2026, 4, 25)
    assert end == date(2026, 4, 25)


def test_resolve_period_range_invalid_period_raises():
    with pytest.raises(ValueError, match="invalid period"):
        oa._resolve_period_range("year", year=2026, today=date(2026, 4, 26))


def test_resolve_compare_range_full_month_to_prev_full_month():
    start, end = oa._resolve_compare_range(
        date(2026, 3, 1), date(2026, 3, 31), "month"
    )
    assert start == date(2026, 2, 1)
    assert end == date(2026, 2, 28)


def test_resolve_compare_range_partial_month_to_prev_same_day():
    # 当月 4-1 ~ 4-25（截至昨日）→ 上月 3-1 ~ 3-25
    start, end = oa._resolve_compare_range(
        date(2026, 4, 1), date(2026, 4, 25), "month"
    )
    assert start == date(2026, 3, 1)
    assert end == date(2026, 3, 25)


def test_resolve_compare_range_week_to_prev_week():
    start, end = oa._resolve_compare_range(
        date(2026, 4, 20), date(2026, 4, 26), "week"
    )
    assert start == date(2026, 4, 13)
    assert end == date(2026, 4, 19)


def test_resolve_compare_range_partial_week_to_same_length_prev_week():
    # 当周 4-20 ~ 4-22（截至周三）→ 上周 4-13 ~ 4-15
    start, end = oa._resolve_compare_range(
        date(2026, 4, 20), date(2026, 4, 22), "week"
    )
    assert start == date(2026, 4, 13)
    assert end == date(2026, 4, 15)


def test_resolve_compare_range_day_to_prev_day():
    start, end = oa._resolve_compare_range(date(2026, 4, 25), date(2026, 4, 25), "day")
    assert start == date(2026, 4, 24)
    assert end == date(2026, 4, 24)


def test_resolve_compare_range_month_clamps_start_day():
    """决策回归：start.day=31 → 上月若只有 28/30 天必须 clamp，不能 ValueError。"""
    # 3-31 → 2 月只有 28 天 → start clamp 到 2-28
    start, end = oa._resolve_compare_range(date(2026, 3, 31), date(2026, 3, 31), "month")
    assert start == date(2026, 2, 28)
    assert end == date(2026, 2, 28)
    # 5-31 → 4 月只有 30 天 → start clamp 到 4-30
    start, end = oa._resolve_compare_range(date(2026, 5, 31), date(2026, 5, 31), "month")
    assert start == date(2026, 4, 30)
    assert end == date(2026, 4, 30)


def test_aggregate_orders_by_product_returns_dict_keyed_by_product_id(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {"product_id": 42, "orders": 10, "units": 12, "revenue": 240.5},
            {"product_id": 99, "orders": 3, "units": 3, "revenue": 60.0},
        ]

    monkeypatch.setattr(oa, "query", fake_query)
    result = oa._aggregate_orders_by_product(date(2026, 4, 1), date(2026, 4, 25), country=None)

    assert 42 in result and 99 in result
    assert result[42]["orders"] == 10
    assert result[42]["units"] == 12
    assert result[42]["revenue"] == 240.5
    assert "created_at_order >= %s" in captured["sql"]
    assert "created_at_order < DATE_ADD(" in captured["sql"]
    assert "COALESCE(lineitem_price" in captured["sql"]
    assert "billing_country" not in captured["sql"]  # 无国家筛选时不带
    assert captured["args"] == (date(2026, 4, 1), date(2026, 4, 25))


def test_aggregate_orders_by_product_with_country_filter(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    oa._aggregate_orders_by_product(date(2026, 4, 1), date(2026, 4, 25), country="DE")

    assert "billing_country" in captured["sql"]
    assert captured["args"] == (date(2026, 4, 1), date(2026, 4, 25), "DE")


def test_aggregate_orders_by_product_skips_null_product_id(monkeypatch):
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"product_id": None, "orders": 5, "units": 5, "revenue": 100.0},
        {"product_id": 42, "orders": 2, "units": 2, "revenue": 40.0},
    ])
    result = oa._aggregate_orders_by_product(date(2026, 4, 1), date(2026, 4, 25), country=None)
    assert list(result.keys()) == [42]


def test_aggregate_ads_by_product_full_coverage_only(monkeypatch):
    """决策 #7：只纳入完全被 [start, end] 覆盖的广告报表。"""
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {"product_id": 42, "spend": 1200.5, "purchases": 130, "purchase_value": 4500.0},
        ]

    monkeypatch.setattr(oa, "query", fake_query)
    result = oa._aggregate_ads_by_product(date(2026, 4, 1), date(2026, 4, 30))

    # SQL 必须用 'report_start_date >= start AND report_end_date <= end'（完全覆盖语义）
    assert "report_start_date >= %s" in captured["sql"]
    assert "report_end_date <= %s" in captured["sql"]
    assert captured["args"] == (date(2026, 4, 1), date(2026, 4, 30))
    assert result[42]["spend"] == 1200.5
    assert result[42]["purchases"] == 130
    assert result[42]["purchase_value"] == 4500.0


def test_aggregate_ads_by_product_skips_null_product_id(monkeypatch):
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"product_id": None, "spend": 100.0, "purchases": 5, "purchase_value": 0.0},
        {"product_id": 42, "spend": 200.0, "purchases": 10, "purchase_value": 600.0},
    ])
    result = oa._aggregate_ads_by_product(date(2026, 4, 1), date(2026, 4, 30))
    assert list(result.keys()) == [42]


def test_aggregate_ads_by_product_decimals_to_floats(monkeypatch):
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"product_id": 42, "spend": Decimal("1200.50"), "purchases": Decimal("130"),
         "purchase_value": Decimal("4500.00")},
    ])
    result = oa._aggregate_ads_by_product(date(2026, 4, 1), date(2026, 4, 30))
    assert result[42]["spend"] == 1200.5
    assert isinstance(result[42]["spend"], float)
    assert result[42]["purchases"] == 130


def test_count_media_items_by_product_groups_by_lang(monkeypatch):
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"product_id": 42, "lang": "en", "n": 1},
        {"product_id": 42, "lang": "de", "n": 2},
        {"product_id": 99, "lang": "en", "n": 1},
    ])
    result = oa._count_media_items_by_product()
    assert result[42] == {"en": 1, "de": 2}
    assert result[99] == {"en": 1}


def test_count_media_items_by_product_filters_deleted(monkeypatch):
    captured = {}
    def fake_query(sql, args=()):
        captured["sql"] = sql
        return []
    monkeypatch.setattr(oa, "query", fake_query)
    oa._count_media_items_by_product()
    assert "deleted_at IS NULL" in captured["sql"]


def test_join_and_compute_filters_zero_zero_products():
    """决策 #12: orders=0 + spend=0 的产品被剔除。"""
    products = {
        42: {"id": 42, "name": "Glow", "product_code": "glow-rjc"},
        99: {"id": 99, "name": "Other", "product_code": "other-rjc"},
        7:  {"id": 7,  "name": "Zero", "product_code": "zero"},
    }
    orders_now  = {42: {"orders": 10, "units": 12, "revenue": 200.0}}
    orders_prev = {42: {"orders": 8,  "units": 10, "revenue": 150.0}}
    ads_now     = {99: {"spend": 100.0, "purchases": 5, "purchase_value": 250.0}}
    ads_prev    = {99: {"spend": 80.0,  "purchases": 4, "purchase_value": 200.0}}
    items       = {42: {"en": 1}, 99: {"en": 1}}

    rows = oa._join_and_compute_dashboard_rows(
        products=products,
        orders_now=orders_now, orders_prev=orders_prev,
        ads_now=ads_now, ads_prev=ads_prev,
        items=items,
        ad_data_available=True,
    )

    pids = {r["product_id"] for r in rows}
    assert pids == {42, 99}  # 7 被剔除


def test_join_and_compute_roas_uses_shopify_revenue_over_spend():
    """决策 #13: ROAS = Shopify 收入 / Meta 花费。"""
    products = {42: {"id": 42, "name": "X", "product_code": "x"}}
    rows = oa._join_and_compute_dashboard_rows(
        products=products,
        orders_now={42: {"orders": 5, "units": 5, "revenue": 500.0}},
        orders_prev={42: {"orders": 3, "units": 3, "revenue": 300.0}},
        ads_now={42: {"spend": 100.0, "purchases": 10, "purchase_value": 999.0}},
        ads_prev={42: {"spend": 80.0,  "purchases": 8,  "purchase_value": 800.0}},
        items={42: {"en": 1}},
        ad_data_available=True,
    )
    assert rows[0]["roas"] == 5.0          # 500 / 100，不是 999 / 100
    assert rows[0]["roas_prev"] == 3.75    # 300 / 80


def test_join_and_compute_ad_unavailable_drops_ad_columns():
    products = {42: {"id": 42, "name": "X", "product_code": "x"}}
    rows = oa._join_and_compute_dashboard_rows(
        products=products,
        orders_now={42: {"orders": 5, "units": 5, "revenue": 500.0}},
        orders_prev={},
        ads_now={}, ads_prev={},
        items={42: {"en": 1}},
        ad_data_available=False,
    )
    assert rows[0]["ad_data_available"] is False
    assert rows[0]["spend"] is None
    assert rows[0]["roas"] is None


def test_get_dashboard_month_view_happy_path(monkeypatch):
    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 10, "units": 12, "revenue": 500.0}
    } if s == date(2026, 4, 1) else {
        42: {"orders": 8, "units": 10, "revenue": 400.0}
    })
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: {
        42: {"spend": 100.0, "purchases": 10, "purchase_value": 500.0}
    } if s == date(2026, 4, 1) else {
        42: {"spend": 80.0, "purchases": 8, "purchase_value": 400.0}
    })
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en": 1, "de": 2}})
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"id": 42, "name": "Glow Set", "product_code": "glow-rjc"}
    ])

    result = oa.get_dashboard(
        period="month", year=2026, month=4,
        today=date(2026, 4, 26), compare=True,
    )

    assert result["period"]["start"] == "2026-04-01"
    assert result["period"]["end"] == "2026-04-25"
    assert result["compare_period"]["start"] == "2026-03-01"
    assert result["compare_period"]["end"] == "2026-03-25"
    assert len(result["products"]) == 1
    assert result["products"][0]["product_id"] == 42
    assert result["products"][0]["roas"] == 5.0
    assert result["country"] is None
    assert result["summary"]["total_orders"] == 10
    assert result["summary"]["total_revenue"] == 500.0
    assert result["summary"]["total_spend"] == 100.0


def test_get_dashboard_day_view_no_ads_data(monkeypatch):
    """决策 #3: 日视图不显示广告。"""
    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 3, "units": 3, "revenue": 90.0}
    })
    # _aggregate_ads_by_product 不该被调用
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: pytest.fail("should not be called"))
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en": 1}})
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"id": 42, "name": "Glow", "product_code": "glow-rjc"}
    ])

    result = oa.get_dashboard(period="day", date_str="2026-04-25", today=date(2026, 4, 26))
    assert result["products"][0]["ad_data_available"] is False
    assert result["products"][0]["spend"] is None


def test_get_dashboard_country_filter_drops_ads(monkeypatch):
    """决策 #8 + meta_ad 表无 country 字段：国家筛选启用时广告整列降级。"""
    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 5, "units": 5, "revenue": 100.0}
    })
    ad_called = []
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: ad_called.append(1) or {})
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en": 1}})
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"id": 42, "name": "Glow", "product_code": "glow-rjc"}
    ])

    result = oa.get_dashboard(
        period="month", year=2026, month=4, country="DE",
        today=date(2026, 4, 26),
    )
    assert ad_called == []
    assert result["products"][0]["ad_data_available"] is False
    assert result["country"] == "DE"


def test_get_dashboard_search_filter(monkeypatch):
    """搜索按 product_code / name 过滤，仅传给 SQL，service 端不再做 in-memory filter。"""
    captured = {}
    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [{"id": 42, "name": "Glow", "product_code": "glow-rjc"}]

    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 5, "units": 5, "revenue": 100.0}
    })
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: {})
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en": 1}})
    monkeypatch.setattr(oa, "query", fake_query)

    oa.get_dashboard(period="month", year=2026, month=4, search="glow", today=date(2026, 4, 26))
    assert "name LIKE" in captured["sql"] or "product_code LIKE" in captured["sql"]
    assert "%glow%" in captured["args"]


def test_get_dashboard_default_sort_spend_desc_for_month(monkeypatch):
    monkeypatch.setattr(oa, "_aggregate_orders_by_product", lambda s, e, *, country=None: {
        42: {"orders": 10, "units": 10, "revenue": 200.0},
        99: {"orders": 5, "units": 5, "revenue": 100.0},
    })
    monkeypatch.setattr(oa, "_aggregate_ads_by_product", lambda s, e: {
        42: {"spend": 50.0, "purchases": 5, "purchase_value": 100.0},
        99: {"spend": 200.0, "purchases": 10, "purchase_value": 500.0},
    })
    monkeypatch.setattr(oa, "_count_media_items_by_product", lambda: {42: {"en":1}, 99: {"en":1}})
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"id": 42, "name": "A", "product_code": "a"},
        {"id": 99, "name": "B", "product_code": "b"},
    ])

    result = oa.get_dashboard(period="month", year=2026, month=4, today=date(2026, 4, 26))
    # 默认按花费降序 → 99 在前
    assert [p["product_id"] for p in result["products"]] == [99, 42]
