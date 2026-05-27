"""广告费分摊（按 units）+ 运费摊到 SKU 行 + 未匹配广告费查询。

业务规则：
  - 广告费按 units 比例分摊到 SKU 行（业务方决策 Q8）
  - 运费按 line_amount 比例分摊（订单内多 SKU 时大行摊更多）
  - 未匹配 product_id 的 campaign 广告费单列（业务方决策 Q9）
"""
from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from typing import Any


# DB facade（同 dashboard.py 模式）
def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def query_one(*args, **kwargs):
    return _facade().query_one(*args, **kwargs)


# ---------------------------------------------------------------------------
# 纯算法：广告费 / 运费摊到行
# ---------------------------------------------------------------------------

def allocate_ad_cost_to_line(
    *,
    line_units: int,
    daily_total_units: int,
    daily_spend_usd: float,
) -> float:
    """按 units 比例把当日 SKU 总广告费摊到订单行。

    分摊广告费 = daily_spend × (line_units / daily_total_units)

    防御：daily_total_units=0（数据异常）或 daily_spend=0 → 0。
    """
    if daily_total_units <= 0 or daily_spend_usd <= 0:
        return 0.0
    if line_units <= 0:
        return 0.0
    return float(
        Decimal(str(daily_spend_usd)) * Decimal(line_units) / Decimal(daily_total_units)
    )


def allocate_shipping_to_line(
    *,
    line_amount: float,
    order_total_line_amount: float,
    order_shipping_usd: float,
) -> float:
    """按 line_amount 比例把订单运费摊到 SKU 行。

    分摊运费 = order_shipping × (line_amount / order_total_line_amount)

    防御：除零返回 0；运费=0 直接 0。
    """
    if order_total_line_amount <= 0 or order_shipping_usd <= 0:
        return 0.0
    if line_amount <= 0:
        return 0.0
    return float(
        Decimal(str(order_shipping_usd))
        * Decimal(str(line_amount))
        / Decimal(str(order_total_line_amount))
    )


# ---------------------------------------------------------------------------
# DB 查询：当日 SKU units / spend / 未匹配 spend
# ---------------------------------------------------------------------------

def get_sku_daily_units(*, product_id: int, business_date: date) -> int:
    """当日 SKU 总销量（按 Meta 业务日归属）。"""
    row = query_one(
        "SELECT COALESCE(SUM(quantity), 0) AS units "
        "FROM dianxiaomi_order_lines "
        "WHERE product_id = %s AND meta_business_date = %s",
        (product_id, business_date),
    )
    if not row:
        return 0
    return int(row.get("units") or 0)


def get_sku_daily_ad_spend(*, product_id: int, business_date: date) -> float:
    """当日 SKU 广告 spend（USD）。

    复用现有 `meta_ad_daily_campaign_metrics`：按 product_id + 业务日求和。
    未匹配 product_id 的 campaign 走 get_unallocated_ad_spend()。
    """
    from tools.meta_daily_final_sync import completed_meta_business_date
    closed_through = completed_meta_business_date()
    if closed_through and business_date > closed_through:
        from appcore.order_analytics.order_profit_aggregation import _load_realtime_ad_snapshot_fallback
        res = _load_realtime_ad_snapshot_fallback(
            date_from=business_date,
            date_to=business_date,
            product_id=product_id,
        )
        spend = res.get("spend_by_product", {}).get((business_date, product_id), 0.0)
        return float(spend)

    row = query_one(
        "SELECT COALESCE(SUM(spend_usd), 0) AS spend "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE product_id = %s AND COALESCE(meta_business_date, report_date) = %s",
        (product_id, business_date),
    )
    if not row:
        return 0.0
    return float(row.get("spend") or 0)


def get_unallocated_ad_spend(*, business_date: date) -> float:
    """当日未匹配 product_id 的 campaign 广告费总和（USD）。

    业务方决策 Q9：单列展示，不进单订单核算；后续做 campaign-product 人工配对兜底。
    """
    from tools.meta_daily_final_sync import completed_meta_business_date
    closed_through = completed_meta_business_date()
    if closed_through and business_date > closed_through:
        from appcore.order_analytics.order_profit_aggregation import _load_realtime_ad_snapshot_fallback
        res = _load_realtime_ad_snapshot_fallback(
            date_from=business_date,
            date_to=business_date,
        )
        return float(res.get("unallocated_spend") or 0.0)

    row = query_one(
        "SELECT COALESCE(SUM(spend_usd), 0) AS spend "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE product_id IS NULL AND COALESCE(meta_business_date, report_date) = %s",
        (business_date,),
    )
    if not row:
        return 0.0
    return float(row.get("spend") or 0)
