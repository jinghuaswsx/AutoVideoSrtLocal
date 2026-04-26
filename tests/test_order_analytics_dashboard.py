from __future__ import annotations

from datetime import date

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
