"""SKU 成本完备性 gate 测试。

完备性定义（业务方决策 Q11）：
  完备 = purchase_price 已填 + (packet_cost_actual 或 packet_cost_estimated 至少一个)

不完备的 SKU 在利润核算里 status='incomplete'，不出 profit 数字，
显式列出缺哪些字段，等业务方在素材管理 → ROAS 入口补录。
"""
from __future__ import annotations

import pytest

from appcore.order_analytics.cost_completeness import check_sku_cost_completeness


def test_complete_with_actual_packet_cost():
    """采购价 + 实际小包成本 → 完备，使用 actual。"""
    product = {
        "id": 316,
        "product_code": "sonic-lens-refresher-rjc",
        "purchase_price": 15.50,
        "packet_cost_actual": 20.50,
        "packet_cost_estimated": 18.00,
    }
    result = check_sku_cost_completeness(product)
    assert result["ok"] is True
    assert result["missing"] == []
    assert result["using_packet_cost"] == "actual"
    assert result["purchase_price"] == 15.50
    assert result["packet_cost"] == 20.50


def test_complete_with_only_estimated_packet_cost():
    """实际没填，预估有填 → 完备，fallback 到 estimated。"""
    product = {
        "id": 9,
        "purchase_price": 20.00,
        "packet_cost_actual": None,
        "packet_cost_estimated": 18.00,
    }
    result = check_sku_cost_completeness(product)
    assert result["ok"] is True
    assert result["missing"] == []
    assert result["using_packet_cost"] == "estimated"
    assert result["packet_cost"] == 18.00


def test_incomplete_missing_purchase_price():
    """缺采购价 → 不完备。"""
    product = {
        "id": 7,
        "purchase_price": None,
        "packet_cost_actual": 20.50,
    }
    result = check_sku_cost_completeness(product)
    assert result["ok"] is False
    assert "purchase_price" in result["missing"]
    assert result["using_packet_cost"] is None


def test_incomplete_missing_both_packet_costs():
    """两个 packet_cost 都缺 → 不完备。"""
    product = {
        "id": 100,
        "purchase_price": 15.50,
        "packet_cost_actual": None,
        "packet_cost_estimated": None,
    }
    result = check_sku_cost_completeness(product)
    assert result["ok"] is False
    assert "packet_cost" in result["missing"]
    assert result["using_packet_cost"] is None


def test_incomplete_all_missing():
    """全空 → 不完备，缺两项都列出。"""
    product = {"id": 200}
    result = check_sku_cost_completeness(product)
    assert result["ok"] is False
    assert "purchase_price" in result["missing"]
    assert "packet_cost" in result["missing"]


def test_zero_value_treated_as_missing():
    """0 视为未维护（采购价不可能为 0）。"""
    product = {
        "id": 300,
        "purchase_price": 0,
        "packet_cost_actual": 20.50,
    }
    result = check_sku_cost_completeness(product)
    assert result["ok"] is False
    assert "purchase_price" in result["missing"]


def test_negative_value_treated_as_missing():
    product = {
        "id": 301,
        "purchase_price": -5,
        "packet_cost_actual": 20.50,
    }
    result = check_sku_cost_completeness(product)
    assert result["ok"] is False
    assert "purchase_price" in result["missing"]


def test_string_numbers_accepted():
    """DB 取出来可能是 Decimal 或字符串，应能正确转换。"""
    product = {
        "id": 400,
        "purchase_price": "15.50",
        "packet_cost_actual": "20.50",
    }
    result = check_sku_cost_completeness(product)
    assert result["ok"] is True
    assert result["purchase_price"] == 15.50


def test_estimated_packet_zero_actual_present_uses_actual():
    """实际有效（>0），预估 0 → 用 actual（不会因为 estimated=0 误判）。"""
    product = {
        "id": 500,
        "purchase_price": 15.50,
        "packet_cost_actual": 20.50,
        "packet_cost_estimated": 0,
    }
    result = check_sku_cost_completeness(product)
    assert result["ok"] is True
    assert result["using_packet_cost"] == "actual"


def test_actual_zero_estimated_present_uses_estimated():
    """实际 0（不应发生，但保护性 fallback）→ 用 estimated。"""
    product = {
        "id": 501,
        "purchase_price": 15.50,
        "packet_cost_actual": 0,
        "packet_cost_estimated": 18.00,
    }
    result = check_sku_cost_completeness(product)
    assert result["ok"] is True
    assert result["using_packet_cost"] == "estimated"
