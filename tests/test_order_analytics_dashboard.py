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
