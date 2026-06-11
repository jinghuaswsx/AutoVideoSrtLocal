"""广告预警模块核心逻辑。

基于 media_product_lang_ad_summary_cache 判断低 ROAS 仍在投放的广告，
提供趋势数据查询和规则引擎研判结论。
Docs anchor: docs/superpowers/specs/2026-06-11-ad-alert-module-design.md
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from appcore import settings as system_settings
from appcore.db import query, query_one

log = logging.getLogger(__name__)

ALERT_THRESHOLD_SETTING_KEY = "ad_alert_roas_threshold"
DEFAULT_THRESHOLD = 1.5


class Severity(str, Enum):
    SEVERE = "severe"
    MODERATE = "moderate"
    MILD = "mild"


SEVERITY_LABELS = {
    Severity.SEVERE: "严重",
    Severity.MODERATE: "中度",
    Severity.MILD: "轻度",
}


class TrendDirection(str, Enum):
    WORSENING = "worsening"
    STABLE = "stable"
    IMPROVING = "improving"


TREND_LABELS = {
    TrendDirection.WORSENING: "恶化",
    TrendDirection.STABLE: "持平",
    TrendDirection.IMPROVING: "改善",
}


class Phase(str, Enum):
    LEARNING = "learning"
    STABLE = "stable"


PHASE_LABELS = {
    Phase.LEARNING: "学习期",
    Phase.STABLE: "稳定期",
}


@dataclass
class Judgment:
    severity: Severity
    trend: TrendDirection
    phase: Phase
    conclusion: str
    reason: str


@dataclass
class AlertItem:
    product_id: int
    product_code: str
    product_name: str
    lang: str
    store_codes: list[str]
    ad_spend_usd: float
    purchase_value_usd: float
    ad_roas: float | None
    active_7d_ad_spend_usd: float
    delivery_status: str
    ad_roas_7d: float | None
    computed_at: str | None
    severity: Severity
    trend: TrendDirection
    phase: Phase
    conclusion: str
    reason: str
    estimated_loss: float
    active_days: int = 0


@dataclass
class DailyPoint:
    date: str
    spend_usd: float = 0.0
    purchase_value_usd: float = 0.0
    roas: float | None = None


@dataclass
class ActiveWindow:
    delivery_start: str | None
    delivery_end: str | None
    active_days: int


@dataclass
class AlertDetail:
    product_id: int
    product_code: str
    product_name: str
    lang: str
    lang_label: str
    store_codes: list[str]
    ad_spend_usd: float
    purchase_value_usd: float
    ad_roas: float | None
    active_7d_ad_spend_usd: float
    estimated_loss: float
    delivery_start_time: str | None
    delivery_end_time: str | None
    active_days: int
    computed_at: str | None
    judgment: Judgment
    trend: list[DailyPoint] = field(default_factory=list)


_LANG_LABELS: dict[str, str] = {
    "en": "英语",
    "de": "德语",
    "fr": "法语",
    "es": "西班牙语",
    "it": "意大利语",
    "nl": "荷兰语",
    "sv": "瑞典语",
    "fi": "芬兰语",
    "ja": "日语",
    "ko": "韩语",
    "pt": "葡萄牙语",
    "pt-br": "巴西葡语",
    "zh": "中文",
}


_COUNTRY_LANG_CASE_SQL = """CASE UPPER(%s)
           WHEN 'US' THEN 'en'
           WHEN 'GB' THEN 'en'
           WHEN 'UK' THEN 'en'
           WHEN 'AU' THEN 'en'
           WHEN 'CA' THEN 'en'
           WHEN 'IE' THEN 'en'
           WHEN 'NZ' THEN 'en'
           WHEN 'DE' THEN 'de'
           WHEN 'AT' THEN 'de'
           WHEN 'FR' THEN 'fr'
           WHEN 'ES' THEN 'es'
           WHEN 'IT' THEN 'it'
           WHEN 'NL' THEN 'nl'
           WHEN 'SE' THEN 'sv'
           WHEN 'FI' THEN 'fi'
           WHEN 'JP' THEN 'ja'
           WHEN 'KR' THEN 'ko'
           WHEN 'BR' THEN 'pt-br'
           WHEN 'PT' THEN 'pt'
           ELSE NULL
         END"""


def get_threshold() -> float:
    """读取预警 ROAS 阈值配置。"""
    try:
        raw = system_settings.get_setting(ALERT_THRESHOLD_SETTING_KEY)
        if not raw:
            return DEFAULT_THRESHOLD
        parsed = json.loads(raw)
        threshold = float(parsed.get("threshold", DEFAULT_THRESHOLD))
        return max(0.1, threshold)
    except (TypeError, ValueError, json.JSONDecodeError):
        return DEFAULT_THRESHOLD


def set_threshold(value: float) -> None:
    """写入预警 ROAS 阈值配置。"""
    threshold = max(0.1, float(value))
    payload = json.dumps({"threshold": threshold}, ensure_ascii=False)
    system_settings.set_setting(ALERT_THRESHOLD_SETTING_KEY, payload)


def get_alerts(
    threshold: float | None = None,
    lang: str | None = None,
    severity: Severity | None = None,
    search: str | None = None,
) -> list[AlertItem]:
    """查询低 ROAS 且仍有活跃消耗的商品语言预警列表。"""
    threshold_value = _normalize_threshold(threshold)
    conditions = [
        "c.ad_roas IS NOT NULL",
        "c.ad_roas < %(threshold)s",
        "c.active_7d_ad_spend_usd > 0",
        "c.ad_spend_usd > 0",
    ]
    params: dict[str, Any] = {"threshold": threshold_value}

    if lang:
        conditions.append("c.lang = %(lang)s")
        params["lang"] = lang.strip().lower()

    if search:
        conditions.append("(p.product_code LIKE %(search)s OR p.name LIKE %(search)s)")
        params["search"] = f"%{search.strip()}%"

    where_clause = " AND ".join(conditions)
    rows = query(
        f"""
        SELECT c.product_id, c.lang, c.ad_spend_usd, c.purchase_value_usd,
               c.ad_roas, c.active_7d_ad_spend_usd, c.computed_at,
               p.product_code, p.name AS product_name, p.store_code
        FROM media_product_lang_ad_summary_cache c
        JOIN media_products p ON p.id = c.product_id AND p.deleted_at IS NULL
        WHERE {where_clause}
        ORDER BY c.ad_roas ASC, c.active_7d_ad_spend_usd DESC
        """,
        params,
    )

    items: list[AlertItem] = []
    for row in rows:
        product_id = int(row["product_id"])
        item_lang = _safe_str(row.get("lang")).lower()
        roas = _safe_float(row.get("ad_roas"))
        spend = _safe_float(row.get("ad_spend_usd"))
        purchase = _safe_float(row.get("purchase_value_usd"))
        active_spend = _safe_float(row.get("active_7d_ad_spend_usd"))
        active_window = _get_active_window(product_id, item_lang)
        recent_7d_roas, prior_7d_roas = _alert_trend_inputs(product_id, item_lang)
        judgment = judge_alert(
            roas,
            recent_7d_roas,
            [],
            prior_7d=prior_7d_roas,
            active_days=active_window.active_days,
        )
        if severity and judgment.severity != severity:
            continue
        items.append(
            AlertItem(
                product_id=product_id,
                product_code=_safe_str(row.get("product_code")),
                product_name=_safe_str(row.get("product_name")),
                lang=item_lang,
                store_codes=[_safe_str(row.get("store_code"))] if row.get("store_code") else [],
                ad_spend_usd=spend,
                purchase_value_usd=purchase,
                ad_roas=roas,
                active_7d_ad_spend_usd=active_spend,
                delivery_status="active" if active_spend > 0 else "stopped",
                ad_roas_7d=recent_7d_roas,
                computed_at=_iso(row.get("computed_at")),
                severity=judgment.severity,
                trend=judgment.trend,
                phase=judgment.phase,
                conclusion=judgment.conclusion,
                reason=judgment.reason,
                estimated_loss=_estimated_loss(purchase, spend),
                active_days=active_window.active_days,
            )
        )
    return items


def get_alert_detail(
    product_id: int,
    lang: str,
    threshold: float | None = None,
) -> AlertDetail | None:
    """查询单条预警详情，包含累计数据、投放时长和近 30 天趋势。"""
    _normalize_threshold(threshold)
    lower_lang = lang.strip().lower()
    row = query_one(
        """
        SELECT c.product_id, c.lang, c.ad_spend_usd, c.purchase_value_usd,
               c.ad_roas, c.active_7d_ad_spend_usd, c.computed_at,
               p.product_code, p.name AS product_name, p.store_code
        FROM media_product_lang_ad_summary_cache c
        JOIN media_products p ON p.id = c.product_id AND p.deleted_at IS NULL
        WHERE c.product_id = %(product_id)s AND c.lang = %(lang)s
        """,
        {"product_id": product_id, "lang": lower_lang},
    )
    if not row:
        return None

    trend_series = get_trend_series(product_id, lower_lang)
    active_window = _get_active_window(product_id, lower_lang)
    recent_7d = _avg_roas(trend_series, 0, 7)
    prior_7d = _avg_roas(trend_series, 7, 7)

    roas = _safe_float(row.get("ad_roas"))
    spend = _safe_float(row.get("ad_spend_usd"))
    purchase = _safe_float(row.get("purchase_value_usd"))
    judgment = judge_alert(
        roas,
        recent_7d,
        trend_series,
        prior_7d=prior_7d,
        active_days=active_window.active_days,
    )

    return AlertDetail(
        product_id=int(row["product_id"]),
        product_code=_safe_str(row.get("product_code")),
        product_name=_safe_str(row.get("product_name")),
        lang=lower_lang,
        lang_label=_lang_label(lower_lang),
        store_codes=[_safe_str(row.get("store_code"))] if row.get("store_code") else [],
        ad_spend_usd=spend,
        purchase_value_usd=purchase,
        ad_roas=roas,
        active_7d_ad_spend_usd=_safe_float(row.get("active_7d_ad_spend_usd")),
        estimated_loss=_estimated_loss(purchase, spend),
        delivery_start_time=active_window.delivery_start,
        delivery_end_time=active_window.delivery_end,
        active_days=active_window.active_days,
        computed_at=_iso(row.get("computed_at")),
        judgment=judgment,
        trend=trend_series[:14],
    )


def get_trend_series(
    product_id: int,
    lang: str,
    days: int = 30,
) -> list[DailyPoint]:
    """查询近 N 天商品语言维度广告花费和购买价值趋势。"""
    lower_lang = lang.strip().lower()
    rows = query(
        f"""
        SELECT
          DATE(COALESCE(m.meta_business_date, m.report_date)) AS ad_date,
          COALESCE(SUM(COALESCE(m.spend_usd, 0)), 0) AS spend_usd,
          COALESCE(SUM(COALESCE(m.purchase_value_usd, 0)), 0) AS purchase_value_usd
        FROM meta_ad_daily_ad_metrics m
        JOIN media_items i
          ON i.product_id = m.product_id
         AND i.deleted_at IS NULL
         AND LOWER(i.lang) = %(lang)s
         AND (
           m.ad_name LIKE CONCAT('%%', i.filename, '%%')
           OR m.normalized_ad_code LIKE CONCAT('%%', i.filename, '%%')
           OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.ad_name LIKE CONCAT('%%', i.display_name, '%%'))
           OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.normalized_ad_code LIKE CONCAT('%%', i.display_name, '%%'))
           OR (
             m.market_country IS NOT NULL
             AND m.market_country <> ''
             AND LOWER(i.lang) = {_COUNTRY_LANG_CASE_SQL % "m.market_country"}
           )
         )
        WHERE m.product_id = %(product_id)s
          AND COALESCE(m.spend_usd, 0) > 0
          AND DATE(COALESCE(m.meta_business_date, m.report_date)) >= DATE_SUB(CURDATE(), INTERVAL %(days)s DAY)
          AND DATE(COALESCE(m.meta_business_date, m.report_date)) < CURDATE()
        GROUP BY ad_date
        ORDER BY ad_date DESC
        """,
        {"product_id": product_id, "lang": lower_lang, "days": max(1, int(days))},
    )

    series: list[DailyPoint] = []
    for row in rows:
        ad_date = _iso_date(row.get("ad_date"))
        if not ad_date:
            continue
        spend = _safe_float(row.get("spend_usd"))
        purchase = _safe_float(row.get("purchase_value_usd"))
        roas = round(purchase / spend, 4) if spend > 0.01 else None
        series.append(
            DailyPoint(
                date=ad_date,
                spend_usd=round(spend, 2),
                purchase_value_usd=round(purchase, 2),
                roas=roas,
            )
        )
    return series


def judge_alert(
    roas: float,
    recent_7d_roas: float | None,
    trend_series: list[DailyPoint],
    *,
    prior_7d: float | None = None,
    active_days: int | None = None,
) -> Judgment:
    """基于 ROAS、趋势和运行阶段给出确定性研判结论。"""
    del trend_series
    if roas < 1.0:
        severity = Severity.SEVERE
    elif roas < 1.3:
        severity = Severity.MODERATE
    else:
        severity = Severity.MILD

    trend = TrendDirection.STABLE
    if prior_7d is not None and recent_7d_roas is not None and prior_7d > 0.01:
        ratio = recent_7d_roas / prior_7d
        if ratio < 0.9:
            trend = TrendDirection.WORSENING
        elif ratio > 1.1:
            trend = TrendDirection.IMPROVING

    phase = Phase.LEARNING if active_days is not None and active_days < 7 else Phase.STABLE

    if severity == Severity.SEVERE and phase == Phase.STABLE:
        conclusion = "建议关停"
        reason = "ROAS 低于 1.0 且已运行超过 7 天，持续亏损，建议尽快关停止损"
    elif phase == Phase.LEARNING:
        conclusion = "建议观察"
        reason = "广告尚在 Meta 学习期（不到 7 天），ROAS 数据尚不稳定，可再观察几天"
    elif severity in (Severity.MODERATE, Severity.MILD) and trend == TrendDirection.WORSENING:
        conclusion = "建议优化"
        reason = "ROAS 偏低且近期趋势持续恶化，建议优化广告素材或调整受众定向"
    else:
        conclusion = "建议暂缓"
        reason = "ROAS 虽低于阈值但近期有回升迹象，可暂缓关停继续观察"

    return Judgment(
        severity=severity,
        trend=trend,
        phase=phase,
        conclusion=conclusion,
        reason=reason,
    )


def _alert_trend_inputs(product_id: int, lang: str) -> tuple[float | None, float | None]:
    series = get_trend_series(product_id, lang, days=14)
    return _avg_roas(series, 0, 7), _avg_roas(series, 7, 7)


def _get_active_window(product_id: int, lang: str) -> ActiveWindow:
    row = query_one(
        f"""
        SELECT
          MIN(DATE(COALESCE(m.meta_business_date, m.report_date))) AS delivery_start,
          MAX(DATE(COALESCE(m.meta_business_date, m.report_date))) AS delivery_end,
          COUNT(DISTINCT DATE(COALESCE(m.meta_business_date, m.report_date))) AS active_days
        FROM meta_ad_daily_ad_metrics m
        JOIN media_items i
          ON i.product_id = m.product_id
         AND i.deleted_at IS NULL
         AND LOWER(i.lang) = %(lang)s
         AND (
           m.ad_name LIKE CONCAT('%%', i.filename, '%%')
           OR m.normalized_ad_code LIKE CONCAT('%%', i.filename, '%%')
           OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.ad_name LIKE CONCAT('%%', i.display_name, '%%'))
           OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.normalized_ad_code LIKE CONCAT('%%', i.display_name, '%%'))
           OR (
             m.market_country IS NOT NULL
             AND m.market_country <> ''
             AND LOWER(i.lang) = {_COUNTRY_LANG_CASE_SQL % "m.market_country"}
           )
         )
        WHERE m.product_id = %(product_id)s
          AND COALESCE(m.spend_usd, 0) > 0
        """,
        {"product_id": product_id, "lang": lang.strip().lower()},
    )
    if not row:
        return ActiveWindow(None, None, 0)
    return ActiveWindow(
        delivery_start=_iso(row.get("delivery_start")),
        delivery_end=_iso(row.get("delivery_end")),
        active_days=int(_safe_float(row.get("active_days"))),
    )


def _avg_roas(series: list[DailyPoint], start: int, length: int) -> float | None:
    segment = series[start:start + length]
    if not segment:
        return None
    total_spend = sum(point.spend_usd for point in segment)
    total_purchase = sum(point.purchase_value_usd for point in segment)
    if total_spend <= 0.01:
        return None
    return round(total_purchase / total_spend, 4)


def _normalize_threshold(value: float | None) -> float:
    if value is None or value <= 0:
        return get_threshold()
    return max(0.1, float(value))


def _estimated_loss(purchase: float, spend: float) -> float:
    return round(purchase - spend, 2)


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _iso(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value:
        return str(value)
    return None


def _iso_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value:
        return str(value)[:10]
    return None


def _lang_label(code: str) -> str:
    return _LANG_LABELS.get(code.lower(), code.upper())
