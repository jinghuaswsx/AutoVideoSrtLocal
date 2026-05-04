"""核心订单 SKU 利润核算公式。

完整公式（USD）：
    revenue        = line_amount + shipping_allocated
    shopify_fee    = calculate_shopify_fee(revenue, presentment, card_country)["fee"]
    ad_cost        = (sku_daily_spend × line_units) / sku_daily_units
    purchase       = purchase_price_cny × quantity / rmb_per_usd
    shipping_cost  = packet_cost_cny × quantity / rmb_per_usd
    return_reserve = revenue × return_reserve_rate (1%)
    profit         = revenue - shopify_fee - ad_cost - purchase
                   - shipping_cost - return_reserve

不完备 SKU（缺采购价或包装成本）→ 返回 status='incomplete'，profit 为 None。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .cost_allocation import allocate_ad_cost_to_line
from .shopify_fee import estimate_fee_for_buyer_country


_DEFAULT_RETURN_RESERVE_RATE = Decimal("0.01")


def _q4(value: Decimal) -> float:
    """4 位小数（DECIMAL(12,4) 列对齐）。"""
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def calculate_line_profit(
    line: dict[str, Any],
    *,
    rmb_per_usd: Decimal,
    return_reserve_rate: Decimal = _DEFAULT_RETURN_RESERVE_RATE,
) -> dict[str, Any]:
    """单 SKU 行利润核算。

    Args:
        line: 输入字典，含：
            line_amount_usd, quantity, buyer_country,
            shipping_allocated_usd, sku_daily_units, sku_daily_ad_spend_usd,
            product_purchase_price_cny, product_packet_cost_cny,
            packet_cost_basis ('actual' | 'estimated' | None)
        rmb_per_usd: 当前 USD→CNY 汇率
        return_reserve_rate: 退货成本占用率（默认 1%）

    Returns:
        若完备 (purchase_price + packet_cost 都有值)：
            {status: 'ok', profit_usd, revenue_usd, shopify_fee_usd,
             ad_cost_usd, purchase_usd, shipping_cost_usd, return_reserve_usd,
             shopify_tier, cost_basis: {...}}
        若不完备：
            {status: 'incomplete', profit_usd: None, missing_fields: [...]}
    """
    # 1. 完备性 gate
    missing: list[str] = []
    if line.get("product_purchase_price_cny") is None:
        missing.append("purchase_price")
    if line.get("product_packet_cost_cny") is None:
        missing.append("packet_cost")
    if missing:
        return {
            "status": "incomplete",
            "profit_usd": None,
            "missing_fields": missing,
            "dxm_order_line_id": line.get("dxm_order_line_id"),
        }

    # 2. 收入侧
    line_amount = _to_decimal(line.get("line_amount_usd"))
    shipping_allocated = _to_decimal(line.get("shipping_allocated_usd"))
    revenue = line_amount + shipping_allocated

    # 3. Shopify 手续费
    fee_result = estimate_fee_for_buyer_country(
        amount=float(revenue),
        buyer_country=line.get("buyer_country"),
    )
    shopify_fee = _to_decimal(fee_result["fee"])
    shopify_tier = fee_result["tier"]

    # 4. 广告费摊到行
    ad_cost = _to_decimal(allocate_ad_cost_to_line(
        line_units=int(line.get("quantity") or 0),
        daily_total_units=int(line.get("sku_daily_units") or 0),
        daily_spend_usd=float(line.get("sku_daily_ad_spend_usd") or 0),
    ))

    # 5. 采购成本（CNY → USD）
    quantity = _to_decimal(line.get("quantity"))
    purchase_cny = _to_decimal(line.get("product_purchase_price_cny"))
    purchase_usd = (purchase_cny * quantity) / rmb_per_usd

    # 6. 包装/小包物流成本（CNY → USD）
    packet_cny = _to_decimal(line.get("product_packet_cost_cny"))
    shipping_cost_usd = (packet_cny * quantity) / rmb_per_usd

    # 7. 退货占用
    return_reserve = revenue * return_reserve_rate

    # 8. 利润
    profit = revenue - shopify_fee - ad_cost - purchase_usd - shipping_cost_usd - return_reserve

    return {
        "status": "ok",
        "dxm_order_line_id": line.get("dxm_order_line_id"),
        "product_id": line.get("product_id"),
        "buyer_country": line.get("buyer_country"),
        "presentment_currency": fee_result.get("rate_breakdown", {}) and None,  # 暂存
        "shopify_tier": shopify_tier,
        "line_amount_usd": _q4(line_amount),
        "shipping_allocated_usd": _q4(shipping_allocated),
        "revenue_usd": _q4(revenue),
        "shopify_fee_usd": _q4(shopify_fee),
        "ad_cost_usd": _q4(ad_cost),
        "purchase_usd": _q4(purchase_usd),
        "shipping_cost_usd": _q4(shipping_cost_usd),
        "return_reserve_usd": _q4(return_reserve),
        "profit_usd": _q4(profit),
        "missing_fields": [],
        "cost_basis": {
            "rmb_per_usd": float(rmb_per_usd),
            "return_reserve_rate": float(return_reserve_rate),
            "purchase_price_cny": float(purchase_cny),
            "packet_cost_cny": float(packet_cny),
            "packet_cost_basis": line.get("packet_cost_basis"),
            "sku_daily_units": int(line.get("sku_daily_units") or 0),
            "sku_daily_ad_spend_usd": float(line.get("sku_daily_ad_spend_usd") or 0),
        },
    }


def aggregate_order_profit(line_results: list[dict[str, Any]]) -> dict[str, Any]:
    """订单级利润聚合：把订单内多条 SKU 行的 profit 求和。

    若全部行完备 → status='ok'，profit_usd = SUM(line.profit_usd)
    若部分行不完备 → status='partially_complete'，profit_usd = SUM(完备行) +
                     incomplete_lines 列出哪些行待补
    若全部行不完备 → status='incomplete'，profit_usd = None
    """
    total = {
        "revenue_usd": Decimal("0"),
        "shopify_fee_usd": Decimal("0"),
        "ad_cost_usd": Decimal("0"),
        "purchase_usd": Decimal("0"),
        "shipping_cost_usd": Decimal("0"),
        "return_reserve_usd": Decimal("0"),
        "profit_usd": Decimal("0"),
    }
    complete_count = 0
    incomplete_count = 0
    incomplete_lines: list[dict[str, Any]] = []

    for line in line_results:
        if line.get("status") == "ok":
            complete_count += 1
            for key in total.keys():
                total[key] += _to_decimal(line.get(key))
        else:
            incomplete_count += 1
            incomplete_lines.append({
                "dxm_order_line_id": line.get("dxm_order_line_id"),
                "missing_fields": line.get("missing_fields", []),
            })

    if complete_count == 0 and incomplete_count > 0:
        status = "incomplete"
        profit_usd = None
    elif incomplete_count > 0:
        status = "partially_complete"
        profit_usd = _q4(total["profit_usd"])
    else:
        status = "ok"
        profit_usd = _q4(total["profit_usd"])

    return {
        "status": status,
        "profit_usd": profit_usd,
        "revenue_usd": _q4(total["revenue_usd"]) if complete_count else None,
        "lines_complete": complete_count,
        "lines_incomplete": incomplete_count,
        "incomplete_lines": incomplete_lines,
    }
