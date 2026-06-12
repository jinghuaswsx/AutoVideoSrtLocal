"""广告预警模块核心逻辑。

基于 media_product_lang_ad_summary_cache 判断低 ROAS 仍在投放的广告，
提供趋势数据查询和规则引擎研判结论。
Docs anchors:
- docs/superpowers/specs/2026-06-11-ad-alert-module-design.md
- docs/superpowers/specs/2026-06-12-ad-alert-problem-ads-subtabs-design.md
- docs/superpowers/specs/2026-06-12-ad-alert-ad-level-design.md
- docs/superpowers/specs/2026-06-12-ad-alert-top-losing-ads-design.md
- docs/superpowers/specs/2026-06-12-ad-alert-review-remediation.md
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from urllib.parse import urlencode

from appcore import settings as system_settings
from appcore.db import query, query_one
from appcore.order_analytics._helpers import current_meta_business_date

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
    top_losing_ads: list[AdListItem] = field(default_factory=list)


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


@dataclass
class ProblemMetric:
    spend_usd: float
    result_count: int
    roas: float | None


@dataclass
class ProblemAdItem:
    level: str
    code: str
    name: str
    ad_account_id: str
    ad_account_name: str
    first_active_date: str | None
    last_active_date: str | None
    detail_url: str
    metrics: dict[str, ProblemMetric]
    product_cn_name: str | None = None
    product_theme: str | None = None
    product_main_image: str | None = None



@dataclass
class AggregatedProductAlert:
    product_id: int
    product_code: str
    product_name: str
    store_codes: list[str]
    ad_spend_usd: float
    purchase_value_usd: float
    ad_roas: float | None
    active_7d_ad_spend_usd: float
    estimated_loss: float
    max_severity: str
    max_severity_label: str
    alert_languages: list[dict[str, Any]]
    alert_count: int
    active_days: int
    computed_at: str | None
    top_losing_ads: list[AdListItem] = field(default_factory=list)
    evaluation_lang: str | None = None


@dataclass
class AdListItem:
    """单个 AD 级别的投放数据。"""

    country: str
    ad_name: str
    normalized_ad_code: str
    total_spend: float
    total_purchase: float
    ad_roas: float | None
    active_days: int


@dataclass
class AdEvaluation:
    """Gemini 对单个 AD 的评估结论。"""

    country: str
    ad_name: str
    roas: float
    judgment: str
    reason: str



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


_COUNTRY_LABELS: dict[str, str] = {
    "US": "美国",
    "GB": "英国",
    "UK": "英国",
    "AU": "澳大利亚",
    "CA": "加拿大",
    "IE": "爱尔兰",
    "NZ": "新西兰",
    "DE": "德国",
    "AT": "奥地利",
    "FR": "法国",
    "ES": "西班牙",
    "IT": "意大利",
    "NL": "荷兰",
    "SE": "瑞典",
    "FI": "芬兰",
    "JP": "日本",
    "KR": "韩国",
    "BR": "巴西",
    "PT": "葡萄牙",
}


_PROBLEM_LEVEL_CONFIG: dict[str, dict[str, str]] = {
    "campaign": {
        "daily_table": "meta_ad_daily_campaign_metrics",
        "daily_code_col": "normalized_campaign_code",
        "daily_name_col": "campaign_name",
        "realtime_table": "meta_ad_realtime_daily_campaign_metrics",
        "realtime_code_col": "normalized_campaign_code",
        "realtime_name_col": "campaign_name",
    },
    "adset": {
        "daily_table": "meta_ad_daily_adset_metrics",
        "daily_code_col": "normalized_adset_code",
        "daily_name_col": "adset_name",
        "realtime_table": "meta_ad_realtime_daily_adset_metrics",
        "realtime_code_col": "normalized_adset_code",
        "realtime_name_col": "adset_name",
    },
    "ad": {
        "daily_table": "meta_ad_daily_ad_metrics",
        "daily_code_col": "normalized_ad_code",
        "daily_name_col": "ad_name",
        "realtime_table": "meta_ad_realtime_daily_ad_metrics",
        "realtime_code_col": "normalized_ad_code",
        "realtime_name_col": "ad_name",
    },
}


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


def _get_top_losing_ads(
    product_id: int,
    lang: str,
    threshold: float,
    limit: int = 3,
) -> list[AdListItem]:
    """获取某商品语言下亏损最严重的 AD，按 ROAS 升序返回。"""
    all_ads = get_ad_list(product_id, lang)
    losing_ads = [
        ad for ad in all_ads
        if ad.ad_roas is not None and ad.ad_roas < threshold
    ]
    losing_ads.sort(key=lambda ad: ad.ad_roas if ad.ad_roas is not None else 999)
    return losing_ads[:max(0, int(limit))]


def _get_alerts_dynamically(
    start_date: str,
    end_date: str,
    threshold_value: float,
    lang: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """在指定时间范围内动态聚合各产品与语言的广告指标。"""
    today_str = date.today().isoformat()
    
    daily_sql = f"""
        SELECT DISTINCT
          i.product_id,
          i.lang,
          CONCAT('daily:', m.id) AS metric_id,
          COALESCE(m.spend_usd, 0) AS spend_usd,
          COALESCE(m.purchase_value_usd, 0) AS purchase_value_usd,
          COALESCE(m.meta_business_date, m.report_date) AS activity_date
        FROM media_items i
        JOIN media_products p ON p.id = i.product_id AND p.deleted_at IS NULL AND p.archived = 0
        JOIN media_languages ml ON ml.code = i.lang AND ml.enabled = 1
        JOIN meta_ad_daily_ad_metrics m
          ON m.product_id = i.product_id
         AND COALESCE(m.spend_usd, 0) > 0
         AND DATE(COALESCE(m.meta_business_date, m.report_date)) >= %(start_date)s
         AND DATE(COALESCE(m.meta_business_date, m.report_date)) <= %(end_date)s
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
        WHERE i.deleted_at IS NULL
    """

    realtime_sql = f"""
        SELECT DISTINCT
          i.product_id,
          i.lang,
          CONCAT('realtime:', m.id) AS metric_id,
          COALESCE(m.spend_usd, 0) AS spend_usd,
          COALESCE(m.purchase_value_usd, 0) AS purchase_value_usd,
          m.business_date AS activity_date
        FROM media_items i
        JOIN media_products p ON p.id = i.product_id AND p.deleted_at IS NULL AND p.archived = 0
        JOIN media_languages ml ON ml.code = i.lang AND ml.enabled = 1
        JOIN (
          SELECT rt.*
          FROM meta_ad_realtime_daily_ad_metrics rt
          INNER JOIN (
            SELECT ad_account_id, MAX(business_date) AS business_date, MAX(snapshot_at) AS max_snapshot_at
            FROM meta_ad_realtime_daily_ad_metrics
            WHERE data_completeness = 'realtime_partial'
              AND business_date >= %(start_date)s
              AND business_date <= %(end_date)s
            GROUP BY ad_account_id, business_date
          ) latest
            ON rt.business_date = latest.business_date
           AND rt.ad_account_id = latest.ad_account_id
           AND rt.snapshot_at = latest.max_snapshot_at
          WHERE rt.data_completeness = 'realtime_partial'
            AND COALESCE(rt.spend_usd, 0) > 0
        ) m
          ON p.product_code IS NOT NULL
         AND p.product_code <> ''
         AND (
           LOWER(COALESCE(m.normalized_campaign_code, '')) LIKE CONCAT(LOWER(p.product_code), '%%')
           OR LOWER(COALESCE(m.campaign_name, '')) LIKE CONCAT(LOWER(p.product_code), '%%')
           OR LOWER(COALESCE(m.normalized_ad_code, '')) LIKE CONCAT(LOWER(p.product_code), '%%')
           OR LOWER(COALESCE(m.ad_name, '')) LIKE CONCAT(LOWER(p.product_code), '%%')
         )
         AND (
           m.ad_name LIKE CONCAT('%%', i.filename, '%%')
           OR m.normalized_ad_code LIKE CONCAT('%%', i.filename, '%%')
           OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.ad_name LIKE CONCAT('%%', i.display_name, '%%'))
           OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.normalized_ad_code LIKE CONCAT('%%', i.display_name, '%%'))
           OR (
             LOWER(i.lang) = CASE
               WHEN m.country_code IS NOT NULL AND m.country_code <> '' THEN
                 CASE UPPER(m.country_code)
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
                 END
               ELSE NULL
             END
           )
         )
        WHERE i.deleted_at IS NULL
    """

    if end_date >= today_str and _has_realtime_ad_table():
        combined_source = f"({daily_sql} UNION ALL {realtime_sql}) matched"
    else:
        combined_source = f"({daily_sql}) matched"

    where_conditions = []
    sql_params = {
        "start_date": start_date,
        "end_date": end_date,
        "threshold": threshold_value,
    }
    if lang:
        where_conditions.append("matched.lang = %(lang)s")
        sql_params["lang"] = lang.strip().lower()
    if search:
        where_conditions.append("(p.product_code LIKE %(search)s OR p.name LIKE %(search)s)")
        sql_params["search"] = f"%{search.strip()}%"

    where_clause = " AND ".join(where_conditions)
    if where_clause:
        where_clause = f"AND {where_clause}"

    query_str = f"""
        SELECT 
          matched.product_id,
          matched.lang,
          SUM(matched.spend_usd) AS ad_spend_usd,
          SUM(matched.purchase_value_usd) AS purchase_value_usd,
          CASE
            WHEN SUM(matched.spend_usd) > 0
            THEN ROUND(SUM(matched.purchase_value_usd) / SUM(matched.spend_usd), 4)
            ELSE NULL
          END AS ad_roas,
          SUM(
            CASE
              WHEN matched.activity_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
              THEN matched.spend_usd
              ELSE 0
            END
          ) AS active_7d_ad_spend_usd,
          MAX(matched.activity_date) AS computed_at,
          p.product_code,
          p.name AS product_name
        FROM {combined_source}
        JOIN media_products p ON p.id = matched.product_id AND p.deleted_at IS NULL AND p.archived = 0
        JOIN media_product_lang_ad_summary_cache lc 
          ON lc.product_id = matched.product_id 
         AND lc.lang = matched.lang
         AND lc.active_7d_ad_spend_usd > 0
        WHERE 1=1 {where_clause}
        GROUP BY matched.product_id, matched.lang, p.product_code, p.name
        HAVING SUM(matched.spend_usd) > 0 
           AND (SUM(matched.purchase_value_usd) / SUM(matched.spend_usd) < %(threshold)s)
        ORDER BY ad_roas ASC, active_7d_ad_spend_usd DESC
    """
    return query(query_str, sql_params)


def get_alerts(
    threshold: float | None = None,
    lang: str | None = None,
    severity: Severity | None = None,
    search: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[AlertItem]:
    """查询低 ROAS 且仍有活跃消耗的商品语言预警列表（支持时间范围选择与亏损过滤）。"""
    threshold_value = _normalize_threshold(threshold)
    
    if start_date and end_date:
        rows = _get_alerts_dynamically(
            start_date=start_date,
            end_date=end_date,
            threshold_value=threshold_value,
            lang=lang,
            search=search,
        )
    else:
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
                   p.product_code, p.name AS product_name
            FROM media_product_lang_ad_summary_cache c
            JOIN media_products p ON p.id = c.product_id AND p.deleted_at IS NULL AND p.archived = 0
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
        if end_date is not None:
            recent_7d_roas, prior_7d_roas = _alert_trend_inputs(product_id, item_lang, end_date)
        else:
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

        estimated_loss = _estimated_loss(purchase, spend)
        is_loss = estimated_loss < -0.01
        is_worsening_and_huge_spend = (
            judgment.trend == TrendDirection.WORSENING
            and (active_spend >= 100.0 or spend >= 300.0)
        )
        if not (is_loss or is_worsening_and_huge_spend):
            continue

        top_losing_ads = _get_top_losing_ads(
            product_id,
            item_lang,
            threshold_value,
            limit=3,
        )
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
                estimated_loss=estimated_loss,
                active_days=active_window.active_days,
                top_losing_ads=top_losing_ads,
            )
        )
    return items


def get_alert_detail(
    product_id: int,
    lang: str,
    threshold: float | None = None,
) -> AlertDetail | None:
    """查询单条预警详情，包含累计数据、投放时长和近 30 天趋势。"""
    threshold_value = _normalize_threshold(threshold)
    lower_lang = lang.strip().lower()
    row = query_one(
        """
        SELECT c.product_id, c.lang, c.ad_spend_usd, c.purchase_value_usd,
               c.ad_roas, c.active_7d_ad_spend_usd, c.computed_at,
               p.product_code, p.name AS product_name
        FROM media_product_lang_ad_summary_cache c
        JOIN media_products p ON p.id = c.product_id AND p.deleted_at IS NULL AND p.archived = 0
        WHERE c.product_id = %(product_id)s AND c.lang = %(lang)s
          AND c.ad_roas IS NOT NULL
          AND c.ad_roas < %(threshold)s
          AND c.active_7d_ad_spend_usd > 0
          AND c.ad_spend_usd > 0
        """,
        {"product_id": product_id, "lang": lower_lang, "threshold": threshold_value},
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


def get_problem_ads(
    level: str,
    *,
    search: str | None = None,
    limit: int = 200,
) -> tuple[date, list[ProblemAdItem]]:
    """查询今天有消耗但成效为 0 的广告，并聚合多时间窗口指标。

    Docs-anchor: docs/superpowers/specs/2026-06-12-ad-alert-problem-ads-subtabs-design.md
    """
    cfg = _problem_level_config(level)
    business_date = current_meta_business_date()
    yesterday = business_date - timedelta(days=1)
    last_7d_start = business_date - timedelta(days=6)
    last_30d_start = business_date - timedelta(days=29)
    safe_limit = max(1, min(int(limit or 200), 500))

    search_clause = ""
    params: dict[str, Any] = {
        "today": business_date,
        "yesterday": yesterday,
        "last_7d_start": last_7d_start,
        "last_30d_start": last_30d_start,
        "limit": safe_limit,
    }
    if search:
        params["search"] = f"%{search.strip()}%"
        search_clause = (
            "AND (LOWER(s.name) LIKE LOWER(%(search)s) "
            "OR LOWER(s.code) LIKE LOWER(%(search)s) "
            "OR LOWER(COALESCE(s.matched_product_code, '')) LIKE LOWER(%(search)s)) "
        )

    source_sql = _problem_ads_source_sql(cfg)
    rows = query(
        f"""
        SELECT
          s.code,
          MAX(s.name) AS name,
          s.ad_account_id,
          MAX(s.ad_account_name) AS ad_account_name,
          MAX(s.matched_product_code) AS matched_product_code,
          MIN(s.metric_date) AS first_active_date,
          MAX(s.metric_date) AS last_active_date,
          SUM(CASE WHEN s.metric_date = %(today)s THEN COALESCE(s.spend_usd, 0) ELSE 0 END) AS today_spend_usd,
          SUM(CASE WHEN s.metric_date = %(today)s THEN COALESCE(s.purchase_value_usd, 0) ELSE 0 END) AS today_purchase_value_usd,
          SUM(CASE WHEN s.metric_date = %(today)s THEN COALESCE(s.result_count, 0) ELSE 0 END) AS today_result_count,
          SUM(CASE WHEN s.metric_date = %(yesterday)s THEN COALESCE(s.spend_usd, 0) ELSE 0 END) AS yesterday_spend_usd,
          SUM(CASE WHEN s.metric_date = %(yesterday)s THEN COALESCE(s.purchase_value_usd, 0) ELSE 0 END) AS yesterday_purchase_value_usd,
          SUM(CASE WHEN s.metric_date = %(yesterday)s THEN COALESCE(s.result_count, 0) ELSE 0 END) AS yesterday_result_count,
          SUM(CASE WHEN s.metric_date >= %(last_7d_start)s AND s.metric_date <= %(today)s THEN COALESCE(s.spend_usd, 0) ELSE 0 END) AS last_7d_spend_usd,
          SUM(CASE WHEN s.metric_date >= %(last_7d_start)s AND s.metric_date <= %(today)s THEN COALESCE(s.purchase_value_usd, 0) ELSE 0 END) AS last_7d_purchase_value_usd,
          SUM(CASE WHEN s.metric_date >= %(last_7d_start)s AND s.metric_date <= %(today)s THEN COALESCE(s.result_count, 0) ELSE 0 END) AS last_7d_result_count,
          SUM(CASE WHEN s.metric_date >= %(last_30d_start)s AND s.metric_date <= %(today)s THEN COALESCE(s.spend_usd, 0) ELSE 0 END) AS last_30d_spend_usd,
          SUM(CASE WHEN s.metric_date >= %(last_30d_start)s AND s.metric_date <= %(today)s THEN COALESCE(s.purchase_value_usd, 0) ELSE 0 END) AS last_30d_purchase_value_usd,
          SUM(CASE WHEN s.metric_date >= %(last_30d_start)s AND s.metric_date <= %(today)s THEN COALESCE(s.result_count, 0) ELSE 0 END) AS last_30d_result_count,
          SUM(COALESCE(s.spend_usd, 0)) AS overall_spend_usd,
          SUM(COALESCE(s.purchase_value_usd, 0)) AS overall_purchase_value_usd,
          SUM(COALESCE(s.result_count, 0)) AS overall_result_count
        FROM {source_sql} s
        JOIN (
          SELECT t.code, t.ad_account_id
          FROM {source_sql} t
          WHERE t.metric_date = %(today)s
          GROUP BY t.code, t.ad_account_id
          HAVING SUM(COALESCE(t.spend_usd, 0)) > 0
             AND SUM(COALESCE(t.result_count, 0)) = 0
        ) problem_today
          ON problem_today.code = s.code
         AND problem_today.ad_account_id <=> s.ad_account_id
        WHERE COALESCE(s.spend_usd, 0) > 0
          {search_clause}
        GROUP BY s.code, s.ad_account_id
        ORDER BY today_spend_usd DESC, last_7d_spend_usd DESC, overall_spend_usd DESC
        LIMIT %(limit)s
        """,
        params,
    )

    items: list[ProblemAdItem] = []
    for row in rows or []:
        code = _safe_str(row.get("code"))
        first_active = _iso_date(row.get("first_active_date")) or business_date.isoformat()
        last_active = _iso_date(row.get("last_active_date"))
        account_id = _safe_str(row.get("ad_account_id"))
        name = _safe_str(row.get("name")) or code
        items.append(
            ProblemAdItem(
                level=level,
                code=code,
                name=name,
                ad_account_id=account_id,
                ad_account_name=_safe_str(row.get("ad_account_name")),
                first_active_date=first_active,
                last_active_date=last_active,
                detail_url=_problem_detail_url(
                    level=level,
                    code=code,
                    name=name,
                    ad_account_id=account_id,
                    start_date=first_active,
                    end_date=business_date.isoformat(),
                ),
                metrics={
                    "today": _problem_metric(row, "today"),
                    "yesterday": _problem_metric(row, "yesterday"),
                    "last_7d": _problem_metric(row, "last_7d"),
                    "last_30d": _problem_metric(row, "last_30d"),
                    "overall": _problem_metric(row, "overall"),
                },
                product_cn_name=None,
                product_theme=None,
                product_main_image=None,
            )
        )

    try:
        _batch_fetch_problem_ad_details(items, rows)
    except Exception as e:
        log.exception("Failed to batch fetch problem ad details: %s", e)

    return business_date, items



def _batch_fetch_problem_ad_details(items: list[ProblemAdItem], rows: list[dict[str, Any]]) -> None:
    if not items:
        return

    # 1. Gather all product codes
    code_to_items: dict[str, list[ProblemAdItem]] = {}
    for item, row in zip(items, rows or []):
        matched = _safe_str(row.get("matched_product_code"))
        prod_code = (matched or item.code or "").strip().lower()
        if prod_code:
            if prod_code not in code_to_items:
                code_to_items[prod_code] = []
            code_to_items[prod_code].append(item)

    clean_codes = list(code_to_items.keys())
    if not clean_codes:
        return

    from appcore.db import query as db_query

    # 2. Batch fetch Chinese names
    try:
        from appcore.product_name_dictionary import get_names
        # pass db_query as the custom query_fn so that get_names queries through it
        names_dict = get_names(clean_codes, query_fn=db_query)
    except Exception as e:
        log.warning("Failed to import or call get_names: %s", e)
        names_dict = {}

    # 3. Fallback: query media_products for names
    placeholders = ",".join(["%s"] * len(clean_codes))
    try:
        rows_mp = db_query(
            f"""
            SELECT LOWER(product_code) AS code, name
            FROM media_products
            WHERE LOWER(product_code) IN ({placeholders})
              AND deleted_at IS NULL
            """,
            tuple(clean_codes)
        )
        for r in rows_mp or []:
            c = str(r["code"]).lower()
            name = str(r["name"] or "").strip()
            if c not in names_dict:
                names_dict[c] = {"cn_name": "", "en_name": ""}
            if not names_dict[c]["cn_name"] and name:
                names_dict[c]["cn_name"] = name
    except Exception as e:
        log.warning("Failed to query media_products for names: %s", e)

    # 4. Batch fetch product themes (categories)
    themes: dict[str, str] = {}
    # 4.1 From tabcut_goods
    try:
        rows_tabcut = db_query(
            f"""
            SELECT LOWER(product_code) AS code, COALESCE(category_l1_name_zh, category_l1_name) AS theme
            FROM tabcut_goods
            WHERE LOWER(product_code) IN ({placeholders})
              AND (category_l1_name_zh IS NOT NULL AND category_l1_name_zh <> ''
                   OR category_l1_name IS NOT NULL AND category_l1_name <> '')
            """,
            tuple(clean_codes)
        )
        for r in rows_tabcut or []:
            c = str(r["code"]).lower()
            t = str(r["theme"] or "").strip()
            if t:
                themes[c] = t
    except Exception as e:
        log.warning("Failed to query product themes from tabcut_goods: %s", e)

    # 4.2 From meta_hot_posts / meta_hot_post_product_analyses
    try:
        from appcore.meta_hot_posts.categories import category_label_zh
        rows_meta = db_query(
            f"""
            SELECT LOWER(mp.product_code) AS code, a.category_l1 AS theme
            FROM media_products mp
            JOIN meta_hot_posts hp ON hp.local_product_id = mp.id
            JOIN meta_hot_post_product_analyses a ON a.product_url_hash = hp.product_url_hash
            WHERE LOWER(mp.product_code) IN ({placeholders})
              AND a.category_l1 IS NOT NULL AND a.category_l1 <> ''
            """,
            tuple(clean_codes)
        )
        for r in rows_meta or []:
            c = str(r["code"]).lower()
            if c not in themes:
                raw_theme = str(r["theme"] or "").strip()
                if raw_theme:
                    themes[c] = category_label_zh(raw_theme)
    except Exception as e:
        log.warning("Failed to query product themes from meta_hot_posts: %s", e)

    # 4.3 From meta_hot_posts / meta_hot_post_product_analyses (images)
    images: dict[str, str] = {}
    # From media_products
    try:
        rows_mp_img = db_query(
            f"""
            SELECT LOWER(product_code) AS code, main_image
            FROM media_products
            WHERE LOWER(product_code) IN ({placeholders})
              AND main_image IS NOT NULL AND main_image <> ''
              AND deleted_at IS NULL
            """,
            tuple(clean_codes)
        )
        for r in rows_mp_img or []:
            c = str(r["code"]).lower()
            img = str(r["main_image"] or "").strip()
            if img:
                images[c] = img
    except Exception as e:
        log.warning("Failed to query product images from media_products: %s", e)

    # From media_product_covers
    try:
        rows_cov = db_query(
            f"""
            SELECT LOWER(mp.product_code) AS code, c.object_key AS main_image
            FROM media_products mp
            JOIN media_product_covers c ON c.product_id = mp.id
            WHERE LOWER(mp.product_code) IN ({placeholders})
              AND c.object_key IS NOT NULL AND c.object_key <> ''
              AND mp.deleted_at IS NULL
            ORDER BY (CASE WHEN c.lang = 'en' THEN 0 ELSE 1 END), c.updated_at DESC
            """,
            tuple(clean_codes)
        )
        for r in rows_cov or []:
            c = str(r["code"]).lower()
            if c not in images:
                img = str(r["main_image"] or "").strip()
                if img:
                    images[c] = img
    except Exception as e:
        log.warning("Failed to query product images from media_product_covers: %s", e)

    # From tabcut_goods
    try:
        rows_tabcut_img = db_query(
            f"""
            SELECT LOWER(product_code) AS code, item_pic_url AS main_image
            FROM tabcut_goods
            WHERE LOWER(product_code) IN ({placeholders})
              AND item_pic_url IS NOT NULL AND item_pic_url <> ''
            """,
            tuple(clean_codes)
        )
        for r in rows_tabcut_img or []:
            c = str(r["code"]).lower()
            if c not in images:
                img = str(r["main_image"] or "").strip()
                if img:
                    images[c] = img
    except Exception as e:
        log.warning("Failed to query product images from tabcut_goods: %s", e)

    # From meta_hot_posts
    try:
        rows_meta_img = db_query(
            f"""
            SELECT LOWER(mp.product_code) AS code, a.product_main_image_url AS main_image
            FROM media_products mp
            JOIN meta_hot_posts hp ON hp.local_product_id = mp.id
            JOIN meta_hot_post_product_analyses a ON a.product_url_hash = hp.product_url_hash
            WHERE LOWER(mp.product_code) IN ({placeholders})
              AND a.product_main_image_url IS NOT NULL AND a.product_main_image_url <> ''
            """,
            tuple(clean_codes)
        )
        for r in rows_meta_img or []:
            c = str(r["code"]).lower()
            if c not in images:
                img = str(r["main_image"] or "").strip()
                if img:
                    images[c] = img
    except Exception as e:
        log.warning("Failed to query product images from meta_hot_posts: %s", e)

    # Normalize image paths
    for c, img in list(images.items()):
        if img:
            img = img.strip()
            if not (img.startswith("http://") or img.startswith("https://") or img.startswith("/")):
                img = "/medias/obj/" + img
            images[c] = img

    # 5. Populate items
    for c, it_list in code_to_items.items():
        cn_name = names_dict.get(c, {}).get("cn_name") or ""
        theme = themes.get(c) or ""
        main_img = images.get(c) or ""
        for it in it_list:
            it.product_cn_name = cn_name or None
            it.product_theme = theme or None
            it.product_main_image = main_img or None


def _match_media_item(
    ad_name: str,
    ad_code: str,
    country: str,
    media_items: list[dict],
    lower_lang: str,
) -> bool:
    """在 Python 内存中判断一个 ad 是否与 media_items 匹配。"""
    country_lang_map = {
        'US': 'en', 'GB': 'en', 'UK': 'en', 'AU': 'en', 'CA': 'en', 'IE': 'en', 'NZ': 'en',
        'DE': 'de', 'AT': 'de', 'FR': 'fr', 'ES': 'es', 'IT': 'it', 'NL': 'nl', 'SE': 'sv',
        'FI': 'fi', 'JP': 'ja', 'KR': 'ko', 'BR': 'pt-br', 'PT': 'pt'
    }
    if country and country_lang_map.get(country.upper()) == lower_lang:
        return True

    for item in media_items:
        fn = (item.get("filename") or "").strip()
        dn = (item.get("display_name") or "").strip()
        if fn and (fn in ad_name or fn in ad_code):
            return True
        if dn and (dn in ad_name or dn in ad_code):
            return True

    return False


def get_ad_list(product_id: int, lang: str) -> list[AdListItem]:
    """查询某个商品语言下每条 AD 的聚合投放数据。"""
    lower_lang = lang.strip().lower()
    if product_id <= 0 or not lower_lang:
        return []

    # 1. 预先查出该商品语言下的所有 media_items
    media_items = query(
        """
        SELECT filename, display_name
        FROM media_items
        WHERE product_id = %(product_id)s
          AND deleted_at IS NULL
          AND LOWER(lang) = %(lang)s
        """,
        {"product_id": product_id, "lang": lower_lang}
    )
    if not media_items:
        return []

    # 2. 预先查出该产品近 3 天活跃的 ad_code 集合，避免 EXISTS 子查询
    active_codes = set()
    active_daily = query(
        """
        SELECT DISTINCT COALESCE(NULLIF(TRIM(normalized_ad_code), ''), '') AS code
        FROM meta_ad_daily_ad_metrics
        WHERE product_id = %(product_id)s
          AND COALESCE(spend_usd, 0) > 0
          AND DATE(COALESCE(meta_business_date, report_date)) >= DATE_SUB(CURDATE(), INTERVAL 3 DAY)
        """,
        {"product_id": product_id}
    )
    for r in active_daily:
        c = r.get("code")
        if c:
            active_codes.add(c)

    if _has_realtime_ad_table():
        active_realtime = query(
            """
            SELECT DISTINCT COALESCE(NULLIF(TRIM(normalized_ad_code), ''), '') AS code
            FROM meta_ad_realtime_daily_ad_metrics
            WHERE COALESCE(spend_usd, 0) > 0
              AND business_date >= DATE_SUB(CURDATE(), INTERVAL 3 DAY)
            """
        )
        for r in active_realtime:
            c = r.get("code")
            if c:
                active_codes.add(c)

    # 如果近 3 天没有任何活跃的广告代码，直接返回空
    if not active_codes:
        return []

    # 3. 从数据库中极速查询该产品下的所有历史聚合广告记录
    rows = query(
        """
        SELECT
          UPPER(TRIM(m.market_country)) AS country,
          COALESCE(NULLIF(TRIM(m.ad_name), ''), NULLIF(TRIM(m.normalized_ad_code), ''), '') AS ad_name,
          COALESCE(NULLIF(TRIM(m.normalized_ad_code), ''), '') AS normalized_ad_code,
          COALESCE(SUM(COALESCE(m.spend_usd, 0)), 0) AS total_spend,
          COALESCE(SUM(COALESCE(m.purchase_value_usd, 0)), 0) AS total_purchase,
          COUNT(DISTINCT DATE(COALESCE(m.meta_business_date, m.report_date))) AS active_days
        FROM meta_ad_daily_ad_metrics m
        WHERE m.product_id = %(product_id)s
          AND COALESCE(m.spend_usd, 0) > 0
          AND m.market_country IS NOT NULL
          AND TRIM(m.market_country) <> ''
        """,
        {"product_id": product_id},
    )

    items: list[AdListItem] = []
    for row in rows:
        ad_name = _safe_str(row.get("ad_name"))
        ad_code = _safe_str(row.get("normalized_ad_code"))
        country = _safe_str(row.get("country")).upper()

        if ad_code not in active_codes:
            continue

        if not _match_media_item(ad_name, ad_code, country, media_items, lower_lang):
            continue

        spend = _safe_float(row.get("total_spend"))
        purchase = _safe_float(row.get("total_purchase"))
        roas = round(purchase / spend, 4) if spend > 0.01 else None
        items.append(
            AdListItem(
                country=country,
                ad_name=ad_name,
                normalized_ad_code=ad_code,
                total_spend=round(spend, 2),
                total_purchase=round(purchase, 2),
                ad_roas=roas,
                active_days=int(_safe_float(row.get("active_days"))),
            )
        )

    items.sort(
        key=lambda ad: (
            ad.ad_roas if ad.ad_roas is not None else 999,
            -ad.total_spend,
        )
    )
    return items

    items: list[AdListItem] = []
    for row in rows:
        spend = _safe_float(row.get("total_spend"))
        purchase = _safe_float(row.get("total_purchase"))
        roas = round(purchase / spend, 4) if spend > 0.01 else None
        items.append(
            AdListItem(
                country=_safe_str(row.get("country")).upper(),
                ad_name=_safe_str(row.get("ad_name")),
                normalized_ad_code=_safe_str(row.get("normalized_ad_code")),
                total_spend=round(spend, 2),
                total_purchase=round(purchase, 2),
                ad_roas=roas,
                active_days=int(_safe_float(row.get("active_days"))),
            )
        )
    return items


def evaluate_ads(
    product_id: int,
    lang: str,
    threshold: float | None = None,
    user_id: int | None = None,
) -> list[AdEvaluation] | None:
    """调用 Gemini 评估某商品语言下亏损 AD 的关停/优化/观察建议。"""
    lower_lang = lang.strip().lower()
    if product_id <= 0 or not lower_lang:
        return []

    threshold_value = _normalize_threshold(threshold)
    ad_list = get_ad_list(product_id, lower_lang)
    losing_ads = [
        ad for ad in ad_list
        if ad.ad_roas is not None and ad.ad_roas < threshold_value
    ]
    if not losing_ads:
        return []

    product_row = query_one(
        "SELECT product_code, name FROM media_products WHERE id = %(product_id)s AND deleted_at IS NULL",
        {"product_id": product_id},
    )
    product_code = _safe_str(product_row.get("product_code")) if product_row else str(product_id)
    product_name = _safe_str(product_row.get("name")) if product_row else product_code
    lang_label = _lang_label(lower_lang)
    ad_list_text = "\n".join(_format_ad_for_prompt(ad) for ad in losing_ads)

    from appcore import llm_client

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个 Meta 广告优化分析师。你的任务是根据广告投放数据分析一组广告的表现，"
                "给出每条广告的关停建议。重点关注 ROAS 低于保本线但仍持续消耗的广告。\n\n"
                "输出格式必须是纯 JSON 数组，不要 markdown 包裹，不要额外说明文字。"
                "数组中每个元素必须包含 country、ad_name、roas、judgment、reason。"
                "judgment 只能是“关停”、“优化”或“观察”。reason 用简短中文说明。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"以下是商品「{product_name}」(编码: {product_code}) 在 {lang_label} "
                f"语言下的广告投放数据，保本 ROAS 为 {threshold_value:.2f}。"
                "请分析并给出建议。\n\n"
                f"广告列表：\n{ad_list_text}"
            ),
        },
    ]

    try:
        result = llm_client.invoke_chat(
            "ad_alert.evaluate",
            messages=messages,
            user_id=user_id,
            project_id=f"ad-alert:{product_id}:{lower_lang}",
            temperature=0.1,
            max_tokens=1200,
            billing_extra={
                "product_id": product_id,
                "lang": lower_lang,
                "ad_count": len(losing_ads),
            },
        )
    except Exception:
        log.warning("ad_alert.evaluate LLM call failed", exc_info=True)
        return None

    evaluations = _parse_ad_evaluations(result.get("json") or result.get("text"))
    if evaluations is None:
        log.warning("ad_alert.evaluate failed to parse response")
    return evaluations



def get_trend_series(
    product_id: int,
    lang: str,
    days: int = 30,
    end_date: str | None = None,
) -> list[DailyPoint]:
    """查询近 N 天商品语言维度广告花费和购买价值趋势。"""
    lower_lang = lang.strip().lower()
    end_date_val = end_date if end_date else date.today().isoformat()

    # 1. 预先查出该商品语言下的所有 media_items
    media_items = query(
        """
        SELECT filename, display_name
        FROM media_items
        WHERE product_id = %(product_id)s
          AND deleted_at IS NULL
          AND LOWER(lang) = %(lang)s
        """,
        {"product_id": product_id, "lang": lower_lang}
    )
    if not media_items:
        return []

    # 2. 查出指定时间范围内的明细记录
    rows = query(
        """
        SELECT
          DATE(COALESCE(m.meta_business_date, m.report_date)) AS ad_date,
          UPPER(TRIM(m.market_country)) AS country,
          COALESCE(NULLIF(TRIM(m.ad_name), ''), NULLIF(TRIM(m.normalized_ad_code), ''), '') AS ad_name,
          COALESCE(NULLIF(TRIM(m.normalized_ad_code), ''), '') AS normalized_ad_code,
          COALESCE(m.spend_usd, 0) AS spend_usd,
          COALESCE(m.purchase_value_usd, 0) AS purchase_value_usd
        FROM meta_ad_daily_ad_metrics m
        WHERE m.product_id = %(product_id)s
          AND COALESCE(m.spend_usd, 0) > 0
          AND DATE(COALESCE(m.meta_business_date, m.report_date)) >= DATE_SUB(DATE(%(end_date_val)s), INTERVAL %(days)s DAY)
          AND DATE(COALESCE(m.meta_business_date, m.report_date)) < DATE(%(end_date_val)s)
        """,
        {
            "product_id": product_id,
            "days": max(1, int(days)),
            "end_date_val": end_date_val,
        },
    )

    # 3. 在 Python 内存中过滤并 Group By 聚合
    date_to_metrics: dict[str, list[float]] = {}
    for row in rows:
        ad_date = _iso_date(row.get("ad_date"))
        if not ad_date:
            continue
        ad_name = _safe_str(row.get("ad_name"))
        ad_code = _safe_str(row.get("normalized_ad_code"))
        country = _safe_str(row.get("country")).upper()

        if not _match_media_item(ad_name, ad_code, country, media_items, lower_lang):
            continue

        spend = _safe_float(row.get("spend_usd"))
        purchase = _safe_float(row.get("purchase_value_usd"))
        
        metrics = date_to_metrics.setdefault(ad_date, [0.0, 0.0])
        metrics[0] += spend
        metrics[1] += purchase

    series: list[DailyPoint] = []
    for ad_date in sorted(date_to_metrics.keys(), reverse=True):
        spend, purchase = date_to_metrics[ad_date]
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


def get_aggregated_products(
    threshold: float | None = None,
    lang: str | None = None,
    severity: Severity | None = None,
    search: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[AggregatedProductAlert]:
    """查询按产品维度聚合的广告预警列表。"""
    items = get_alerts(
        threshold=threshold,
        lang=lang,
        severity=None,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )

    # Group by product_id
    grouped: dict[int, list[AlertItem]] = {}
    for item in items:
        grouped.setdefault(item.product_id, []).append(item)

    res: list[AggregatedProductAlert] = []
    for pid, p_items in grouped.items():
        if severity and not any(it.severity == severity for it in p_items):
            continue

        first = p_items[0]
        total_spend = sum(it.ad_spend_usd for it in p_items)
        total_purchase = sum(it.purchase_value_usd for it in p_items)
        avg_roas = round(total_purchase / total_spend, 4) if total_spend > 0.01 else None
        total_active_7d_spend = sum(it.active_7d_ad_spend_usd for it in p_items)

        # Max severity ordering: severe > moderate > mild
        sev_order = {Severity.SEVERE: 3, Severity.MODERATE: 2, Severity.MILD: 1}
        max_item = max(p_items, key=lambda it: sev_order.get(it.severity, 0))

        stores_set = set()
        for it in p_items:
            for sc in it.store_codes:
                if sc:
                    stores_set.add(sc)

        alert_languages = [
            {
                "lang": it.lang,
                "lang_label": _lang_label(it.lang),
                "severity": it.severity.value,
                "severity_label": SEVERITY_LABELS.get(it.severity, ""),
                "roas": it.ad_roas,
            }
            for it in p_items
        ]
        top_losing_ads = [
            ad
            for it in p_items
            for ad in it.top_losing_ads
            if ad.ad_roas is not None
        ]
        top_losing_ads.sort(key=lambda ad: ad.ad_roas if ad.ad_roas is not None else 999)

        res.append(
            AggregatedProductAlert(
                product_id=pid,
                product_code=first.product_code,
                product_name=first.product_name,
                store_codes=sorted(list(stores_set)),
                ad_spend_usd=round(total_spend, 2),
                purchase_value_usd=round(total_purchase, 2),
                ad_roas=avg_roas,
                active_7d_ad_spend_usd=round(total_active_7d_spend, 2),
                estimated_loss=round(total_purchase - total_spend, 2),
                max_severity=max_item.severity.value,
                max_severity_label=SEVERITY_LABELS.get(max_item.severity, ""),
                alert_languages=alert_languages,
                alert_count=len(p_items),
                active_days=max(it.active_days for it in p_items),
                computed_at=max_item.computed_at,
                top_losing_ads=top_losing_ads[:3],
                evaluation_lang=max_item.lang,
            )
        )

    # Sort by max_severity desc (severe first), then active_7d_ad_spend_usd desc
    sev_rank = {"severe": 3, "moderate": 2, "mild": 1}
    res.sort(key=lambda x: (sev_rank.get(x.max_severity, 0), x.active_7d_ad_spend_usd), reverse=True)
    return res


def get_product_alert_details(product_id: int, threshold: float | None = None) -> dict[str, Any]:
    """查询商品下的国家与广告预警列表。"""
    threshold_value = _normalize_threshold(threshold)
    p_row = query_one(
        "SELECT id, product_code, name FROM media_products WHERE id = %(product_id)s AND deleted_at IS NULL AND archived = 0",
        {"product_id": product_id}
    )
    if not p_row:
        return {}

    rows = query(
        """
        SELECT c.product_id, c.lang, c.ad_spend_usd, c.purchase_value_usd,
               c.ad_roas, c.active_7d_ad_spend_usd, c.computed_at,
               p.product_code, p.name AS product_name
        FROM media_product_lang_ad_summary_cache c
        JOIN media_products p ON p.id = c.product_id AND p.deleted_at IS NULL AND p.archived = 0
        WHERE c.product_id = %(product_id)s
          AND c.ad_roas IS NOT NULL
          AND c.ad_roas < %(threshold)s
          AND c.active_7d_ad_spend_usd > 0
          AND c.ad_spend_usd > 0
        ORDER BY c.ad_roas ASC, c.active_7d_ad_spend_usd DESC
        """,
        {"product_id": product_id, "threshold": threshold_value}
    )

    countries = []
    for row in rows:
        lang = _safe_str(row.get("lang"))
        detail = get_alert_detail(product_id, lang, threshold=threshold)
        if detail:
            countries.append(detail)

    realtime_exists_sql = ""
    if _has_realtime_ad_table():
        realtime_exists_sql = """
            OR EXISTS (
              SELECT 1 FROM meta_ad_realtime_daily_ad_metrics rt
              WHERE rt.normalized_ad_code = m.normalized_ad_code
                AND rt.spend_usd > 0
                AND rt.business_date >= DATE_SUB(CURDATE(), INTERVAL 3 DAY)
            )
        """

    ads_rows = query(
        f"""
        SELECT
          m.normalized_ad_code AS ad_code,
          MAX(m.ad_name) AS ad_name,
          m.ad_account_id,
          MAX(m.ad_account_name) AS ad_account_name,
          MIN(DATE(COALESCE(m.meta_business_date, m.report_date))) AS first_active_date,
          MAX(DATE(COALESCE(m.meta_business_date, m.report_date))) AS last_active_date,
          SUM(COALESCE(m.spend_usd, 0)) AS ad_spend_usd,
          SUM(COALESCE(m.purchase_value_usd, 0)) AS purchase_value_usd,
          COUNT(DISTINCT DATE(COALESCE(m.meta_business_date, m.report_date))) AS active_days
        FROM meta_ad_daily_ad_metrics m
        WHERE m.product_id = %(product_id)s
          AND (
            EXISTS (
              SELECT 1 FROM meta_ad_daily_ad_metrics d
              WHERE d.product_id = m.product_id
                AND d.normalized_ad_code = m.normalized_ad_code
                AND d.spend_usd > 0
                AND DATE(COALESCE(d.meta_business_date, d.report_date)) >= DATE_SUB(CURDATE(), INTERVAL 3 DAY)
            )
            {realtime_exists_sql}
          )
        GROUP BY m.normalized_ad_code, m.ad_account_id
        ORDER BY ad_spend_usd DESC
        """,
        {"product_id": product_id}
    )

    today_map = {}
    prod_code = _safe_str(p_row.get("product_code"))
    if prod_code:
        try:
            today_rows = query(
                """
                SELECT 
                  m.normalized_ad_code AS ad_code, 
                  m.ad_account_id, 
                  MAX(m.ad_name) AS ad_name,
                  MAX(m.ad_account_name) AS ad_account_name,
                  SUM(COALESCE(m.spend_usd, 0)) AS today_spend, 
                  SUM(COALESCE(m.purchase_value_usd, 0)) AS today_purchase
                FROM meta_ad_realtime_daily_ad_metrics m
                INNER JOIN (
                  SELECT business_date, ad_account_id, MAX(snapshot_at) AS max_snapshot_at
                  FROM meta_ad_realtime_daily_ad_metrics
                  WHERE business_date = CURDATE()
                  GROUP BY business_date, ad_account_id
                ) latest
                  ON latest.business_date = m.business_date
                 AND latest.ad_account_id = m.ad_account_id
                 AND latest.max_snapshot_at = m.snapshot_at
                WHERE m.business_date = CURDATE()
                  AND (
                    LOWER(m.normalized_campaign_code) LIKE CONCAT(LOWER(%(prod_code)s), '%%')
                    OR LOWER(m.campaign_name) LIKE CONCAT(LOWER(%(prod_code)s), '%%')
                    OR LOWER(m.normalized_ad_code) LIKE CONCAT(LOWER(%(prod_code)s), '%%')
                    OR LOWER(m.ad_name) LIKE CONCAT(LOWER(%(prod_code)s), '%%')
                  )
                GROUP BY m.normalized_ad_code, m.ad_account_id
                """,
                {"prod_code": prod_code}
            )
            for r in today_rows:
                key = (_safe_str(r.get("ad_code")), _safe_str(r.get("ad_account_id")))
                today_map[key] = {
                    "ad_name": _safe_str(r.get("ad_name")),
                    "ad_account_name": _safe_str(r.get("ad_account_name")),
                    "spend": _safe_float(r.get("today_spend")),
                    "purchase": _safe_float(r.get("today_purchase")),
                }
        except Exception as e:
            log.warning(f"Failed to query realtime ads for product {product_id}: {e}")

    ads_dict = {}
    for r in ads_rows:
        code = _safe_str(r.get("ad_code"))
        acc_id = _safe_str(r.get("ad_account_id"))
        key = (code, acc_id)
        
        spend = _safe_float(r.get("ad_spend_usd"))
        purchase = _safe_float(r.get("purchase_value_usd"))
        
        ads_dict[key] = {
            "ad_code": code,
            "ad_name": _safe_str(r.get("ad_name")),
            "ad_account_id": acc_id,
            "ad_account_name": _safe_str(r.get("ad_account_name")),
            "first_active_date": _iso_date(r.get("first_active_date")),
            "last_active_date": _iso_date(r.get("last_active_date")),
            "ad_spend_usd": spend,
            "purchase_value_usd": purchase,
            "active_days": int(r.get("active_days") or 0),
        }

    for key, today_val in today_map.items():
        if key in ads_dict:
            ads_dict[key]["ad_spend_usd"] += today_val["spend"]
            ads_dict[key]["purchase_value_usd"] += today_val["purchase"]
            ads_dict[key]["last_active_date"] = date.today().isoformat()
        else:
            ads_dict[key] = {
                "ad_code": key[0],
                "ad_name": today_val["ad_name"] or key[0],
                "ad_account_id": key[1],
                "ad_account_name": today_val["ad_account_name"],
                "first_active_date": date.today().isoformat(),
                "last_active_date": date.today().isoformat(),
                "ad_spend_usd": today_val["spend"],
                "purchase_value_usd": today_val["purchase"],
                "active_days": 1,
            }

    ads_list = []
    for item in ads_dict.values():
        spend = round(item["ad_spend_usd"], 2)
        purchase = round(item["purchase_value_usd"], 2)
        roas = round(purchase / spend, 4) if spend > 0.01 else None
        item["ad_spend_usd"] = spend
        item["purchase_value_usd"] = purchase
        item["ad_roas"] = roas
        ads_list.append(item)

    ads_list.sort(key=lambda x: x["ad_spend_usd"], reverse=True)

    return {
        "product_id": product_id,
        "product_code": prod_code,
        "product_name": _safe_str(p_row.get("name")),
        "countries": countries,
        "ads": ads_list,
    }


def get_ad_detail_and_trend(
    product_id: int,
    ad_code: str,
    ad_account_id: str,
) -> dict[str, Any] | None:
    """查询单个广告的多时间窗口数据和近 30 天趋势。"""
    cfg = _problem_level_config("ad")
    source_sql = _problem_ads_source_sql(cfg)

    business_date = current_meta_business_date()
    yesterday = business_date - timedelta(days=1)
    last_7d_start = business_date - timedelta(days=6)
    last_30d_start = business_date - timedelta(days=29)

    params = {
        "ad_code": ad_code,
        "ad_account_id": ad_account_id,
        "today": business_date,
        "yesterday": yesterday,
        "last_7d_start": last_7d_start,
        "last_30d_start": last_30d_start,
    }

    row = query_one(
        f"""
        SELECT
          MIN(s.metric_date) AS first_active_date,
          MAX(s.metric_date) AS last_active_date,
          MAX(s.name) AS ad_name,
          MAX(s.ad_account_name) AS ad_account_name,
          SUM(CASE WHEN s.metric_date = %(today)s THEN COALESCE(s.spend_usd, 0) ELSE 0 END) AS today_spend_usd,
          SUM(CASE WHEN s.metric_date = %(today)s THEN COALESCE(s.purchase_value_usd, 0) ELSE 0 END) AS today_purchase_value_usd,
          SUM(CASE WHEN s.metric_date = %(today)s THEN COALESCE(s.result_count, 0) ELSE 0 END) AS today_result_count,
          SUM(CASE WHEN s.metric_date = %(yesterday)s THEN COALESCE(s.spend_usd, 0) ELSE 0 END) AS yesterday_spend_usd,
          SUM(CASE WHEN s.metric_date = %(yesterday)s THEN COALESCE(s.purchase_value_usd, 0) ELSE 0 END) AS yesterday_purchase_value_usd,
          SUM(CASE WHEN s.metric_date = %(yesterday)s THEN COALESCE(s.result_count, 0) ELSE 0 END) AS yesterday_result_count,
          SUM(CASE WHEN s.metric_date >= %(last_7d_start)s AND s.metric_date <= %(today)s THEN COALESCE(s.spend_usd, 0) ELSE 0 END) AS last_7d_spend_usd,
          SUM(CASE WHEN s.metric_date >= %(last_7d_start)s AND s.metric_date <= %(today)s THEN COALESCE(s.purchase_value_usd, 0) ELSE 0 END) AS last_7d_purchase_value_usd,
          SUM(CASE WHEN s.metric_date >= %(last_7d_start)s AND s.metric_date <= %(today)s THEN COALESCE(s.result_count, 0) ELSE 0 END) AS last_7d_result_count,
          SUM(CASE WHEN s.metric_date >= %(last_30d_start)s AND s.metric_date <= %(today)s THEN COALESCE(s.spend_usd, 0) ELSE 0 END) AS last_30d_spend_usd,
          SUM(CASE WHEN s.metric_date >= %(last_30d_start)s AND s.metric_date <= %(today)s THEN COALESCE(s.purchase_value_usd, 0) ELSE 0 END) AS last_30d_purchase_value_usd,
          SUM(CASE WHEN s.metric_date >= %(last_30d_start)s AND s.metric_date <= %(today)s THEN COALESCE(s.result_count, 0) ELSE 0 END) AS last_30d_result_count,
          SUM(COALESCE(s.spend_usd, 0)) AS overall_spend_usd,
          SUM(COALESCE(s.purchase_value_usd, 0)) AS overall_purchase_value_usd,
          SUM(COALESCE(s.result_count, 0)) AS overall_result_count
        FROM {source_sql} s
        WHERE s.code = %(ad_code)s
          AND s.ad_account_id = %(ad_account_id)s
        """,
        params
    )

    if not row or not row.get("ad_name"):
        fallback = query_one(
            """
            SELECT MIN(DATE(COALESCE(m.meta_business_date, m.report_date))) AS first_active_date,
                   MAX(DATE(COALESCE(m.meta_business_date, m.report_date))) AS last_active_date,
                   MAX(m.ad_name) AS ad_name,
                   MAX(m.ad_account_name) AS ad_account_name,
                   SUM(COALESCE(m.spend_usd, 0)) AS overall_spend_usd,
                   SUM(COALESCE(m.purchase_value_usd, 0)) AS overall_purchase_value_usd,
                   SUM(COALESCE(m.result_count, 0)) AS overall_result_count
            FROM meta_ad_daily_ad_metrics m
            WHERE m.normalized_ad_code = %(ad_code)s
              AND m.ad_account_id = %(ad_account_id)s
            """,
            {"ad_code": ad_code, "ad_account_id": ad_account_id}
        )
        if not fallback or not fallback.get("ad_name"):
            return None
        row = {
            "first_active_date": fallback.get("first_active_date"),
            "last_active_date": fallback.get("last_active_date"),
            "ad_name": fallback.get("ad_name"),
            "ad_account_name": fallback.get("ad_account_name"),
            "today_spend_usd": 0.0,
            "today_purchase_value_usd": 0.0,
            "today_result_count": 0,
            "yesterday_spend_usd": 0.0,
            "yesterday_purchase_value_usd": 0.0,
            "yesterday_result_count": 0,
            "last_7d_spend_usd": 0.0,
            "last_7d_purchase_value_usd": 0.0,
            "last_7d_result_count": 0,
            "last_30d_spend_usd": 0.0,
            "last_30d_purchase_value_usd": 0.0,
            "last_30d_result_count": 0,
            "overall_spend_usd": fallback.get("overall_spend_usd"),
            "overall_purchase_value_usd": fallback.get("overall_purchase_value_usd"),
            "overall_result_count": fallback.get("overall_result_count"),
        }

    def _metric(prefix: str) -> dict[str, Any]:
        spend = round(_safe_float(row.get(f"{prefix}_spend_usd")), 2)
        purchase = _safe_float(row.get(f"{prefix}_purchase_value_usd"))
        roas = round(purchase / spend, 4) if spend > 0.01 else None
        return {
            "spend_usd": spend,
            "purchase_value_usd": round(purchase, 2),
            "result_count": int(_safe_float(row.get(f"{prefix}_result_count"))),
            "roas": roas,
        }

    metrics = {
        "today": _metric("today"),
        "yesterday": _metric("yesterday"),
        "last_7d": _metric("last_7d"),
        "last_30d": _metric("last_30d"),
        "overall": _metric("overall"),
    }

    trend_rows = query(
        """
        SELECT
          DATE(COALESCE(m.meta_business_date, m.report_date)) AS ad_date,
          COALESCE(SUM(COALESCE(m.spend_usd, 0)), 0) AS spend_usd,
          COALESCE(SUM(COALESCE(m.purchase_value_usd, 0)), 0) AS purchase_value_usd
        FROM meta_ad_daily_ad_metrics m
        WHERE m.product_id = %(product_id)s
          AND m.normalized_ad_code = %(ad_code)s
          AND m.ad_account_id = %(ad_account_id)s
          AND DATE(COALESCE(m.meta_business_date, m.report_date)) >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
          AND DATE(COALESCE(m.meta_business_date, m.report_date)) < CURDATE()
        GROUP BY ad_date
        ORDER BY ad_date DESC
        """,
        {"product_id": product_id, "ad_code": ad_code, "ad_account_id": ad_account_id}
    )

    trend_series: list[DailyPoint] = []
    for r in trend_rows:
        ad_date = _iso_date(r["ad_date"])
        if ad_date:
            spend = _safe_float(r["spend_usd"])
            purchase = _safe_float(r["purchase_value_usd"])
            trend_series.append(
                DailyPoint(
                    date=ad_date,
                    spend_usd=round(spend, 2),
                    purchase_value_usd=round(purchase, 2),
                    roas=round(purchase / spend, 4) if spend > 0.01 else None
                )
            )

    today_spend = metrics["today"]["spend_usd"]
    today_purchase = metrics["today"]["purchase_value_usd"]
    if today_spend > 0.01:
        trend_series.insert(
            0,
            DailyPoint(
                date=business_date.isoformat(),
                spend_usd=today_spend,
                purchase_value_usd=today_purchase,
                roas=metrics["today"]["roas"]
            )
        )

    return {
        "product_id": product_id,
        "ad_code": ad_code,
        "ad_name": _safe_str(row.get("ad_name")),
        "ad_account_id": ad_account_id,
        "ad_account_name": _safe_str(row.get("ad_account_name")),
        "first_active_date": _iso_date(row.get("first_active_date")),
        "last_active_date": _iso_date(row.get("last_active_date")),
        "metrics": metrics,
        "trend": trend_series,
    }



def _alert_trend_inputs(product_id: int, lang: str, end_date: str | None = None) -> tuple[float | None, float | None]:
    series = get_trend_series(product_id, lang, days=14, end_date=end_date)
    return _avg_roas(series, 0, 7), _avg_roas(series, 7, 7)


def _format_ad_for_prompt(ad: AdListItem) -> str:
    country_name = _COUNTRY_LABELS.get(ad.country.upper())
    country_text = f"{ad.country}（{country_name}）" if country_name else ad.country
    roas_text = f"{ad.ad_roas:.2f}" if ad.ad_roas is not None else "N/A"
    return (
        f"- 国家: {country_text} | AD名称: {ad.ad_name or ad.normalized_ad_code} | "
        f"花费: ${ad.total_spend:.2f} | 购买价值: ${ad.total_purchase:.2f} | "
        f"ROAS: {roas_text} | 活跃天数: {ad.active_days}"
    )


def _parse_ad_evaluations(payload: Any) -> list[AdEvaluation] | None:
    parsed = _coerce_json_payload(payload)
    if parsed is None:
        return None
    if isinstance(parsed, dict):
        parsed = parsed.get("evaluations") or parsed.get("items") or [parsed]
    if not isinstance(parsed, list):
        return None

    evaluations: list[AdEvaluation] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        judgment = _safe_str(item.get("judgment")).strip()
        if judgment not in {"关停", "优化", "观察"}:
            judgment = "观察"
        evaluations.append(
            AdEvaluation(
                country=_safe_str(item.get("country")).upper(),
                ad_name=_safe_str(item.get("ad_name")),
                roas=round(_safe_float(item.get("roas")), 4),
                judgment=judgment,
                reason=_safe_str(item.get("reason")),
            )
        )
    return evaluations


def _coerce_json_payload(payload: Any) -> Any:
    if isinstance(payload, (list, dict)):
        return payload
    if payload is None:
        return None
    text = str(payload).strip()
    if not text:
        return None
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1].strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        array_start = text.find("[")
        array_end = text.rfind("]")
        if array_start != -1 and array_end > array_start:
            try:
                return json.loads(text[array_start:array_end + 1])
            except json.JSONDecodeError:
                pass
        object_start = text.find("{")
        object_end = text.rfind("}")
        if object_start != -1 and object_end > object_start:
            try:
                return json.loads(text[object_start:object_end + 1])
            except json.JSONDecodeError:
                pass
    return None


def _get_active_window(product_id: int, lang: str) -> ActiveWindow:
    lower_lang = lang.strip().lower()

    # 1. 预先查出该商品语言下的所有 media_items
    media_items = query(
        """
        SELECT filename, display_name
        FROM media_items
        WHERE product_id = %(product_id)s
          AND deleted_at IS NULL
          AND LOWER(lang) = %(lang)s
        """,
        {"product_id": product_id, "lang": lower_lang}
    )
    if not media_items:
        return ActiveWindow(None, None, 0)

    # 2. 查询该产品所有有消耗的记录明细
    rows = query(
        """
        SELECT
          DATE(COALESCE(m.meta_business_date, m.report_date)) AS ad_date,
          UPPER(TRIM(m.market_country)) AS country,
          COALESCE(NULLIF(TRIM(m.ad_name), ''), NULLIF(TRIM(m.normalized_ad_code), ''), '') AS ad_name,
          COALESCE(NULLIF(TRIM(m.normalized_ad_code), ''), '') AS normalized_ad_code
        FROM meta_ad_daily_ad_metrics m
        WHERE m.product_id = %(product_id)s
          AND COALESCE(m.spend_usd, 0) > 0
        """,
        {"product_id": product_id},
    )

    # 3. 在 Python 内存中过滤并计算活跃天数及边界
    matched_dates = set()
    for row in rows:
        ad_date = row.get("ad_date")
        if not ad_date:
            continue
        date_str = _iso_date(ad_date)
        if not date_str:
            continue

        ad_name = _safe_str(row.get("ad_name"))
        ad_code = _safe_str(row.get("normalized_ad_code"))
        country = _safe_str(row.get("country")).upper()

        if _match_media_item(ad_name, ad_code, country, media_items, lower_lang):
            matched_dates.add(date_str)

    if not matched_dates:
        return ActiveWindow(None, None, 0)

    sorted_dates = sorted(list(matched_dates))
    return ActiveWindow(
        delivery_start=sorted_dates[0],
        delivery_end=sorted_dates[-1],
        active_days=len(sorted_dates),
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


def _problem_level_config(level: str) -> dict[str, str]:
    normalized = (level or "").strip().lower()
    cfg = _PROBLEM_LEVEL_CONFIG.get(normalized)
    if not cfg:
        raise ValueError("level must be one of campaign/adset/ad")
    return cfg


def _problem_ads_source_sql(cfg: dict[str, str]) -> str:
    daily_table = cfg["daily_table"]
    daily_code_col = cfg["daily_code_col"]
    daily_name_col = cfg["daily_name_col"]
    realtime_table = cfg["realtime_table"]
    realtime_code_col = cfg["realtime_code_col"]
    realtime_name_col = cfg["realtime_name_col"]
    return f"""
        (
          SELECT
            COALESCE(meta_business_date, report_date) AS metric_date,
            {daily_code_col} AS code,
            {daily_name_col} AS name,
            ad_account_id,
            ad_account_name,
            matched_product_code,
            spend_usd,
            purchase_value_usd,
            result_count
          FROM {daily_table}
          WHERE COALESCE(meta_business_date, report_date) < %(today)s
            AND {daily_code_col} IS NOT NULL
            AND {daily_code_col} <> ''
          UNION ALL
          SELECT
            m.business_date AS metric_date,
            m.{realtime_code_col} AS code,
            m.{realtime_name_col} AS name,
            m.ad_account_id,
            m.ad_account_name,
            NULL AS matched_product_code,
            m.spend_usd,
            m.purchase_value_usd,
            m.result_count
          FROM {realtime_table} m
          INNER JOIN (
            SELECT business_date, ad_account_id, MAX(snapshot_at) AS max_snapshot_at
            FROM {realtime_table}
            WHERE business_date = %(today)s
              AND data_completeness = 'realtime_partial'
            GROUP BY business_date, ad_account_id
          ) latest
            ON latest.business_date = m.business_date
           AND latest.ad_account_id = m.ad_account_id
           AND latest.max_snapshot_at = m.snapshot_at
          WHERE m.business_date = %(today)s
            AND m.data_completeness = 'realtime_partial'
            AND m.{realtime_code_col} IS NOT NULL
            AND m.{realtime_code_col} <> ''
        )
    """


def _problem_metric(row: dict[str, Any], prefix: str) -> ProblemMetric:
    spend = round(_safe_float(row.get(f"{prefix}_spend_usd")), 2)
    purchase = _safe_float(row.get(f"{prefix}_purchase_value_usd"))
    result_count = int(_safe_float(row.get(f"{prefix}_result_count")))
    return ProblemMetric(
        spend_usd=spend,
        result_count=result_count,
        roas=round(purchase / spend, 4) if spend > 0.01 else None,
    )


def _problem_detail_url(
    *,
    level: str,
    code: str,
    name: str,
    ad_account_id: str,
    start_date: str,
    end_date: str,
) -> str:
    params = {
        "tab": "ads",
        "ads_level": level,
        "ads_code": code,
        "ads_name": name,
        "start_date": start_date,
        "end_date": end_date,
    }
    if ad_account_id:
        params["ad_account_id"] = ad_account_id
    return "/order-analytics?" + urlencode(params)


_realtime_ad_table_exists: bool | None = None


def _has_realtime_ad_table() -> bool:
    """检查数据库中是否存在 meta_ad_realtime_daily_ad_metrics 表。"""
    global _realtime_ad_table_exists
    if _realtime_ad_table_exists is not None:
        return _realtime_ad_table_exists
    try:
        query_one("SELECT 1 FROM meta_ad_realtime_daily_ad_metrics LIMIT 1")
        _realtime_ad_table_exists = True
    except Exception:
        _realtime_ad_table_exists = False
    return _realtime_ad_table_exists
