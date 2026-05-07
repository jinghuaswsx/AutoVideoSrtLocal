"""产品盈亏列表（全产品聚合，给 Tab ① 用）。

按日期范围 + 国家维度聚合每个 media_product，输出每个产品的：
订单数 / 收入 / 物流费 / 采购费 / 广告费 / ROAS / 利润 / 利润率 / 成本完备性。

数据口径与 product_profit_report.generate_report() 单产品口径完全一致：
- 订单 / 收入 / 各项费用：order_profit_lines + dianxiaomi_order_lines JOIN
- 广告费：按订单 site_code → ad_account 1:1 映射，按当日 units 分摊
  （广告费表与 product_profit_report 一致使用 meta_ad_daily_campaign_metrics +
   report_date 字段，避免引入新口径）
- 利润 = revenue - shopify_fee - ad_cost - purchase - shipping - return_reserve
"""
from __future__ import annotations

import io
import logging
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from .cost_completeness import check_sku_cost_completeness
from .product_profit_report import SITE_TO_AD_ACCOUNT

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB facade（同 product_profit_report.py / cost_completeness.py 模式）
# ---------------------------------------------------------------------------
def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def query_one(*args, **kwargs):
    return _facade().query_one(*args, **kwargs)


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
def _load_lines(date_from: date, date_to: date, country: str | None) -> list[dict[str, Any]]:
    """加载日期范围内所有产品的订单行（不限定单一 product_id）。

    country：传非空且不是 "all" 时按 buyer_country 严格过滤（已 upper 归一化）。
    """
    sql = (
        "SELECT "
        "  opl.product_id, mp.product_code, mp.name, "
        "  opl.business_date, opl.buyer_country, "
        "  opl.revenue_usd, opl.shopify_fee_usd, opl.purchase_usd, "
        "  opl.shipping_cost_usd, opl.return_reserve_usd, "
        "  dol.site_code, dol.quantity, dol.dxm_package_id "
        "FROM order_profit_lines opl "
        "JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id "
        "JOIN media_products mp ON mp.id = opl.product_id "
        "WHERE opl.business_date BETWEEN %s AND %s "
    )
    params: list[Any] = [date_from, date_to]
    if country and country.strip().lower() not in ("", "all"):
        sql += " AND opl.buyer_country = %s "
        params.append(country.strip().upper())
    sql += " ORDER BY opl.product_id, opl.business_date"
    return query(sql, tuple(params))


def _load_ad_spend(date_from: date, date_to: date) -> dict[tuple[date, str], Decimal]:
    """日期 × 广告账户 → spend_usd。

    用同款 meta_ad_daily_campaign_metrics + report_date，与 product_profit_report
    保持口径一致（不引入 realtime 表的不同业务日定义）。
    """
    rows = query(
        "SELECT report_date, ad_account_id, COALESCE(SUM(spend_usd), 0) AS spend "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE report_date BETWEEN %s AND %s "
        "GROUP BY report_date, ad_account_id",
        (date_from, date_to),
    )
    out: dict[tuple[date, str], Decimal] = {}
    for r in rows:
        d = r["report_date"]
        acc = r.get("ad_account_id") or ""
        if not acc:
            continue
        out[(d, acc)] = Decimal(str(r["spend"] or 0))
    return out


def _load_site_units(date_from: date, date_to: date) -> dict[tuple[date, str], int]:
    """每天 × 每站点的全产品总 units，用于按 units 比例分摊广告费。

    与 product_profit_report._load_site_daily_units 的差别：这里是全产品维度，
    不限定 product_id。
    """
    rows = query(
        "SELECT opl.business_date, dol.site_code, "
        "       COALESCE(SUM(dol.quantity), 0) AS units "
        "FROM order_profit_lines opl "
        "JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id "
        "WHERE opl.business_date BETWEEN %s AND %s "
        "GROUP BY opl.business_date, dol.site_code",
        (date_from, date_to),
    )
    out: dict[tuple[date, str], int] = {}
    for r in rows:
        site = r.get("site_code") or ""
        out[(r["business_date"], site)] = int(r["units"] or 0)
    return out


def _load_product_costs(product_ids: list[int]) -> dict[int, dict[str, Any]]:
    """加载产品成本字段（用于 cost_completeness 检查）。"""
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = query(
        f"SELECT id, purchase_price, packet_cost_actual, packet_cost_estimated "
        f"FROM media_products WHERE id IN ({placeholders})",
        tuple(product_ids),
    )
    return {int(r["id"]): r for r in rows}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def _completeness_label(product_row: dict[str, Any]) -> str:
    """把 cost_completeness.check_sku_cost_completeness 的真实返回（含 ok/missing）
    映射到列表展示用的字符串。

    真实返回字段：{"ok": bool, "missing": [...], ...}
    → ok=True 输出 "ok"；否则输出 "incomplete"。
    （不实现 plan 假设的 partial 档：cost_completeness 没有这一档。）
    """
    if not product_row:
        return "incomplete"
    res = check_sku_cost_completeness(product_row)
    return "ok" if res.get("ok") else "incomplete"


def generate_list(
    *,
    date_from: date,
    date_to: date,
    country: str | None = None,
) -> dict[str, Any]:
    """生成全产品聚合列表 + summary。

    Args:
        date_from / date_to: 日期范围（按 order_profit_lines.business_date 过滤）
        country: 可选国家过滤（buyer_country）；None / "" / "all" 视为不过滤

    Returns:
        {
          "rows": [
            {
              "product_id": int, "product_code": str, "name": str,
              "order_count": int,
              "revenue_usd": float,
              "shipping_cost_usd": float, "shipping_pct": float,
              "purchase_usd": float, "purchase_pct": float,
              "ad_cost_usd": float, "ad_pct": float,
              "roas": float | None,
              "profit_usd": float, "profit_pct": float,
              "cost_completeness": "ok" | "incomplete",
            }, ...
          ],
          "summary": {
            "product_count": int,
            "total_orders": int,
            "total_revenue_usd": float,
            "total_profit_usd": float,
            "overall_roas": float | None,
          }
        }
    """
    lines = _load_lines(date_from, date_to, country)
    if not lines:
        return {
            "rows": [],
            "summary": {
                "product_count": 0,
                "total_orders": 0,
                "total_revenue_usd": 0.0,
                "total_profit_usd": 0.0,
                "overall_roas": None,
            },
        }

    ad_spend = _load_ad_spend(date_from, date_to)
    site_units = _load_site_units(date_from, date_to)
    product_ids = list({int(line["product_id"]) for line in lines if line.get("product_id") is not None})
    product_costs = _load_product_costs(product_ids)

    # 按产品分组聚合
    def _zero_bucket() -> dict[str, Any]:
        return {
            "product_id": 0,
            "product_code": "",
            "name": "",
            # order_keys: set[dxm_package_id]，用于按订单去重计数
            # 同一订单包含 N 个 SKU 行时只算 1 单，避免出现「1 单 2 SKU 显示为 2 单」。
            "order_keys": set(),
            "revenue": Decimal("0"),
            "shopify_fee": Decimal("0"),
            "purchase": Decimal("0"),
            "shipping_cost": Decimal("0"),
            "return_reserve": Decimal("0"),
            "ad_cost": Decimal("0"),
        }

    by_product: dict[int, dict[str, Any]] = defaultdict(_zero_bucket)

    for line in lines:
        pid = int(line["product_id"])
        bucket = by_product[pid]
        bucket["product_id"] = pid
        bucket["product_code"] = line.get("product_code") or ""
        bucket["name"] = line.get("name") or ""
        pkg_id = line.get("dxm_package_id")
        if pkg_id is not None:
            bucket["order_keys"].add(pkg_id)
        bucket["revenue"] += Decimal(str(line.get("revenue_usd") or 0))
        bucket["shopify_fee"] += Decimal(str(line.get("shopify_fee_usd") or 0))
        bucket["purchase"] += Decimal(str(line.get("purchase_usd") or 0))
        bucket["shipping_cost"] += Decimal(str(line.get("shipping_cost_usd") or 0))
        bucket["return_reserve"] += Decimal(str(line.get("return_reserve_usd") or 0))

        # 广告分摊：当日当站全部 spend × (本行 units / 当日当站全部 units)
        site_code = line.get("site_code") or ""
        ad_account = SITE_TO_AD_ACCOUNT.get(site_code) if site_code else None
        if ad_account:
            day_spend = ad_spend.get((line["business_date"], ad_account), Decimal("0"))
            day_units = site_units.get((line["business_date"], site_code), 0)
            line_units = int(line.get("quantity") or 0)
            if day_spend > 0 and day_units > 0 and line_units > 0:
                share = Decimal(line_units) / Decimal(day_units)
                bucket["ad_cost"] += day_spend * share

    # 转 rows（按 revenue 降序）
    rows: list[dict[str, Any]] = []
    total_orders = 0
    total_revenue = Decimal("0")
    total_profit = Decimal("0")
    total_ad = Decimal("0")
    for pid, b in sorted(by_product.items(), key=lambda kv: -kv[1]["revenue"]):
        revenue = b["revenue"]
        profit = (
            revenue
            - b["shopify_fee"]
            - b["ad_cost"]
            - b["purchase"]
            - b["shipping_cost"]
            - b["return_reserve"]
        )
        roas = float(revenue / b["ad_cost"]) if b["ad_cost"] > 0 else None
        order_count = len(b["order_keys"])
        rows.append({
            "product_id": pid,
            "product_code": b["product_code"],
            "name": b["name"],
            "order_count": order_count,
            "revenue_usd": float(revenue),
            "shipping_cost_usd": float(b["shipping_cost"]),
            "shipping_pct": float(b["shipping_cost"] / revenue) if revenue > 0 else 0.0,
            "purchase_usd": float(b["purchase"]),
            "purchase_pct": float(b["purchase"] / revenue) if revenue > 0 else 0.0,
            "ad_cost_usd": float(b["ad_cost"]),
            "ad_pct": float(b["ad_cost"] / revenue) if revenue > 0 else 0.0,
            "roas": roas,
            "profit_usd": float(profit),
            "profit_pct": float(profit / revenue) if revenue > 0 else 0.0,
            "cost_completeness": _completeness_label(product_costs.get(pid, {})),
        })
        total_orders += order_count
        total_revenue += revenue
        total_profit += profit
        total_ad += b["ad_cost"]

    summary = {
        "product_count": len(rows),
        "total_orders": total_orders,
        "total_revenue_usd": float(total_revenue),
        "total_profit_usd": float(total_profit),
        "overall_roas": float(total_revenue / total_ad) if total_ad > 0 else None,
    }
    return {"rows": rows, "summary": summary}


# ---------------------------------------------------------------------------
# xlsx 导出（Tab ① 列表）
# ---------------------------------------------------------------------------
def generate_list_xlsx(report: dict[str, Any]) -> bytes:
    """把 generate_list() 的结果导出为 xlsx。两个 sheet：summary、products。

    与 product_profit_report.generate_xlsx 一致采用 lazy import openpyxl 的模式，
    避免在不需要导出时额外加载依赖。
    """
    import openpyxl  # noqa: PLC0415 - lazy import 同 product_profit_report.generate_xlsx

    wb = openpyxl.Workbook()

    ws1 = wb.active
    ws1.title = "summary"
    s = report["summary"]
    ws1.append(["指标", "值"])
    ws1.append(["产品数", s["product_count"]])
    ws1.append(["订单数", s["total_orders"]])
    ws1.append(["收入(USD)", s["total_revenue_usd"]])
    ws1.append(["利润(USD)", s["total_profit_usd"]])
    ws1.append(["整体 ROAS", s["overall_roas"]])

    ws2 = wb.create_sheet("products")
    headers = [
        "产品代码", "产品名", "订单数", "收入(USD)",
        "物流(USD)", "物流占比", "采购(USD)", "采购占比",
        "广告(USD)", "广告占比", "ROAS", "利润(USD)", "利润率", "成本完备",
    ]
    ws2.append(headers)
    for r in report["rows"]:
        ws2.append([
            r["product_code"], r["name"], r["order_count"], r["revenue_usd"],
            r["shipping_cost_usd"], r["shipping_pct"],
            r["purchase_usd"], r["purchase_pct"],
            r["ad_cost_usd"], r["ad_pct"],
            r["roas"] if r["roas"] is not None else "",
            r["profit_usd"], r["profit_pct"],
            r["cost_completeness"],
        ])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
