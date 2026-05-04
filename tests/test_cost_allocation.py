"""广告费分摊 + 运费摊到 SKU 行的测试。

业务规则：
  - 广告费按 units 比例分摊（业务方决策 Q8）
  - 运费按行 line_amount 比例分摊（订单内单 SKU 数量不一时合理）
"""
from __future__ import annotations

from datetime import date

import pytest

from appcore import order_analytics as oa
from appcore.order_analytics.cost_allocation import (
    allocate_ad_cost_to_line,
    allocate_shipping_to_line,
    get_sku_daily_ad_spend,
    get_sku_daily_units,
    get_unallocated_ad_spend,
)


# -------- allocate_ad_cost_to_line（按 units 比例）--------

def test_allocate_ad_basic_units_proportional():
    """SKU X 当日 spend=$100，units=10，订单行 units=2 → 分摊 $20。"""
    result = allocate_ad_cost_to_line(
        line_units=2, daily_total_units=10, daily_spend_usd=100.0
    )
    assert result == pytest.approx(20.0)


def test_allocate_ad_zero_daily_units_returns_zero():
    """SKU 当日没销量（数据异常） → 0，不抛错。"""
    result = allocate_ad_cost_to_line(
        line_units=2, daily_total_units=0, daily_spend_usd=100.0
    )
    assert result == 0.0


def test_allocate_ad_zero_spend_returns_zero():
    """SKU 当日没投广告 → 0。"""
    result = allocate_ad_cost_to_line(
        line_units=5, daily_total_units=10, daily_spend_usd=0
    )
    assert result == 0.0


def test_allocate_ad_full_attribution_when_only_one_unit():
    """SKU 当日只卖一件 → 那一件分摊全部 spend。"""
    result = allocate_ad_cost_to_line(
        line_units=1, daily_total_units=1, daily_spend_usd=50.0
    )
    assert result == pytest.approx(50.0)


# -------- allocate_shipping_to_line（按行 line_amount 比例）--------

def test_allocate_shipping_single_line_gets_all():
    """订单只有 1 行 → 所有运费摊给它。"""
    result = allocate_shipping_to_line(
        line_amount=29.95,
        order_total_line_amount=29.95,
        order_shipping_usd=6.99,
    )
    assert result == pytest.approx(6.99)


def test_allocate_shipping_two_lines_proportional():
    """两个 SKU 行（29.95 + 19.95），总运费 $5 → 分摊 $2.99 / $2.00。"""
    line1 = allocate_shipping_to_line(
        line_amount=29.95, order_total_line_amount=49.90, order_shipping_usd=5.0
    )
    line2 = allocate_shipping_to_line(
        line_amount=19.95, order_total_line_amount=49.90, order_shipping_usd=5.0
    )
    assert line1 + line2 == pytest.approx(5.0, abs=0.02)
    assert line1 > line2  # 大行摊得更多


def test_allocate_shipping_zero_total_returns_zero():
    """订单 line_amount 总和为 0（异常） → 摊为 0，不除零。"""
    result = allocate_shipping_to_line(
        line_amount=29.95, order_total_line_amount=0, order_shipping_usd=6.99
    )
    assert result == 0.0


def test_allocate_shipping_zero_shipping_returns_zero():
    result = allocate_shipping_to_line(
        line_amount=29.95, order_total_line_amount=29.95, order_shipping_usd=0
    )
    assert result == 0.0


# -------- DB 查询函数（用 monkeypatch mock）--------

def test_get_sku_daily_units_aggregates_by_product_and_date(monkeypatch):
    captured = {}

    def fake_query_one(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return {"units": 12}

    monkeypatch.setattr(oa, "query_one", fake_query_one)

    result = get_sku_daily_units(product_id=316, business_date=date(2026, 5, 4))
    assert result == 12
    assert "FROM dianxiaomi_order_lines" in captured["sql"]
    assert "GROUP BY" not in captured["sql"]  # 单 product 单日只查一行
    assert captured["args"] == (316, date(2026, 5, 4))


def test_get_sku_daily_units_returns_zero_when_no_orders(monkeypatch):
    monkeypatch.setattr(oa, "query_one", lambda sql, args=(): None)
    assert get_sku_daily_units(product_id=999, business_date=date(2026, 5, 4)) == 0


def test_get_sku_daily_ad_spend_sums_meta_daily_metrics(monkeypatch):
    def fake_query_one(sql, args=()):
        return {"spend": 35.50}

    monkeypatch.setattr(oa, "query_one", fake_query_one)

    result = get_sku_daily_ad_spend(product_id=316, business_date=date(2026, 5, 4))
    assert result == pytest.approx(35.50)


def test_get_sku_daily_ad_spend_returns_zero_when_no_ads(monkeypatch):
    monkeypatch.setattr(oa, "query_one", lambda sql, args=(): {"spend": None})
    assert get_sku_daily_ad_spend(product_id=999, business_date=date(2026, 5, 4)) == 0.0


def test_get_unallocated_ad_spend_returns_unmatched_total(monkeypatch):
    """campaign 未匹配 product_id 的 spend 总和，按当日。"""
    def fake_query_one(sql, args=()):
        assert "product_id IS NULL" in sql
        return {"spend": 12.34}

    monkeypatch.setattr(oa, "query_one", fake_query_one)
    result = get_unallocated_ad_spend(business_date=date(2026, 5, 4))
    assert result == pytest.approx(12.34)
