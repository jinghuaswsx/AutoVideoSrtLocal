from __future__ import annotations

from appcore.order_analytics.realtime import (
    _build_order_profit_status_label,
    _derive_order_profit_status,
    _derive_refund_status,
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
