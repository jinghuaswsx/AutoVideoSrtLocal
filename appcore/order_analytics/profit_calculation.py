"""核心订单 SKU 利润核算公式。

完整公式（USD）：
    revenue        = line_amount + shipping_allocated
    shopify_fee    = calculate_shopify_fee(revenue, presentment, card_country)["fee"]
    ad_cost        = (sku_daily_spend × line_units) / sku_daily_units
    purchase       = purchase_price_cny × quantity / rmb_per_usd
    shipping_cost  = shipping_cost_cny / rmb_per_usd
    return_reserve = revenue × return_reserve_rate (1%)
    profit         = revenue - shopify_fee - ad_cost - purchase
                   - shipping_cost - return_reserve

shipping_cost_cny 由调用方按三级降级链预解析：
  1. allocated_logistic_fee           (订单级真实值, 按 line_amount 比例分摊)
  2. packet_cost_actual × quantity    (产品均值)
  3. packet_cost_estimated × quantity (产品中位数)

不完备 SKU（缺采购价或 shipping_cost_cny）→ 返回 status='incomplete'，profit 为 None。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .cost_allocation import allocate_ad_cost_to_line
from .shopify_fee import estimate_fee_for_buyer_country


_DEFAULT_RETURN_RESERVE_RATE = Decimal("0.01")
_RETURN_RESERVE_RATE_SETTING_KEY = "order_profit_return_reserve_rate"


def get_configured_return_reserve_rate() -> Decimal:
    """读 system_settings.order_profit_return_reserve_rate（默认 0.01 = 1%）。

    业务方可在 admin/settings 调整，调整后下一次 backfill / incremental 自动生效。
    """
    try:
        from appcore.settings import get_setting
        raw = get_setting(_RETURN_RESERVE_RATE_SETTING_KEY)
        if raw is None or str(raw).strip() == "":
            return _DEFAULT_RETURN_RESERVE_RATE
        rate = Decimal(str(raw).strip())
        if rate < 0 or rate > Decimal("1"):
            return _DEFAULT_RETURN_RESERVE_RATE
        return rate
    except Exception:
        return _DEFAULT_RETURN_RESERVE_RATE


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
            product_purchase_price_cny, shipping_cost_cny, shipping_cost_source
        rmb_per_usd: 当前 USD→CNY 汇率
        return_reserve_rate: 退货成本占用率（默认 1%）

    Returns:
        若完备 (purchase_price + shipping_cost_cny 都有值)：
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
    if line.get("shipping_cost_cny") is None:
        missing.append("shipping_cost")
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

    # 3. Shopify 手续费（H1 修复：按订单 amount 一次算 fee + 摊回行）
    # Shopify 实际按"整笔交易 amount"收一次 fee（含 0.30 固定费），不是按 SKU 行多次。
    # 调用方传入 order_total_revenue_usd → 算出整单 fee → 按 line revenue 比例摊回本行。
    # 缺省（单 SKU 订单）→ 退化为按本行 revenue 算 fee（结果一致）。
    order_total_revenue_usd = line.get("order_total_revenue_usd")
    if order_total_revenue_usd is not None and float(order_total_revenue_usd) > 0:
        order_revenue = _to_decimal(order_total_revenue_usd)
        fee_result = estimate_fee_for_buyer_country(
            amount=float(order_revenue),
            buyer_country=line.get("buyer_country"),
        )
        order_fee = _to_decimal(fee_result["fee"])
        # 按本行 revenue / 订单 revenue 比例摊
        shopify_fee = order_fee * (revenue / order_revenue) if order_revenue > 0 else _to_decimal(0)
    else:
        # 单 SKU 订单：等价于按本行 revenue 算 fee
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

    # 6. 小包物流成本（CNY → USD），已由调用方预解析为行级总额
    shipping_cost_cny = _to_decimal(line.get("shipping_cost_cny"))
    shipping_cost_usd = shipping_cost_cny / rmb_per_usd

    # 7. 退货占用
    return_reserve = revenue * return_reserve_rate

    # 8. 利润
    profit = revenue - shopify_fee - ad_cost - purchase_usd - shipping_cost_usd - return_reserve

    return {
        "status": "ok",
        "dxm_order_line_id": line.get("dxm_order_line_id"),
        "product_id": line.get("product_id"),
        "buyer_country": line.get("buyer_country"),
        "presentment_currency": fee_result.get("rate_breakdown", {}) and None,
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
            "shipping_cost_cny": float(shipping_cost_cny),
            "shipping_cost_source": line.get("shipping_cost_source"),
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
