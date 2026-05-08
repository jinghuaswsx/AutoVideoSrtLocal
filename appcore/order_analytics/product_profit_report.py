"""产品盈亏报表（订单级 / 每日 / 每国家 / 总账）。

跟 order_profit_lines 的关系：
  - 订单基础字段（line_amount / shipping_allocated / revenue / purchase / shipping_cost
    / return_reserve）直接读 order_profit_lines 已有列。
  - 日期范围按 dianxiaomi_order_lines.meta_business_date 过滤，与 Meta 广告
    spend 的日期口径一致。
  - shopify_fee_usd（合计）也直接读，再按 calculate_shopify_fee 的 rate_breakdown
    比例拆出 base / cross_border / currency_conversion 三项（保证拆分后
    base + intl + conv = order_profit_lines.shopify_fee_usd 不丢精度）。
  - **广告费现场重算**：读取 meta_ad_daily_campaign_metrics 已按 campaign → product
    回填的 product_id 维度 spend，订单行按该产品当日 total units 分摊；总账则
    直接使用 product_id 日期范围 spend，保证与产品列表 Tab 的广告费口径一致。
  - **订单利润现场重算**：revenue - shopify_fee - ad_cost_recalc - purchase
    - shipping_cost - return_reserve。

Shopify Payments 真实 fee：
  当 shopify_payments_transactions 有记录时（按 order_name JOIN），
  在 cost_basis 里标 shopify_fee_source='real'，否则 'estimated'。
  当前真实 fee 还是用合计形式（CSV 不直接给三项细分），所以拆分仍按估算费率比例。
"""
from __future__ import annotations

import io
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .shopify_fee import calculate_shopify_fee, infer_presentment_currency_from_country

# ---------------------------------------------------------------------------
# DB facade（同 cost_allocation.py / order_profit_aggregation.py 模式）
# ---------------------------------------------------------------------------
def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def query_one(*args, **kwargs):
    return _facade().query_one(*args, **kwargs)


# 完整口径：订单 site_code 简称 → 对外展示完整名
SITE_FULL_NAME: dict[str, str] = {
    "newjoy": "newjoyloo",
    "omurio": "Omurio",
}


def _site_full(site_code: str | None) -> str:
    if not site_code:
        return "(未知)"
    return SITE_FULL_NAME.get(site_code, site_code)


# ---------------------------------------------------------------------------
# 产品下拉
# ---------------------------------------------------------------------------
def list_products() -> list[dict[str, Any]]:
    """返回前端产品下拉数据：[{id, product_code, name}, ...]，按 product_code 排序。"""
    rows = query(
        "SELECT id, product_code, name "
        "FROM media_products "
        "WHERE product_code IS NOT NULL AND product_code <> '' "
        "ORDER BY product_code"
    )
    return [
        {"id": int(r["id"]), "product_code": r["product_code"], "name": r.get("name") or ""}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
def _load_order_lines(product_id: int, date_from: date, date_to: date) -> list[dict[str, Any]]:
    """加载产品在指定日期范围内的所有 SKU 行，含订单基础字段 + 现成核算字段。"""
    return query(
        "SELECT "
        "  opl.dxm_order_line_id, dol.meta_business_date AS business_date, opl.paid_at, "
        "  opl.buyer_country, opl.line_amount_usd, opl.shipping_allocated_usd, "
        "  opl.revenue_usd, opl.shopify_fee_usd, opl.purchase_usd, "
        "  opl.shipping_cost_usd, opl.return_reserve_usd, opl.profit_usd AS profit_old_usd, "
        "  opl.shopify_tier, opl.status, opl.missing_fields, opl.cost_basis, "
        "  dol.dxm_package_id, dol.extended_order_id, dol.site_code, "
        "  dol.product_sku, dol.product_display_sku, dol.product_name, "
        "  dol.quantity, dol.unit_price, dol.line_amount AS line_amount_native, "
        "  dol.order_amount AS order_amount_native, dol.order_currency, "
        "  dol.platform "
        "FROM order_profit_lines opl "
        "JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id "
        "WHERE opl.product_id = %s "
        "  AND dol.meta_business_date BETWEEN %s AND %s "
        "ORDER BY dol.meta_business_date ASC, opl.dxm_order_line_id ASC",
        (product_id, date_from, date_to),
    )


def _load_site_daily_units(product_id: int, date_from: date, date_to: date) -> dict[tuple[date, str], int]:
    """加载每天 × 每站点的产品总 units。

    Key: (business_date, site_code) → units. 这里必须使用 dianxiaomi_order_lines
    的 meta_business_date，和 _load_order_lines 的行日期、Meta spend 日期保持一致；
    否则订单行和分摊分母不在同一日期基准上，会导致日广告费被多摊或少摊。
    """
    rows = query(
        "SELECT dol.meta_business_date AS d, dol.site_code, "
        "       COALESCE(SUM(dol.quantity), 0) AS units "
        "FROM order_profit_lines opl "
        "JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id "
        "WHERE opl.product_id = %s "
        "  AND dol.meta_business_date BETWEEN %s AND %s "
        "GROUP BY dol.meta_business_date, dol.site_code",
        (product_id, date_from, date_to),
    )
    out: dict[tuple[date, str], int] = {}
    for r in rows:
        d = r["d"]
        site = r.get("site_code") or ""
        out[(d, site)] = int(r["units"] or 0)
    return out


def _load_account_daily_spend(product_id: int, date_from: date, date_to: date) -> dict[tuple[date, str], float]:
    """每天 × 每账户对该产品的广告 spend。

    Key: (business_date, ad_account_id) → spend_usd. 下游会忽略账户归属，只按
    business_date 合计 product_id 维度 spend，与产品列表 Tab 保持一致。
    """
    rows = query(
        "SELECT COALESCE(meta_business_date, report_date) AS report_date, "
        "       ad_account_id, COALESCE(SUM(spend_usd), 0) AS spend "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE product_id = %s "
        "  AND COALESCE(meta_business_date, report_date) BETWEEN %s AND %s "
        "GROUP BY COALESCE(meta_business_date, report_date), ad_account_id",
        (product_id, date_from, date_to),
    )
    out: dict[tuple[date, str], float] = {}
    for r in rows:
        d = r["report_date"]
        acc = r.get("ad_account_id") or ""
        out[(d, acc)] = float(r["spend"] or 0)
    return out


def _daily_account_spend(account_spend: dict[tuple[date, str], float]) -> dict[date, float]:
    """把 account 维度 spend 汇总成每天的 product_id spend。"""
    out: dict[date, float] = defaultdict(float)
    for (spend_date, _account_id), value in account_spend.items():
        out[spend_date] += float(value or 0.0)
    return out


def _total_account_spend(account_spend: dict[tuple[date, str], float]) -> float:
    """单产品日期范围内的广告 spend，总账与 Tab ① 使用同一 product_id 口径。"""
    return round(sum(_daily_account_spend(account_spend).values()), 2)


def _load_real_fees(extended_order_ids: list[str]) -> dict[str, float]:
    """从 shopify_payments_transactions JOIN 真实 fee（按 order_name）。

    返回 {order_name: total_fee_usd}（同一订单可能有多条 charge/refund，求合计）。
    """
    if not extended_order_ids:
        return {}
    placeholders = ",".join(["%s"] * len(extended_order_ids))
    rows = query(
        f"SELECT order_name, COALESCE(SUM(fee_usd), 0) AS fee "
        f"FROM shopify_payments_transactions "
        f"WHERE type='charge' AND order_name IN ({placeholders}) "
        f"GROUP BY order_name",
        tuple(extended_order_ids),
    )
    return {r["order_name"]: float(r["fee"] or 0) for r in rows}


# ---------------------------------------------------------------------------
# 核心：单行报表行的拼装（含拆分手续费 + 重算广告费 + 重算利润）
# ---------------------------------------------------------------------------
def _split_shopify_fee(line_amount_usd: float, total_fee_usd: float, buyer_country: str | None) -> dict[str, float]:
    """把合计 shopify_fee 按 calculate_shopify_fee 的费率比例拆成三项。

    base_fee + intl_fee + conv_fee = total_fee_usd（保证不引入数字误差）。
    """
    if total_fee_usd <= 0 or line_amount_usd <= 0:
        return {"base_fee": 0.0, "intl_fee": 0.0, "conv_fee": 0.0}

    presentment = infer_presentment_currency_from_country(buyer_country)
    fee_calc = calculate_shopify_fee(
        amount=line_amount_usd,
        presentment_currency=presentment,
        card_country=buyer_country,
    )
    rb = fee_calc["rate_breakdown"]
    base_rate = float(rb["base_rate"])
    cb_rate = float(rb["cross_border_rate"])
    cc_rate = float(rb["currency_conversion_rate"])
    total_rate = base_rate + cb_rate + cc_rate
    if total_rate <= 0:
        return {"base_fee": total_fee_usd, "intl_fee": 0.0, "conv_fee": 0.0}

    # 按比例拆，最后一项用减法保证合计严格等于 total_fee_usd
    intl_fee = round(total_fee_usd * cb_rate / total_rate, 4)
    conv_fee = round(total_fee_usd * cc_rate / total_rate, 4)
    base_fee = round(total_fee_usd - intl_fee - conv_fee, 4)
    return {"base_fee": base_fee, "intl_fee": intl_fee, "conv_fee": conv_fee}


def _recalc_ad_cost(line: dict[str, Any], site_units: dict, account_spend: dict) -> float:
    """按 product_id 归属的当日广告费分摊到本行。

    daily_spend = meta_ad_daily_campaign_metrics 中该 product_id 当日所有账户 spend
    daily_units = 该产品当日所有站点 units
    本行 ad_cost = daily_spend × line_units / daily_units
    """
    business_date = line.get("business_date")
    if not business_date:
        return 0.0
    spend = sum(
        float(value or 0.0)
        for (spend_date, _account_id), value in account_spend.items()
        if spend_date == business_date
    )
    units_total = sum(
        int(units or 0)
        for (units_date, _site), units in site_units.items()
        if units_date == business_date
    )
    line_units = int(line.get("quantity") or 0)
    if spend <= 0 or units_total <= 0 or line_units <= 0:
        return 0.0
    return round(float(spend) * line_units / float(units_total), 4)


def _build_order_row(line: dict[str, Any], site_units: dict, account_spend: dict, real_fees: dict) -> dict[str, Any]:
    """组装单条报表行（含 7 列拆分 + 重算利润）。

    incomplete 行：calculate_line_profit 已经在缺采购价/物流时用 fallback 比例
    （PURCHASE_FALLBACK_RATIO=0.10 / SHIPPING_FALLBACK_RATIO=0.20）算出估算值，
    本函数把这些估算值带出 + 标注 cost_basis_source / estimated_fields，
    前端据此渲染 "估算" 标签。ad_cost 始终按 product_id 当日 spend 现场重算。
    """
    is_incomplete = line.get("status") == "incomplete"
    revenue = float(line.get("revenue_usd") or 0)
    line_amount = float(line.get("line_amount_usd") or 0)
    shipping_allocated = float(line.get("shipping_allocated_usd") or 0)
    buyer_country = line.get("buyer_country")

    # 解析 cost_basis 拿出估算字段标识（DB 里以 JSON 文本存）
    raw_cost_basis = line.get("cost_basis")
    cost_basis_dict: dict = {}
    if isinstance(raw_cost_basis, dict):
        cost_basis_dict = raw_cost_basis
    elif isinstance(raw_cost_basis, str) and raw_cost_basis:
        try:
            import json as _json
            cost_basis_dict = _json.loads(raw_cost_basis) or {}
        except (ValueError, TypeError):
            cost_basis_dict = {}
    estimated_fields = list(cost_basis_dict.get("estimated_fields") or [])
    if is_incomplete and not estimated_fields:
        # 历史数据可能没回填 cost_basis.estimated_fields，从 missing_fields 兜底推断
        raw_missing = line.get("missing_fields")
        missing_list: list = []
        if isinstance(raw_missing, list):
            missing_list = raw_missing
        elif isinstance(raw_missing, str) and raw_missing:
            try:
                import json as _json
                missing_list = _json.loads(raw_missing) or []
            except (ValueError, TypeError):
                missing_list = []
        if "purchase_price" in missing_list:
            estimated_fields.append("purchase")
        if "shipping_cost" in missing_list:
            estimated_fields.append("shipping_cost")

    # ad_cost 跟 status 无关（按 product_id 当日 spend + 当日 units 现场算）
    ad_cost_recalc = _recalc_ad_cost(line, site_units, account_spend)

    # Shopify fee 来源标记：如果 order_name 在 real_fees 里就是 real
    order_name = line.get("extended_order_id") or ""
    fee_source = "real" if order_name in real_fees else "estimated"

    # 成本字段：一律取 DB 上的值（incomplete 时这些是 calc 用 fallback 估算的）
    purchase = round(float(line.get("purchase_usd") or 0), 4) if line.get("purchase_usd") is not None else 0.0
    shipping_cost = round(float(line.get("shipping_cost_usd") or 0), 4) if line.get("shipping_cost_usd") is not None else 0.0
    return_reserve = round(float(line.get("return_reserve_usd") or 0), 4) if line.get("return_reserve_usd") is not None else 0.0
    shopify_fee_total = round(float(line.get("shopify_fee_usd") or 0), 4) if line.get("shopify_fee_usd") is not None else 0.0
    fee_split = _split_shopify_fee(line_amount, shopify_fee_total, buyer_country)
    profit_recalc = round(
        revenue - shopify_fee_total - ad_cost_recalc - purchase - shipping_cost - return_reserve, 4
    )

    # cost_basis_source: 整体一行的成本性质标识，给前端用
    if not is_incomplete:
        cost_basis_source = "real"  # 完整真实
    elif len(estimated_fields) >= 2:
        cost_basis_source = "estimated"  # 全估算
    else:
        cost_basis_source = "partial_estimated"  # 仅部分字段估算

    return {
        "dxm_package_id": line.get("dxm_package_id"),
        "extended_order_id": order_name,
        "paid_at": line.get("paid_at"),
        "business_date": line.get("business_date"),
        "site": _site_full(line.get("site_code")),
        "buyer_country": buyer_country or "",
        "platform": line.get("platform") or "",
        "product_sku": line.get("product_display_sku") or line.get("product_sku") or "",
        "product_name": line.get("product_name") or "",
        "quantity": int(line.get("quantity") or 0),
        "line_amount_usd": round(line_amount, 4),
        "shipping_allocated_usd": round(shipping_allocated, 4),
        "revenue_usd": round(revenue, 4),
        # === 用户要的 7 列（incomplete 时全部为 None，前端显示 "—"）===
        "purchase_cost_usd": purchase,
        "shipping_cost_usd": shipping_cost,
        "shopify_base_fee_usd": fee_split["base_fee"],
        "intl_card_fee_usd": fee_split["intl_fee"],
        "currency_conv_fee_usd": fee_split["conv_fee"],
        "shopify_fee_total_usd": shopify_fee_total,
        "profit_usd": profit_recalc,
        # ===
        "ad_cost_recalc_usd": ad_cost_recalc,
        "ad_cost_old_usd": 0.0,  # 旧值，便于对账
        "return_reserve_usd": return_reserve,
        "shopify_fee_source": fee_source,
        "shopify_tier": line.get("shopify_tier") or "",
        "profit_old_usd": (
            round(float(line["profit_old_usd"]), 4)
            if line.get("profit_old_usd") is not None else None
        ),
        "status": line.get("status"),
        # 整体成本性质："real" / "partial_estimated" / "estimated"
        "cost_basis_source": cost_basis_source,
        # 哪些成本字段是估算（"purchase" / "shipping_cost"）
        "estimated_fields": estimated_fields,
    }


# ---------------------------------------------------------------------------
# 报表入口
# ---------------------------------------------------------------------------
def generate_report(
    *,
    product_id: int,
    date_from: date,
    date_to: date,
) -> dict[str, Any]:
    """返回完整报表字典：{orders, daily, by_country, by_site, total, meta}。"""
    lines = _load_order_lines(product_id, date_from, date_to)
    site_units = _load_site_daily_units(product_id, date_from, date_to)
    account_spend = _load_account_daily_spend(product_id, date_from, date_to)

    extended_ids = list({(line.get("extended_order_id") or "") for line in lines if line.get("extended_order_id")})
    real_fees = _load_real_fees(extended_ids)

    orders = [_build_order_row(line, site_units, account_spend, real_fees) for line in lines]

    # incomplete 行的成本字段是 None，聚合时当 0 处理（跟历史口径一致：
    # 总账只反映 ok 行的成本，incomplete 行只贡献 revenue 和 ad_cost）
    def _n(v):
        return float(v) if v is not None else 0.0

    # === 聚合：每日 ===
    daily_agg: dict[date, dict[str, Any]] = defaultdict(lambda: {
        "lines": 0, "orders_set": set(), "units": 0,
        "revenue": 0.0, "shopify_fee": 0.0, "ad_cost": 0.0,
        "purchase": 0.0, "shipping_cost": 0.0, "return_reserve": 0.0,
        "profit": 0.0,
    })
    for o in orders:
        d = o["business_date"]
        a = daily_agg[d]
        a["lines"] += 1
        a["orders_set"].add(o["dxm_package_id"])
        a["units"] += o["quantity"]
        a["revenue"] += _n(o["revenue_usd"])
        a["shopify_fee"] += _n(o["shopify_fee_total_usd"])
        a["ad_cost"] += _n(o["ad_cost_recalc_usd"])
        a["purchase"] += _n(o["purchase_cost_usd"])
        a["shipping_cost"] += _n(o["shipping_cost_usd"])
        a["return_reserve"] += _n(o["return_reserve_usd"])
        a["profit"] += _n(o["profit_usd"])
    daily = []
    for d in sorted(daily_agg.keys()):
        a = daily_agg[d]
        daily.append({
            "business_date": d,
            "lines": a["lines"],
            "orders": len(a["orders_set"]),
            "units": a["units"],
            "revenue_usd": round(a["revenue"], 2),
            "shopify_fee_usd": round(a["shopify_fee"], 2),
            "ad_cost_usd": round(a["ad_cost"], 2),
            "purchase_usd": round(a["purchase"], 2),
            "shipping_cost_usd": round(a["shipping_cost"], 2),
            "return_reserve_usd": round(a["return_reserve"], 2),
            "profit_usd": round(a["profit"], 2),
        })

    # === 聚合：按国家 × 站点 ===
    cs_agg: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {
        "lines": 0, "orders_set": set(), "units": 0,
        "revenue": 0.0, "shopify_fee": 0.0, "ad_cost": 0.0,
        "purchase": 0.0, "shipping_cost": 0.0, "profit": 0.0,
    })
    for o in orders:
        key = (o["buyer_country"] or "?", o["site"])
        a = cs_agg[key]
        a["lines"] += 1
        a["orders_set"].add(o["dxm_package_id"])
        a["units"] += o["quantity"]
        a["revenue"] += _n(o["revenue_usd"])
        a["shopify_fee"] += _n(o["shopify_fee_total_usd"])
        a["ad_cost"] += _n(o["ad_cost_recalc_usd"])
        a["purchase"] += _n(o["purchase_cost_usd"])
        a["shipping_cost"] += _n(o["shipping_cost_usd"])
        a["profit"] += _n(o["profit_usd"])
    by_country = []
    for (country, site) in sorted(cs_agg.keys()):
        a = cs_agg[(country, site)]
        by_country.append({
            "buyer_country": country,
            "site": site,
            "orders": len(a["orders_set"]),
            "units": a["units"],
            "revenue_usd": round(a["revenue"], 2),
            "shopify_fee_usd": round(a["shopify_fee"], 2),
            "ad_cost_usd": round(a["ad_cost"], 2),
            "purchase_usd": round(a["purchase"], 2),
            "shipping_cost_usd": round(a["shipping_cost"], 2),
            "profit_usd": round(a["profit"], 2),
            "profit_per_order_usd": round(a["profit"] / max(len(a["orders_set"]), 1), 2),
        })

    # === 聚合：按站点 ===
    site_agg: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "lines": 0, "orders_set": set(), "units": 0,
        "revenue": 0.0, "shopify_fee": 0.0, "ad_cost": 0.0,
        "purchase": 0.0, "shipping_cost": 0.0, "profit": 0.0,
    })
    for o in orders:
        a = site_agg[o["site"]]
        a["lines"] += 1
        a["orders_set"].add(o["dxm_package_id"])
        a["units"] += o["quantity"]
        a["revenue"] += _n(o["revenue_usd"])
        a["shopify_fee"] += _n(o["shopify_fee_total_usd"])
        a["ad_cost"] += _n(o["ad_cost_recalc_usd"])
        a["purchase"] += _n(o["purchase_cost_usd"])
        a["shipping_cost"] += _n(o["shipping_cost_usd"])
        a["profit"] += _n(o["profit_usd"])
    by_site = []
    for site in sorted(site_agg.keys()):
        a = site_agg[site]
        by_site.append({
            "site": site,
            "orders": len(a["orders_set"]),
            "units": a["units"],
            "revenue_usd": round(a["revenue"], 2),
            "shopify_fee_usd": round(a["shopify_fee"], 2),
            "ad_cost_usd": round(a["ad_cost"], 2),
            "purchase_usd": round(a["purchase"], 2),
            "shipping_cost_usd": round(a["shipping_cost"], 2),
            "profit_usd": round(a["profit"], 2),
        })

    # === 总账 ===
    total_units = sum(o["quantity"] for o in orders)
    raw_revenue = sum(_n(o["revenue_usd"]) for o in orders)
    raw_shopify_fee = sum(_n(o["shopify_fee_total_usd"]) for o in orders)
    raw_purchase = sum(_n(o["purchase_cost_usd"]) for o in orders)
    raw_shipping = sum(_n(o["shipping_cost_usd"]) for o in orders)
    raw_return = sum(_n(o["return_reserve_usd"]) for o in orders)
    total_revenue = round(raw_revenue, 2)
    total_shopify_fee = round(raw_shopify_fee, 2)
    allocated_ad = round(sum(_n(o["ad_cost_recalc_usd"]) for o in orders), 2)
    total_ad = _total_account_spend(account_spend)
    total_purchase = round(raw_purchase, 2)
    total_shipping = round(raw_shipping, 2)
    total_return = round(raw_return, 2)
    unallocated_ad = round(total_ad - allocated_ad, 2)
    total_profit = round(
        raw_revenue
        - raw_shopify_fee
        - total_ad
        - raw_purchase
        - raw_shipping
        - raw_return,
        2,
    )
    orders_count = len({o["dxm_package_id"] for o in orders})
    incomplete_orders_count = sum(1 for o in orders if o.get("status") == "incomplete")
    # 估算行的成本贡献（让前端能展示 "X 收入由估算成本支撑"）
    estimated_revenue = round(sum(_n(o["revenue_usd"]) for o in orders if o.get("status") == "incomplete"), 2)
    estimated_profit_contrib = round(sum(_n(o["profit_usd"]) for o in orders if o.get("status") == "incomplete"), 2)

    product_row = query_one(
        "SELECT id, product_code, name FROM media_products WHERE id=%s",
        (product_id,),
    ) or {}

    total = {
        "product_id": product_id,
        "product_code": product_row.get("product_code"),
        "product_name": product_row.get("name"),
        "date_from": date_from,
        "date_to": date_to,
        "orders": orders_count,
        "lines": len(orders),
        "units": total_units,
        "revenue_usd": total_revenue,
        "shopify_fee_usd": total_shopify_fee,
        "ad_cost_usd": total_ad,
        "allocated_ad_spend_usd": allocated_ad,
        "unallocated_ad_spend_usd": unallocated_ad,
        "purchase_usd": total_purchase,
        "shipping_cost_usd": total_shipping,
        "return_reserve_usd": total_return,
        "profit_usd": total_profit,
        "profit_pct": round(100 * total_profit / total_revenue, 2) if total_revenue else None,
        "real_fee_coverage_pct": round(
            100 * sum(1 for o in orders if o["shopify_fee_source"] == "real") / max(len(orders), 1),
            1,
        ),
        "incomplete_lines": incomplete_orders_count,
        "incomplete_pct": round(100 * incomplete_orders_count / max(len(orders), 1), 1),
        "estimated_revenue_usd": estimated_revenue,
        "estimated_profit_usd": estimated_profit_contrib,
        "fallback_purchase_ratio_pct": 10.0,  # PURCHASE_FALLBACK_RATIO × 100
        "fallback_shipping_ratio_pct": 20.0,  # SHIPPING_FALLBACK_RATIO × 100
    }

    return {
        "orders": orders,
        "daily": daily,
        "by_country": by_country,
        "by_site": by_site,
        "total": total,
        "meta": {
            "generated_at": datetime.now(),
            "ad_attribution_basis": "product_id_daily_units",
            "shopify_fee_split_basis": "rate_breakdown_proportional",
        },
    }


# ---------------------------------------------------------------------------
# Excel 生成（xlsxwriter）
# ---------------------------------------------------------------------------
ORDER_COLUMNS: list[tuple[str, str]] = [
    ("dxm_package_id", "订单号"),
    ("paid_at", "付款时间"),
    ("business_date", "业务日期"),
    ("site", "站点"),
    ("buyer_country", "买家国家"),
    ("product_sku", "SKU"),
    ("quantity", "数量"),
    ("line_amount_usd", "商品金额 USD"),
    ("shipping_allocated_usd", "运费收入 USD"),
    ("revenue_usd", "总收入 USD"),
    ("purchase_cost_usd", "采购成本"),
    ("shipping_cost_usd", "物流成本"),
    ("shopify_base_fee_usd", "Shopify 平台手续费"),
    ("intl_card_fee_usd", "国际信用卡费"),
    ("currency_conv_fee_usd", "货币转换费"),
    ("shopify_fee_total_usd", "合计手续费"),
    ("ad_cost_recalc_usd", "广告费（修正）"),
    ("return_reserve_usd", "退货占用"),
    ("profit_usd", "订单利润"),
    ("shopify_fee_source", "手续费来源"),
    ("profit_old_usd", "利润（修正前）"),
    ("status", "核算状态"),
]

DAILY_COLUMNS: list[tuple[str, str]] = [
    ("business_date", "业务日期"),
    ("orders", "订单数"),
    ("units", "件数"),
    ("revenue_usd", "总收入"),
    ("shopify_fee_usd", "Shopify 手续费"),
    ("ad_cost_usd", "广告费"),
    ("purchase_usd", "采购成本"),
    ("shipping_cost_usd", "物流成本"),
    ("return_reserve_usd", "退货占用"),
    ("profit_usd", "利润"),
]

COUNTRY_COLUMNS: list[tuple[str, str]] = [
    ("buyer_country", "国家"),
    ("site", "站点"),
    ("orders", "订单数"),
    ("units", "件数"),
    ("revenue_usd", "总收入"),
    ("shopify_fee_usd", "Shopify 手续费"),
    ("ad_cost_usd", "广告费"),
    ("purchase_usd", "采购成本"),
    ("shipping_cost_usd", "物流成本"),
    ("profit_usd", "利润"),
    ("profit_per_order_usd", "单订单平均利润"),
]


def generate_xlsx(report: dict[str, Any]) -> bytes:
    """生成 4-sheet xlsx：订单明细 / 每日 / 按国家 / 总账。"""
    import xlsxwriter

    buf = io.BytesIO()
    book = xlsxwriter.Workbook(buf, {"in_memory": True, "default_date_format": "yyyy-mm-dd"})

    fmt_header = book.add_format({
        "bold": True, "bg_color": "#1e40af", "font_color": "#ffffff",
        "border": 1, "align": "center", "valign": "vcenter",
    })
    fmt_money = book.add_format({"num_format": "#,##0.00"})
    fmt_int = book.add_format({"num_format": "#,##0"})
    fmt_date = book.add_format({"num_format": "yyyy-mm-dd"})
    fmt_datetime = book.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})
    fmt_bold = book.add_format({"bold": True})

    money_keys = {
        "revenue_usd", "line_amount_usd", "shipping_allocated_usd",
        "purchase_cost_usd", "shipping_cost_usd", "shopify_base_fee_usd",
        "intl_card_fee_usd", "currency_conv_fee_usd", "shopify_fee_total_usd",
        "ad_cost_recalc_usd", "ad_cost_old_usd", "return_reserve_usd",
        "profit_usd", "profit_old_usd", "shopify_fee_usd", "ad_cost_usd",
        "purchase_usd", "profit_per_order_usd",
    }
    int_keys = {"quantity", "units", "orders", "lines"}

    def _write_sheet(sheet, columns, rows):
        # header
        for col_idx, (_, label) in enumerate(columns):
            sheet.write(0, col_idx, label, fmt_header)
        # data
        for row_idx, row in enumerate(rows, start=1):
            for col_idx, (key, _) in enumerate(columns):
                val = row.get(key)
                if val is None:
                    continue
                if key in money_keys and isinstance(val, (int, float)):
                    sheet.write_number(row_idx, col_idx, float(val), fmt_money)
                elif key in int_keys and isinstance(val, int):
                    sheet.write_number(row_idx, col_idx, val, fmt_int)
                elif isinstance(val, datetime):
                    sheet.write_datetime(row_idx, col_idx, val, fmt_datetime)
                elif isinstance(val, date):
                    sheet.write_datetime(row_idx, col_idx, datetime(val.year, val.month, val.day), fmt_date)
                else:
                    sheet.write(row_idx, col_idx, val)
        sheet.freeze_panes(1, 0)
        sheet.autofilter(0, 0, max(len(rows), 1), len(columns) - 1)
        for col_idx, (_, label) in enumerate(columns):
            sheet.set_column(col_idx, col_idx, max(len(label) * 2 + 2, 12))

    # Sheet 1: 订单明细
    sh = book.add_worksheet("订单明细")
    _write_sheet(sh, ORDER_COLUMNS, report["orders"])

    # Sheet 2: 每日盈亏
    sh = book.add_worksheet("每日盈亏")
    _write_sheet(sh, DAILY_COLUMNS, report["daily"])

    # Sheet 3: 按国家
    sh = book.add_worksheet("按国家")
    _write_sheet(sh, COUNTRY_COLUMNS, report["by_country"])

    # Sheet 4: 产品总账
    sh = book.add_worksheet("产品总账")
    total = report["total"]
    sh.write(0, 0, "项目", fmt_header)
    sh.write(0, 1, "值", fmt_header)
    rows_total = [
        ("产品", f"{total.get('product_code') or ''} ({total.get('product_name') or ''})"),
        ("时间范围", f"{total.get('date_from')} ~ {total.get('date_to')}"),
        ("订单数", total.get("orders", 0)),
        ("SKU 行数", total.get("lines", 0)),
        ("总件数", total.get("units", 0)),
        ("总收入 (USD)", total.get("revenue_usd", 0)),
        ("Shopify 手续费", total.get("shopify_fee_usd", 0)),
        ("广告费（修正）", total.get("ad_cost_usd", 0)),
        ("采购成本", total.get("purchase_usd", 0)),
        ("物流成本", total.get("shipping_cost_usd", 0)),
        ("退货占用", total.get("return_reserve_usd", 0)),
        ("总利润 (USD)", total.get("profit_usd", 0)),
        ("利润率 %", total.get("profit_pct")),
        ("Shopify Fee 真实覆盖率 %", total.get("real_fee_coverage_pct", 0)),
    ]
    for idx, (label, val) in enumerate(rows_total, start=1):
        sh.write(idx, 0, label, fmt_bold)
        if isinstance(val, (int, float)):
            sh.write_number(idx, 1, float(val), fmt_money if isinstance(val, float) else fmt_int)
        else:
            sh.write(idx, 1, val if val is not None else "")
    sh.set_column(0, 0, 28)
    sh.set_column(1, 1, 36)

    # 站点切片接在产品总账下面
    by_site = report.get("by_site") or []
    if by_site:
        start_row = len(rows_total) + 3
        sh.write(start_row, 0, "站点切片", fmt_header)
        sh.write(start_row, 1, "", fmt_header)
        site_cols = [
            ("site", "站点"), ("orders", "订单"), ("units", "件数"),
            ("revenue_usd", "收入"), ("shopify_fee_usd", "手续费"),
            ("ad_cost_usd", "广告费"), ("profit_usd", "利润"),
        ]
        for col_idx, (_, label) in enumerate(site_cols):
            sh.write(start_row + 1, col_idx, label, fmt_header)
        for r_idx, srow in enumerate(by_site, start=start_row + 2):
            for c_idx, (key, _) in enumerate(site_cols):
                val = srow.get(key)
                if val is None:
                    continue
                if isinstance(val, (int, float)) and key != "site":
                    sh.write_number(r_idx, c_idx, float(val), fmt_money if isinstance(val, float) else fmt_int)
                else:
                    sh.write(r_idx, c_idx, val)

    book.close()
    buf.seek(0)
    return buf.read()
