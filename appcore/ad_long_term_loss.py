"""长期亏损品报警：产品级真实利润 + 波动豁免规则。

Docs-anchor: docs/superpowers/specs/2026-06-14-ad-alert-long-term-loss-product-design.md
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from appcore import settings as system_settings
from appcore.db import query
from appcore.order_analytics._helpers import current_meta_business_date
from appcore.order_analytics._open_day_freshness import ensure_open_day_profit_lines_fresh
from appcore.order_analytics.product_profit_list import _load_ad_spend

log = logging.getLogger(__name__)

# (setting_key, default, caster)
_LTL_SETTINGS: dict[str, tuple[str, float, type]] = {
    "long_days": ("ad_alert_ltl_long_days", 30, int),
    "recent_days": ("ad_alert_ltl_recent_days", 7, int),
    "loss_ratio": ("ad_alert_ltl_loss_ratio", 0.10, float),
    "min_active_days": ("ad_alert_ltl_min_active_days", 10, int),
    "min_spend_7d": ("ad_alert_ltl_min_spend_7d", 50.0, float),
    "min_loss_7d": ("ad_alert_ltl_min_loss_7d", 20.0, float),
    "est_cost_rate": ("ad_alert_ltl_est_cost_rate", 0.08, float),
    "est_shipping_rate": ("ad_alert_ltl_est_shipping_rate", 0.17, float),
}


@dataclass
class LtlVerdict:
    alert: bool
    verdict: str | None  # "long_term_net_loss" | "erodes_profit" | None
    loss_7d: float
    loss_ratio: float | None


def judge_long_term_loss(
    *, profit_7d: float, profit_30d: float, loss_ratio: float
) -> LtlVerdict:
    """对单品的窗口盈亏做判定。详见 spec「判定规则」。"""
    if profit_7d >= 0:
        return LtlVerdict(False, None, 0.0, None)
    loss_7d = -profit_7d
    if profit_30d <= 0:
        return LtlVerdict(True, "long_term_net_loss", loss_7d, None)
    ratio = loss_7d / profit_30d
    if ratio > loss_ratio:
        return LtlVerdict(True, "erodes_profit", loss_7d, ratio)
    return LtlVerdict(False, None, loss_7d, ratio)


def get_ltl_config() -> dict[str, float]:
    cfg: dict[str, float] = {}
    for name, (key, default, caster) in _LTL_SETTINGS.items():
        raw = None
        try:
            raw = system_settings.get_setting(key)
        except Exception:
            raw = None
        if raw is None or str(raw).strip() == "":
            cfg[name] = default
            continue
        try:
            cfg[name] = caster(str(raw).strip())
        except (TypeError, ValueError):
            cfg[name] = default
    return cfg


@dataclass
class WindowMetric:
    product_id: int
    product_code: str
    product_name: str
    product_main_image: str | None
    revenue_7d: float
    profit_7d: float
    revenue_30d: float
    profit_30d: float
    spend_7d: float
    active_days: int
    has_estimated_cost: bool
    first_active_date: date | None
    last_active_date: date | None


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _load_window_metrics(business_date: date, cfg: dict[str, float]) -> dict[int, WindowMetric]:
    long_days = int(cfg["long_days"])
    recent_days = int(cfg["recent_days"])
    d30 = business_date - timedelta(days=long_days - 1)
    d7 = business_date - timedelta(days=recent_days - 1)

    ensure_open_day_profit_lines_fresh(d30, business_date)

    params = {
        "d30": d30,
        "d7": d7,
        "today": business_date,
        "cost_rate": cfg["est_cost_rate"],
        "ship_rate": cfg["est_shipping_rate"],
    }
    rows = query(
        """
        SELECT
          opl.product_id,
          MAX(mp.product_code) AS product_code,
          MAX(mp.name) AS product_name,
          MAX(mp.main_image) AS product_main_image,
          MIN(dol.meta_business_date) AS first_active_date,
          MAX(dol.meta_business_date) AS last_active_date,
          SUM(CASE WHEN dol.meta_business_date >= %(d7)s THEN opl.revenue_usd ELSE 0 END) AS revenue_7d,
          SUM(CASE WHEN dol.meta_business_date >= %(d7)s THEN opl.shopify_fee_usd ELSE 0 END) AS fee_7d,
          SUM(CASE WHEN dol.meta_business_date >= %(d7)s THEN opl.return_reserve_usd ELSE 0 END) AS rr_7d,
          SUM(CASE WHEN dol.meta_business_date >= %(d7)s THEN
                CASE WHEN opl.missing_fields LIKE '%%purchase_price%%'
                     THEN opl.revenue_usd * %(cost_rate)s ELSE opl.purchase_usd END
              ELSE 0 END) AS purchase_7d,
          SUM(CASE WHEN dol.meta_business_date >= %(d7)s THEN
                CASE WHEN opl.missing_fields LIKE '%%shipping_cost%%'
                     THEN opl.revenue_usd * %(ship_rate)s ELSE opl.shipping_cost_usd END
              ELSE 0 END) AS shipping_7d,
          SUM(opl.revenue_usd) AS revenue_30d,
          SUM(opl.shopify_fee_usd) AS fee_30d,
          SUM(opl.return_reserve_usd) AS rr_30d,
          SUM(CASE WHEN opl.missing_fields LIKE '%%purchase_price%%'
                   THEN opl.revenue_usd * %(cost_rate)s ELSE opl.purchase_usd END) AS purchase_30d,
          SUM(CASE WHEN opl.missing_fields LIKE '%%shipping_cost%%'
                   THEN opl.revenue_usd * %(ship_rate)s ELSE opl.shipping_cost_usd END) AS shipping_30d,
          MAX(CASE WHEN opl.status <> 'ok' THEN 1 ELSE 0 END) AS has_estimated
        FROM order_profit_lines opl
        JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id
        LEFT JOIN media_products mp ON mp.id = opl.product_id
        WHERE dol.meta_business_date BETWEEN %(d30)s AND %(today)s
          AND opl.product_id IS NOT NULL
        GROUP BY opl.product_id
        """,
        params,
    )

    active_rows = query(
        """
        SELECT product_id,
               COUNT(DISTINCT COALESCE(meta_business_date, report_date)) AS active_days
        FROM meta_ad_daily_ad_metrics
        WHERE COALESCE(meta_business_date, report_date) BETWEEN %(d30)s AND %(today)s
          AND product_id IS NOT NULL
          AND COALESCE(spend_usd, 0) > 0
        GROUP BY product_id
        """,
        params,
    )
    active_by_pid = {int(r["product_id"]): int(r["active_days"] or 0) for r in active_rows or []}

    spend_7d_by_pid = _load_ad_spend(d7, business_date)
    spend_30d_by_pid = _load_ad_spend(d30, business_date)

    out: dict[int, WindowMetric] = {}
    for r in rows or []:
        pid = int(r["product_id"])
        spend_7d = float(spend_7d_by_pid.get(pid, 0) or 0)
        spend_30d = float(spend_30d_by_pid.get(pid, 0) or 0)
        rev_7d = _safe_float(r.get("revenue_7d"))
        rev_30d = _safe_float(r.get("revenue_30d"))
        profit_7d = (
            rev_7d - _safe_float(r.get("fee_7d")) - _safe_float(r.get("purchase_7d"))
            - _safe_float(r.get("shipping_7d")) - _safe_float(r.get("rr_7d")) - spend_7d
        )
        profit_30d = (
            rev_30d - _safe_float(r.get("fee_30d")) - _safe_float(r.get("purchase_30d"))
            - _safe_float(r.get("shipping_30d")) - _safe_float(r.get("rr_30d")) - spend_30d
        )
        out[pid] = WindowMetric(
            product_id=pid,
            product_code=str(r.get("product_code") or ""),
            product_name=str(r.get("product_name") or ""),
            product_main_image=r.get("product_main_image"),
            revenue_7d=round(rev_7d, 2),
            profit_7d=round(profit_7d, 2),
            revenue_30d=round(rev_30d, 2),
            profit_30d=round(profit_30d, 2),
            spend_7d=round(spend_7d, 2),
            active_days=active_by_pid.get(pid, 0),
            has_estimated_cost=bool(r.get("has_estimated")),
            first_active_date=r.get("first_active_date"),
            last_active_date=r.get("last_active_date"),
        )
    return out
