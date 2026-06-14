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
