"""dianxiaomi 同步脚本字段映射修复测试。

修前现状：profit API 100% 返回 {"profit": "--"}，导致 logistic_fee /
amount_cny 等字段 100% NULL。修后：fallback 到 raw_order_json 顶层
字段（logisticFee 84% 命中），amount_cny 用 orderAmount × rmb_per_usd 折算。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from appcore.order_analytics.dianxiaomi import (
    _compute_amount_cny,
    _resolve_logistic_fee_cny,
)


def test_compute_amount_cny_prefers_profit_when_present():
    """profit API 真实返回 amountCNY 时优先用它。"""
    result = _compute_amount_cny(
        order={"orderAmount": 36.94},
        profit={"amountCNY": 250.00},
        rmb_per_usd=Decimal("6.83"),
    )
    assert result == 250.00


def test_compute_amount_cny_falls_back_to_order_usd_times_rate():
    """profit 缺失（"--" 或 None）→ orderAmount × rmb_per_usd。"""
    result = _compute_amount_cny(
        order={"orderAmount": 36.94},
        profit={"profit": "--"},  # 真实场景：profit API 返回 "--"
        rmb_per_usd=Decimal("6.83"),
    )
    # 36.94 × 6.83 = 252.30
    assert result == pytest.approx(252.30, abs=0.01)


def test_compute_amount_cny_falls_back_when_profit_amount_is_none():
    result = _compute_amount_cny(
        order={"orderAmount": 36.94},
        profit={"amountCNY": None},
        rmb_per_usd=Decimal("6.83"),
    )
    assert result == pytest.approx(252.30, abs=0.01)


def test_compute_amount_cny_returns_none_when_all_missing():
    """orderAmount 和 amountCNY 都缺 → None。"""
    result = _compute_amount_cny(
        order={},
        profit={},
        rmb_per_usd=Decimal("6.83"),
    )
    assert result is None


def test_compute_amount_cny_uses_custom_rate():
    """rmb_per_usd 可配置，admin 改了汇率应生效。"""
    result = _compute_amount_cny(
        order={"orderAmount": 100.0},
        profit={},
        rmb_per_usd=Decimal("7.00"),
    )
    assert result == pytest.approx(700.00, abs=0.01)


def test_resolve_logistic_fee_prefers_profit():
    result = _resolve_logistic_fee_cny(
        order={"logisticFee": 50.00},
        profit={"logisticFee": 61.606},  # 真实场景：profit API 返回更准
    )
    assert result == 61.61


def test_resolve_logistic_fee_falls_back_to_order_top_level():
    """profit API 没值时 fallback 到 raw_order_json 顶层 logisticFee。"""
    result = _resolve_logistic_fee_cny(
        order={"logisticFee": 61.606},
        profit={"profit": "--"},  # 真实场景
    )
    assert result == 61.61


def test_resolve_logistic_fee_returns_none_when_both_missing():
    """两边都缺（如 logisticFeeErr 类异常订单）→ None。"""
    result = _resolve_logistic_fee_cny(
        order={"logisticFeeErr": "请到物流方式设置运费模板"},
        profit={},
    )
    assert result is None


def test_resolve_logistic_fee_handles_zero_correctly():
    """0 是合法值（如包邮订单），不应被当作"缺失"误判。"""
    result = _resolve_logistic_fee_cny(
        order={},
        profit={"logisticFee": 0.0},
    )
    assert result == 0.0
