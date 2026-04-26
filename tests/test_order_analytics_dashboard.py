from __future__ import annotations

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


from datetime import date


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
    import pytest
    with pytest.raises(ValueError, match="invalid period"):
        oa._resolve_period_range("year", year=2026, today=date(2026, 4, 26))
