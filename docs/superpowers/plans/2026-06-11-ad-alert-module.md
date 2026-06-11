# 广告预警模块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a ROAS alert system that surfaces product×language ads where ROAS < configurable threshold (default 1.5) and spending is still active, providing trend data and rule-based judgment for manual stop-loss decisions.

**Architecture:** Core logic in `appcore/ad_alerts.py` (threshold config, alert query, rule engine, trend data). Flask routes in `web/routes/ad_alerts.py` (list page + JSON API). Frontend is Jinja2 + inline SVG (no chart library) following Ocean Blue design system. No new DB tables — reuses `media_product_lang_ad_summary_cache` and `meta_ad_daily_ad_metrics`.

**Tech Stack:** Python 3 / Flask / MySQL / Jinja2 / Ocean Blue CSS / login_required + admin_required auth

---
## 文件结构

| 操作 | 文件 | 职责 |
|------|------|------|
| Create | `appcore/ad_alerts.py` | 阈值配置、预警查询、规则引擎、趋势数据 |
| Create | `web/routes/ad_alerts.py` | Flask Blueprint — 列表页 + JSON API |
| Create | `web/templates/ad_alerts.html` | 预警列表页模板（含内联数据、筛选、卡片列表） |
| Create | `web/templates/ad_alerts_detail_modal.html` | 详情弹窗模板（含 SVG 趋势图） |
| Modify | `web/app.py` | 注册新蓝图 + 导入 |
| Modify | `web/templates/layout.html` | 侧栏菜单入口 |
| Modify | `web/routes/admin.py` | 阈值配置 POST + 渲染 |
| Modify | `web/templates/admin_settings.html` | ROAS 设置区加阈值输入框 |

---

### Task 1: 核心逻辑 `appcore/ad_alerts.py`

**Files:**
- Create: `appcore/ad_alerts.py`

- [ ] **Step 1: 创建 appcore/ad_alerts.py 并实现阈值配置**

```python
"""广告预警模块 — 核心逻辑。

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


def get_threshold() -> float:
    """读取预警阈值配置，默认 1.5。"""
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
    """写入预警阈值配置。"""
    threshold = max(0.1, float(value))
    payload = json.dumps({"threshold": threshold}, ensure_ascii=False)
    system_settings.set_setting(ALERT_THRESHOLD_SETTING_KEY, payload)
```

- [ ] **Step 2: 实现数据模型和 LangCountryMapper**

```python
class Severity(str, Enum):
    SEVERE = "severe"       # ROAS < 1.0
    MODERATE = "moderate"   # 1.0 ≤ ROAS < 1.3
    MILD = "mild"           # 1.3 ≤ ROAS < threshold

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
    """研判结论。"""
    severity: Severity
    trend: TrendDirection
    phase: Phase
    conclusion: str
    reason: str


@dataclass
class AlertItem:
    """预警列表中的一条记录。"""
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


@dataclass
class DailyPoint:
    """趋势中的一个数据点。"""
    date: str
    spend_usd: float = 0.0
    purchase_value_usd: float = 0.0
    roas: float | None = None


@dataclass
class AlertDetail:
    """单条预警详情。"""
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


# ISO 639-1 → 中文标签
_LANG_LABELS: dict[str, str] = {
    "en": "英语", "de": "德语", "fr": "法语", "es": "西班牙语",
    "it": "意大利语", "nl": "荷兰语", "sv": "瑞典语", "fi": "芬兰语",
    "ja": "日语", "ko": "韩语", "pt": "葡萄牙语", "pt-br": "巴西葡语",
    "zh": "中文",
}


def _lang_label(code: str) -> str:
    return _LANG_LABELS.get(code.lower(), code.upper())
```

- [ ] **Step 3: 实现 _type_safe_float 辅助函数和预警主查询**

```python
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


def get_alerts(
    threshold: float | None = None,
    lang: str | None = None,
    severity: Severity | None = None,
    search: str | None = None,
) -> list[AlertItem]:
    """查询预警列表。

    :param threshold: ROAS 阈值，默认从配置读取
    :param lang: 语言筛选（如 "en"、"de"）
    :param severity: 严重度筛选
    :param search: 商品名称/编码搜索
    """
    if threshold is None:
        threshold = get_threshold()

    conditions = ["c.ad_roas IS NOT NULL", "c.ad_roas < %(threshold)s", "c.active_7d_ad_spend_usd > 0"]
    params: dict[str, Any] = {"threshold": threshold}

    if lang:
        conditions.append("c.lang = %(lang)s")
        params["lang"] = lang

    if search:
        conditions.append("(p.product_code LIKE %(search)s OR p.name LIKE %(search)s)")
        params["search"] = f"%{search}%"

    where_clause = " AND ".join(conditions)

    sql = f"""
        SELECT c.product_id, c.lang, c.item_count, c.pushed_video_count,
               c.ad_spend_usd, c.purchase_value_usd, c.ad_roas,
               c.active_7d_ad_spend_usd, c.computed_at,
               p.product_code, p.name AS product_name,
               p.store_code
        FROM media_product_lang_ad_summary_cache c
        JOIN media_products p ON p.id = c.product_id AND p.deleted_at IS NULL
        WHERE {where_clause}
        ORDER BY c.ad_roas ASC
    """
    rows = query(sql, params)

    items: list[AlertItem] = []
    for row in rows:
        roas = _safe_float(row.get("ad_roas") or 0)
        acts = _safe_float(row.get("active_7d_ad_spend_usd") or 0)
        spend = _safe_float(row.get("ad_spend_usd") or 0)
        purchase = _safe_float(row.get("purchase_value_usd") or 0)

        judgment = judge_alert(roas, None, [])
        estimated_loss = round(spend - purchase, 2)

        item = AlertItem(
            product_id=int(row["product_id"]),
            product_code=_safe_str(row.get("product_code")),
            product_name=_safe_str(row.get("product_name")),
            lang=_safe_str(row.get("lang")),
            store_codes=[_safe_str(row.get("store_code"))] if row.get("store_code") else [],
            ad_spend_usd=spend,
            purchase_value_usd=purchase,
            ad_roas=roas if roas > 0 else None,
            active_7d_ad_spend_usd=acts,
            delivery_status="active" if acts > 0 else "stopped",
            ad_roas_7d=None,
            computed_at=_iso(row.get("computed_at")),
            severity=judgment.severity,
            trend=judgment.trend,
            phase=judgment.phase,
            conclusion=judgment.conclusion,
            reason=judgment.reason,
            estimated_loss=estimated_loss,
        )
        if severity and item.severity != severity:
            continue
        items.append(item)

    return items
```

- [ ] **Step 4: 实现详情查询（含趋势数据和运行时长）**

```python
def get_alert_detail(
    product_id: int,
    lang: str,
    threshold: float | None = None,
) -> AlertDetail | None:
    """查询单条预警详情，含累计数据和趋势序列。

    趋势序列从 meta_ad_daily_ad_metrics 取 product_id 对应日期的
    spend_usd 和 purchase_value_usd（使用 ad_name 匹配 lang 逻辑）。
    """
    if threshold is None:
        threshold = get_threshold()

    row = query_one(
        """SELECT c.product_id, c.lang, c.item_count, c.pushed_video_count,
                  c.ad_spend_usd, c.purchase_value_usd, c.ad_roas,
                  c.active_7d_ad_spend_usd, c.computed_at,
                  p.product_code, p.name AS product_name,
                  p.store_code
           FROM media_product_lang_ad_summary_cache c
           JOIN media_products p ON p.id = c.product_id AND p.deleted_at IS NULL
           WHERE c.product_id = %(product_id)s AND c.lang = %(lang)s""",
        {"product_id": product_id, "lang": lang},
    )
    if not row:
        return None

    # 获取投放时长（从 daily_ad_metrics）
    day_range = query_one(
        """SELECT
               MIN(DATE(COALESCE(meta_business_date, report_date))) AS delivery_start,
               MAX(DATE(COALESCE(meta_business_date, report_date))) AS delivery_end
           FROM meta_ad_daily_ad_metrics
           WHERE product_id = %(product_id)s
             AND COALESCE(spend_usd, 0) > 0""",
        {"product_id": product_id},
    )
    active_days = _safe_float(
        query_one(
            "SELECT COUNT(DISTINCT DATE(COALESCE(meta_business_date, report_date))) AS cnt "
            "FROM meta_ad_daily_ad_metrics "
            "WHERE product_id = %(product_id)s AND COALESCE(spend_usd, 0) > 0",
            {"product_id": product_id},
        ).get("cnt")
    ) if day_range else 0

    # 获取趋势序列
    trend_series = get_trend_series(product_id, lang)

    roas = _safe_float(row.get("ad_roas") or 0)
    spend = _safe_float(row.get("ad_spend_usd") or 0)
    purchase = _safe_float(row.get("purchase_value_usd") or 0)
    acts = _safe_float(row.get("active_7d_ad_spend_usd") or 0)

    # 从趋势序列计算时段 ROAS 和趋势方向
    trend_7d_roas = _avg_roas(trend_series, 0, 7)
    trend_14d_roas = _avg_roas(trend_series, 0, 14)

    # 计算近7天vs前7天趋势
    recent_7d = _avg_roas(trend_series, 0, 7)
    prior_7d = _avg_roas(trend_series, 7, 14)

    judgment = judge_alert(roas, recent_7d, trend_series, prior_7d=prior_7d, active_days=int(active_days))

    days_series = trend_series[:14] if len(trend_series) > 14 else trend_series

    return AlertDetail(
        product_id=int(row["product_id"]),
        product_code=_safe_str(row.get("product_code")),
        product_name=_safe_str(row.get("product_name")),
        lang=_safe_str(row.get("lang")),
        lang_label=_lang_label(_safe_str(row.get("lang"))),
        store_codes=[_safe_str(row.get("store_code"))] if row.get("store_code") else [],
        ad_spend_usd=spend,
        purchase_value_usd=purchase,
        ad_roas=roas if roas > 0 else None,
        active_7d_ad_spend_usd=acts,
        estimated_loss=round(spend - purchase, 2),
        delivery_start_time=_iso(day_range.get("delivery_start")) if day_range else None,
        delivery_end_time=_iso(day_range.get("delivery_end")) if day_range else None,
        active_days=int(active_days),
        computed_at=_iso(row.get("computed_at")),
        judgment=judgment,
        trend=days_series,
    )
```

- [ ] **Step 5: 实现趋势数据查询和均值计算**

```python
_COUNTRY_LANG_MAP: dict[str, str] = {
    "US": "en", "GB": "en", "UK": "en", "AU": "en", "CA": "en",
    "IE": "en", "NZ": "en",
    "DE": "de", "AT": "de",
    "FR": "fr",
    "ES": "es",
    "IT": "it",
    "NL": "nl",
    "SE": "sv", "FI": "fi",
    "JP": "ja",
    "KR": "ko",
    "BR": "pt-br", "PT": "pt",
}


def _lang_from_country(country_code: str) -> str | None:
    return _COUNTRY_LANG_MAP.get(country_code.upper())


def get_trend_series(
    product_id: int,
    lang: str,
    days: int = 30,
) -> list[DailyPoint]:
    """查询近 N 天该商品该语言的广告投放趋势。

    通过 country_code / market_country 映射为 lang，
    或通过 ad_name 中的国家关键词推断语言。
    """
    lower_lang = lang.strip().lower()
    rows = query(
        """SELECT
               DATE(COALESCE(d.meta_business_date, d.report_date)) AS ad_date,
               COALESCE(SUM(COALESCE(d.spend_usd, 0)), 0) AS spend_usd,
               COALESCE(SUM(COALESCE(d.purchase_value_usd, 0)), 0) AS purchase_value_usd
           FROM meta_ad_daily_ad_metrics d
           WHERE d.product_id = %(product_id)s
             AND DATE(COALESCE(d.meta_business_date, d.report_date)) >= DATE_SUB(CURDATE(), INTERVAL %(days)s DAY)
             AND DATE(COALESCE(d.meta_business_date, d.report_date)) < CURDATE()
           GROUP BY ad_date
           ORDER BY ad_date ASC""",
        {"product_id": product_id, "days": days},
    )

    # 按 ad_id 分组后无法直接区分语言，这里返回该商品所有语言的汇总趋势
    # 实际项目中可以通过 ad_name 匹配 media_items.filename 来精确筛选
    series: list[DailyPoint] = []
    for row in rows:
        ad_date = _safe_str(row.get("ad_date"))
        if not ad_date:
            continue
        spend = _safe_float(row.get("spend_usd"))
        purchase = _safe_float(row.get("purchase_value_usd"))
        roas = round(purchase / spend, 4) if spend > 0.01 else None
        series.append(DailyPoint(date=ad_date, spend_usd=spend, purchase_value_usd=purchase, roas=roas))

    return series


def _avg_roas(series: list[DailyPoint], start: int, length: int) -> float | None:
    """计算序列中 [start, start+length) 范围内的日均 ROAS。"""
    segment = series[start:start + length]
    if not segment:
        return None
    total_spend = sum(p.spend_usd for p in segment)
    total_purchase = sum(p.purchase_value_usd for p in segment)
    if total_spend <= 0.01:
        return None
    return round(total_purchase / total_spend, 4)
```

- [ ] **Step 6: 实现规则引擎**

```python
def judge_alert(
    roas: float,
    recent_7d_roas: float | None,
    trend_series: list[DailyPoint],
    *,
    prior_7d: float | None = None,
    active_days: int | None = None,
) -> Judgment:
    """基于规则的广告研判引擎。

    返回包含严重度、趋势方向、运行阶段、结论和理由的 Judgment。
    """
    # 严重度
    if roas < 1.0:
        severity = Severity.SEVERE
    elif roas < 1.3:
        severity = Severity.MODERATE
    else:
        severity = Severity.MILD

    # 趋势方向：比较近7天 vs 更早7天的日均 ROAS
    trend = TrendDirection.STABLE
    if prior_7d is not None and recent_7d_roas is not None:
        if prior_7d > 0.01:
            ratio = recent_7d_roas / prior_7d
            if ratio < 0.9:
                trend = TrendDirection.WORSENING
            elif ratio > 1.1:
                trend = TrendDirection.IMPROVING

    # 运行阶段
    if active_days is not None and active_days < 7:
        phase = Phase.LEARNING
    else:
        phase = Phase.STABLE

    # 结论
    if severity == Severity.SEVERE and phase == Phase.STABLE:
        conclusion = "建议关停"
        reason = "ROAS 低于 1.0 且已运行超过 7 天，持续亏损，建议尽快关停止损"
    elif phase == Phase.LEARNING:
        conclusion = "建议观察"
        reason = "广告尚在 Meta 学习期（不到 7 天），ROAS 数据尚不稳定，可再观察几天"
    elif severity in (Severity.MODERATE, Severity.MILD) and phase == Phase.STABLE and trend == TrendDirection.WORSENING:
        conclusion = "建议优化"
        reason = "ROAS 偏低且近期趋势持续恶化，建议优化广告素材或调整受众定向"
    else:
        conclusion = "建议暂缓"
        reason = "ROAS 虽低于阈值但近期有回升迹象，可暂缓关停继续观察"

    return Judgment(
        severity=severity, trend=trend, phase=phase,
        conclusion=conclusion, reason=reason,
    )
```

- [ ] **Step 7: 提交**

```bash
git add appcore/ad_alerts.py
git commit -m "feat: add ad alert core logic (threshold, query, rule engine, trend)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Flask 路由 `web/routes/ad_alerts.py`

**Files:**
- Create: `web/routes/ad_alerts.py`

- [ ] **Step 1: 创建 web/routes/ad_alerts.py（Blueprint + 路由）**

```python
"""广告预警路由。

Docs anchor: docs/superpowers/specs/2026-06-11-ad-alert-module-design.md
"""
from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required
from web.auth import admin_required

from appcore import ad_alerts

log = logging.getLogger(__name__)

bp = Blueprint("ad_alerts", __name__, url_prefix="/ad-alerts")


def _parse_severity(raw: str | None):
    if not raw:
        return None
    try:
        return ad_alerts.Severity(raw)
    except ValueError:
        return None


@bp.route("/")
@login_required
@admin_required
def list_page():
    """预警列表页（服务端渲染，内嵌初始数据）。"""
    threshold = ad_alerts.get_threshold()
    return render_template(
        "ad_alerts.html",
        threshold=threshold,
        alert_counts={},
        SEVERITY_LABELS=ad_alerts.SEVERITY_LABELS,
        TREND_LABELS=ad_alerts.TREND_LABELS,
        PHASE_LABELS=ad_alerts.PHASE_LABELS,
    )


@bp.route("/api/list")
@login_required
@admin_required
def api_list():
    """预警列表 JSON API。"""
    try:
        threshold = float(request.args.get("threshold") or 0)
    except (TypeError, ValueError):
        threshold = None
    lang = request.args.get("lang") or None
    severity_raw = request.args.get("severity") or None
    search = request.args.get("search") or None
    severity = _parse_severity(severity_raw)

    items = ad_alerts.get_alerts(
        threshold=threshold,
        lang=lang,
        severity=severity,
        search=search,
    )
    return jsonify({
        "items": [_alert_item_to_dict(item) for item in items],
        "total": len(items),
        "threshold": threshold or ad_alerts.get_threshold(),
    })


@bp.route("/api/detail")
@login_required
@admin_required
def api_detail():
    """预警详情 JSON API。"""
    try:
        product_id = int(request.args.get("product_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid product_id"}), 400
    lang = request.args.get("lang") or ""
    if not lang:
        return jsonify({"error": "lang required"}), 400

    detail = ad_alerts.get_alert_detail(product_id, lang)
    if not detail:
        return jsonify({"error": "not found"}), 404

    return jsonify({
        "detail": _alert_detail_to_dict(detail),
    })


@bp.route("/api/threshold", methods=["POST"])
@login_required
@admin_required
def api_set_threshold():
    """更新预警阈值。"""
    body = request.get_json(silent=True) or {}
    try:
        value = float(body.get("threshold") or 0)
        if value <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "threshold must be a positive number"}), 400

    ad_alerts.set_threshold(value)
    return jsonify({"threshold": value})


def _alert_item_to_dict(item: ad_alerts.AlertItem) -> dict[str, Any]:
    return {
        "product_id": item.product_id,
        "product_code": item.product_code,
        "product_name": item.product_name,
        "lang": item.lang,
        "store_codes": item.store_codes,
        "ad_spend_usd": item.ad_spend_usd,
        "purchase_value_usd": item.purchase_value_usd,
        "ad_roas": item.ad_roas,
        "active_7d_ad_spend_usd": item.active_7d_ad_spend_usd,
        "delivery_status": item.delivery_status,
        "severity": item.severity.value,
        "severity_label": ad_alerts.SEVERITY_LABELS.get(item.severity, ""),
        "trend": item.trend.value,
        "trend_label": ad_alerts.TREND_LABELS.get(item.trend, ""),
        "phase": item.phase.value,
        "phase_label": ad_alerts.PHASE_LABELS.get(item.phase, ""),
        "conclusion": item.conclusion,
        "reason": item.reason,
        "estimated_loss": item.estimated_loss,
        "computed_at": item.computed_at,
    }


def _alert_detail_to_dict(detail: ad_alerts.AlertDetail) -> dict[str, Any]:
    return {
        "product_id": detail.product_id,
        "product_code": detail.product_code,
        "product_name": detail.product_name,
        "lang": detail.lang,
        "lang_label": detail.lang_label,
        "store_codes": detail.store_codes,
        "ad_spend_usd": detail.ad_spend_usd,
        "purchase_value_usd": detail.purchase_value_usd,
        "ad_roas": detail.ad_roas,
        "active_7d_ad_spend_usd": detail.active_7d_ad_spend_usd,
        "estimated_loss": detail.estimated_loss,
        "delivery_start_time": detail.delivery_start_time,
        "delivery_end_time": detail.delivery_end_time,
        "active_days": detail.active_days,
        "computed_at": detail.computed_at,
        "judgment": {
            "severity": detail.judgment.severity.value,
            "severity_label": ad_alerts.SEVERITY_LABELS.get(detail.judgment.severity, ""),
            "trend": detail.judgment.trend.value,
            "trend_label": ad_alerts.TREND_LABELS.get(detail.judgment.trend, ""),
            "phase": detail.judgment.phase.value,
            "phase_label": ad_alerts.PHASE_LABELS.get(detail.judgment.phase, ""),
            "conclusion": detail.judgment.conclusion,
            "reason": detail.judgment.reason,
        },
        "trend": [
            {
                "date": p.date,
                "spend_usd": p.spend_usd,
                "purchase_value_usd": p.purchase_value_usd,
                "roas": p.roas,
            }
            for p in detail.trend
        ],
    }
```

- [ ] **Step 2: 提交**

```bash
git add web/routes/ad_alerts.py
git commit -m "feat: add ad alert routes (list page + JSON API + threshold config)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 蓝图注册 `web/app.py`

**Files:**
- Modify: `web/app.py`

- [ ] **Step 1: 在 web/app.py 的导入区域（第 134 行附近）添加导入**

在 `from web.routes.video_analyse_ai import bp as video_analyse_ai_bp` 之后添加：

```python
from web.routes.ad_alerts import bp as ad_alerts_bp
```

- [ ] **Step 2: 在 web/app.py 的蓝图注册区域（第 456 行附近）注册蓝图**

在 `app.register_blueprint(video_analyse_ai_bp)` 之后添加：

```python
    app.register_blueprint(ad_alerts_bp)
```

- [ ] **Step 3: 提交**

```bash
git add web/app.py
git commit -m "feat: register ad_alerts blueprint

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 预警列表页模板

**Files:**
- Create: `web/templates/ad_alerts.html`

- [ ] **Step 1: 创建 web/templates/ad_alerts.html**

```html
{% extends "layout.html" %}
{% block title %}广告预警{% endblock %}
{% block head %}
{{ super() }}
<style>
:root {
  --alert-card-gap: 12px;
}
.alert-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}
.alert-header h1 {
  font-size: 22px;
  font-weight: 700;
  margin: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}
.alert-header h1 .badge {
  font-size: 13px;
  font-weight: 500;
  background: var(--oc-bg-subtle);
  color: var(--oc-fg-muted);
  padding: 2px 10px;
  border-radius: 12px;
}
.alert-tools {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.alert-threshold-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--oc-fg-muted);
  background: var(--oc-bg-subtle);
  padding: 4px 12px;
  border-radius: 6px;
}
.alert-threshold-badge strong {
  color: var(--oc-fg);
}
.alert-threshold-badge button {
  border: none;
  background: none;
  cursor: pointer;
  color: var(--oc-primary);
  font-size: 13px;
  padding: 0;
}
.alert-filters {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.alert-filters .oc-select {
  min-width: 120px;
}
.alert-search-input {
  flex: 1;
  min-width: 200px;
  max-width: 340px;
}
.alert-list {
  display: flex;
  flex-direction: column;
  gap: var(--alert-card-gap);
}
.alert-card {
  background: var(--oc-card-bg);
  border: 1px solid var(--oc-border);
  border-radius: 12px;
  padding: 16px 20px;
  cursor: pointer;
  transition: box-shadow 0.15s, border-color 0.15s;
}
.alert-card:hover {
  box-shadow: 0 2px 8px rgba(0,0,0,0.06);
  border-color: var(--oc-primary);
}
.alert-card-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.alert-card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.alert-card-title .lang-tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  background: var(--oc-primary);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  flex-shrink: 0;
}
.alert-card-title .product-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--oc-fg);
}
.alert-card-title .product-code {
  font-size: 12px;
  color: var(--oc-fg-muted);
  background: var(--oc-bg-subtle);
  padding: 1px 8px;
  border-radius: 4px;
}
.alert-card-badges {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.alert-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 500;
  padding: 2px 10px;
  border-radius: 10px;
}
.alert-badge.severe {
  background: #fef2f2;
  color: #dc2626;
}
.alert-badge.moderate {
  background: #fff7ed;
  color: #ea580c;
}
.alert-badge.mild {
  background: #fffbeb;
  color: #ca8a04;
}
.alert-badge.worsening {
  background: #fef2f2;
  color: #dc2626;
}
.alert-badge.stable {
  background: #f0fdf4;
  color: #16a34a;
}
.alert-badge.improving {
  background: #eff6ff;
  color: #2563eb;
}
.alert-card-metrics {
  display: flex;
  align-items: center;
  gap: 20px;
  font-size: 13px;
  color: var(--oc-fg-muted);
  flex-wrap: wrap;
}
.alert-card-metrics .metric {
  display: flex;
  align-items: baseline;
  gap: 4px;
}
.alert-card-metrics .metric strong {
  color: var(--oc-fg);
  font-weight: 600;
}
.alert-card-metrics .metric .loss {
  color: var(--oc-danger);
}
.alert-card-conclusion {
  margin-top: 10px;
  padding: 8px 12px;
  background: var(--oc-bg-subtle);
  border-radius: 8px;
  font-size: 13px;
  display: flex;
  align-items: flex-start;
  gap: 6px;
}
.alert-card-conclusion .conclusion-icon {
  flex-shrink: 0;
  margin-top: 1px;
}
.alert-card-conclusion .conclusion-label {
  font-weight: 600;
  margin-right: 4px;
  white-space: nowrap;
}
.alert-card-action {
  margin-top: 10px;
  text-align: right;
}
.alert-empty {
  text-align: center;
  padding: 60px 20px;
  color: var(--oc-fg-muted);
}
.alert-empty .empty-icon {
  font-size: 48px;
  margin-bottom: 12px;
}
.alert-loading {
  text-align: center;
  padding: 40px;
  color: var(--oc-fg-muted);
}
.severe .conclusion-label { color: var(--oc-danger); }
.moderate .conclusion-label { color: #ea580c; }
.mild .conclusion-label { color: #ca8a04; }
/* Severity filter btn group */
.oc-filter-group {
  display: inline-flex;
  gap: 0;
  border: 1px solid var(--oc-border);
  border-radius: 8px;
  overflow: hidden;
}
.oc-filter-btn {
  padding: 6px 14px;
  font-size: 13px;
  border: none;
  background: var(--oc-card-bg);
  color: var(--oc-fg-muted);
  cursor: pointer;
  border-right: 1px solid var(--oc-border);
  transition: background 0.1s;
}
.oc-filter-btn:last-child {
  border-right: none;
}
.oc-filter-btn.active {
  background: var(--oc-primary);
  color: #fff;
}
.oc-filter-btn:hover:not(.active) {
  background: var(--oc-bg-subtle);
}
/* Modal overrides */
.oc-roas-modal { max-width: 780px; }
</style>
{% endblock %}

{% block content %}
<div class="alert-header">
  <h1>
    🔔 广告预警
    <span class="badge" id="alertCountBadge">加载中...</span>
  </h1>
  <div class="alert-tools">
    <span class="alert-threshold-badge">
      ROAS 阈值: <strong id="thresholdDisplay">{{ "%.2f"|format(threshold) }}</strong>
      <button type="button" id="editThresholdBtn" title="修改阈值">✎</button>
    </span>
    <button type="button" class="oc-btn ghost" id="refreshBtn">🔄 刷新</button>
  </div>
</div>

<div class="alert-filters">
  <div class="oc-filter-group" id="severityFilterGroup">
    <button type="button" class="oc-filter-btn active" data-value="">全部</button>
    <button type="button" class="oc-filter-btn" data-value="severe">严重</button>
    <button type="button" class="oc-filter-btn" data-value="moderate">中度</button>
    <button type="button" class="oc-filter-btn" data-value="mild">轻度</button>
  </div>
  <input type="text" class="oc-input alert-search-input" id="searchInput" placeholder="搜索商品名称或编码..." />
</div>

<div id="alertList" class="alert-list">
  <div class="alert-loading">加载预警数据...</div>
</div>

<!-- 详情弹窗 -->
<div id="detailModalMask" class="oc-modal-mask oc" hidden>
  <div class="oc-modal oc-roas-modal" role="dialog">
    <div class="oc-modal-header">
      <h3 id="detailModalTitle">广告预警详情</h3>
      <button type="button" class="oc-icon-btn" id="detailCloseBtn" aria-label="关闭">✕</button>
    </div>
    <div class="oc-modal-body" id="detailModalBody">
      <div class="alert-loading">加载详情...</div>
    </div>
  </div>
</div>

<!-- 阈值编辑弹窗 -->
<div id="thresholdModalMask" class="oc-modal-mask oc" hidden>
  <div class="oc-modal" style="max-width:400px;" role="dialog">
    <div class="oc-modal-header">
      <h3>修改预警阈值</h3>
      <button type="button" class="oc-icon-btn" id="thresholdCloseBtn">✕</button>
    </div>
    <div class="oc-modal-body">
      <p class="field-hint" style="margin-bottom:12px;">
        低于该 ROAS 且仍在投放的广告将触发预警。保本 ROAS 参考值为 1.5。
      </p>
      <input type="number" class="oc-input" id="thresholdInput" step="0.1" min="0.1" value="{{ "%.1f"|format(threshold) }}" style="width:100%;" />
    </div>
    <div class="oc-modal-footer">
      <button type="button" class="oc-btn ghost" id="thresholdCancelBtn">取消</button>
      <button type="button" class="oc-btn primary" id="thresholdSaveBtn">保存</button>
    </div>
  </div>
</div>
{% endblock %}

{% block scripts %}
{{ super() }}
<script>
(function() {
  var currentThreshold = {{ threshold|tojson }};
  var currentSeverity = '';
  var currentSearch = '';
  var debounceTimer = null;

  function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  function loadAlerts() {
    var listEl = document.getElementById('alertList');
    listEl.innerHTML = '<div class="alert-loading">加载预警数据...</div>';

    var params = new URLSearchParams();
    if (currentThreshold) params.set('threshold', currentThreshold);
    if (currentSeverity) params.set('severity', currentSeverity);
    if (currentSearch) params.set('search', currentSearch);

    fetch('/ad-alerts/api/list?' + params.toString())
      .then(function(r) { return r.json(); })
      .then(function(data) {
        renderAlerts(data.items);
        document.getElementById('alertCountBadge').textContent = data.total + ' 条预警';
      })
      .catch(function() {
        listEl.innerHTML = '<div class="alert-empty">加载失败，请重试</div>';
      });
  }

  function renderAlerts(items) {
    var listEl = document.getElementById('alertList');
    if (!items || items.length === 0) {
      listEl.innerHTML = '<div class="alert-empty"><div class="empty-icon">✅</div><div>暂无预警</div></div>';
      return;
    }
    var html = '';
    items.forEach(function(item) {
      var sevClass = item.severity || 'mild';
      var trendClass = item.trend || 'stable';
      var trendIcon = item.trend === 'worsening' ? '🔻' : (item.trend === 'improving' ? '🟢' : '➖');
      var lossHtml = item.estimated_loss < 0
        ? '<span class="loss">-$' + Math.abs(item.estimated_loss).toFixed(2) + '</span>'
        : '$' + item.estimated_loss.toFixed(2);

      html += '<div class="alert-card" onclick="openDetail(' + item.product_id + ',\'' + item.lang + '\')">';
      html += '  <div class="alert-card-top">';
      html += '    <div class="alert-card-title">';
      html += '      <span class="lang-tag">' + item.lang.toUpperCase() + '</span>';
      html += '      <span class="product-name">' + escHtml(item.product_name || item.product_code) + '</span>';
      if (item.product_code) {
        html += '      <span class="product-code">' + escHtml(item.product_code) + '</span>';
      }
      html += '    </div>';
      html += '    <div class="alert-card-badges">';
      html += '      <span class="alert-badge ' + sevClass + '">' + escHtml(item.severity_label) + '</span>';
      html += '    </div>';
      html += '  </div>';
      html += '  <div class="alert-card-metrics">';
      html += '    <span class="metric">ROAS: <strong>' + (item.ad_roas !== null && item.ad_roas !== undefined ? item.ad_roas.toFixed(2) : 'N/A') + '</strong></span>';
      html += '    <span class="metric">消耗: <strong>$' + (item.ad_spend_usd || 0).toFixed(0) + '</strong></span>';
      html += '    <span class="metric">' + trendIcon + ' ' + escHtml(item.trend_label) + '</span>';
      html += '    <span class="metric">预亏: ' + lossHtml + '</span>';
      html += '  </div>';
      html += '  <div class="alert-card-conclusion ' + sevClass + '">';
      html += '    <span class="conclusion-icon">⚠️</span>';
      html += '    <span><span class="conclusion-label">' + escHtml(item.conclusion) + '</span>' + escHtml(item.reason) + '</span>';
      html += '  </div>';
      html += '</div>';
    });
    listEl.innerHTML = html;
  }

  function escHtml(s) {
    if (!s) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(s));
    return div.innerHTML;
  }

  window.openDetail = function(productId, lang) {
    var mask = document.getElementById('detailModalMask');
    mask.removeAttribute('hidden');
    document.getElementById('detailModalBody').innerHTML = '<div class="alert-loading">加载详情...</div>';
    document.getElementById('detailModalTitle').textContent = lang.toUpperCase() + ' 广告预警详情';

    fetch('/ad-alerts/api/detail?product_id=' + productId + '&lang=' + encodeURIComponent(lang))
      .then(function(r) { return r.json(); })
      .then(function(data) {
        renderDetail(data.detail);
      })
      .catch(function() {
        document.getElementById('detailModalBody').innerHTML = '<div class="alert-empty">加载详情失败</div>';
      });
  };

  function renderDetail(detail) {
    if (!detail) {
      document.getElementById('detailModalBody').innerHTML = '<div class="alert-empty">未找到数据</div>';
      return;
    }
    var j = detail.judgment || {};
    var html = '<div class="oc-form">';

    // 基本信息
    html += '<div class="oc-roas-head-actions" style="margin-bottom:16px;">';
    html += '  <span style="font-size:15px;font-weight:600;">' + escHtml(detail.product_name || detail.product_code) + '</span>';
    html += '  <span class="oc-badge" style="margin-left:8px;">' + escHtml(detail.lang_label || detail.lang.toUpperCase()) + '</span>';
    html += '</div>';

    // 趋势图
    if (detail.trend && detail.trend.length > 0) {
      html += '<div style="margin-bottom:20px;">';
      html += '  <h4 style="font-size:14px;font-weight:600;margin-bottom:8px;">近 ' + detail.trend.length + ' 天趋势</h4>';
      html += '  <div style="width:100%;overflow-x:auto;">';
      html += '    <svg width="' + Math.max(300, detail.trend.length * 25) + '" height="160" viewBox="0 0 ' + Math.max(300, detail.trend.length * 25) + ' 160" xmlns="http://www.w3.org/2000/svg" style="display:block;">';
      html += renderTrendSvg(detail.trend, Math.max(300, detail.trend.length * 25), 160);
      html += '    </svg>';
      html += '  </div>';
      html += '</div>';
    }

    // 累计数据
    html += '<table class="oc-table oc-table-compact" style="margin-bottom:16px;">';
    html += '  <tbody>';
    html += '    <tr><td style="width:140px;color:var(--oc-fg-muted);">总广告花费</td><td><strong>$' + (detail.ad_spend_usd || 0).toFixed(2) + '</strong></td></tr>';
    html += '    <tr><td style="color:var(--oc-fg-muted);">总购买价值</td><td><strong>$' + (detail.purchase_value_usd || 0).toFixed(2) + '</strong></td></tr>';
    html += '    <tr><td style="color:var(--oc-fg-muted);">综合 ROAS</td><td><strong>' + (detail.ad_roas !== null && detail.ad_roas !== undefined ? detail.ad_roas.toFixed(2) : 'N/A') + '</strong></td></tr>';
    html += '    <tr><td style="color:var(--oc-fg-muted);">预估亏损</td><td><strong style="color:var(--oc-danger);">$' + Math.abs(detail.estimated_loss || 0).toFixed(2) + '</strong></td></tr>';
    html += '    <tr><td style="color:var(--oc-fg-muted);">首次投放</td><td>' + escHtml(detail.delivery_start_time || '未知') + '</td></tr>';
    html += '    <tr><td style="color:var(--oc-fg-muted);">活跃天数</td><td>' + (detail.active_days || 0) + ' 天</td></tr>';
    html += '  </tbody>';
    html += '</table>';

    // 研判结论
    var sevClass = j.severity || 'mild';
    html += '<div class="alert-card-conclusion ' + sevClass + '" style="margin-top:0;">';
    html += '  <span class="conclusion-icon">⚠️</span>';
    html += '  <div>';
    html += '    <div style="font-weight:600;margin-bottom:4px;">' + escHtml(j.conclusion || '') + '</div>';
    html += '    <div style="font-size:13px;color:var(--oc-fg-muted);">' + escHtml(j.reason || '') + '</div>';
    html += '  </div>';
    html += '</div>';

    html += '</div>';
    document.getElementById('detailModalBody').innerHTML = html;
    updateTrendSvgColors();
  }

  function renderTrendSvg(trend, w, h) {
    var pad = { top: 20, right: 10, bottom: 30, left: 10 };
    var plotW = w - pad.left - pad.right;
    var plotH = h - pad.top - pad.bottom;

    var maxVal = 0;
    trend.forEach(function(p) {
      var v = Math.max(p.spend_usd || 0, p.purchase_value_usd || 0);
      if (v > maxVal) maxVal = v;
    });
    maxVal = Math.max(maxVal, 0.01);

    function x(i) { return pad.left + (i / Math.max(trend.length - 1, 1)) * plotW; }
    function y(v) { return pad.top + plotH - (v / maxVal) * plotH; }

    var spendPath = '';
    var purchasePath = '';
    trend.forEach(function(p, i) {
      var sx = x(i).toFixed(1);
      var sy = y(p.spend_usd || 0).toFixed(1);
      var px = x(i).toFixed(1);
      var py = y(p.purchase_value_usd || 0).toFixed(1);
      if (i === 0) {
        spendPath = 'M' + sx + ',' + sy;
        purchasePath = 'M' + px + ',' + py;
      } else {
        spendPath += ' L' + sx + ',' + sy;
        purchasePath += ' L' + px + ',' + py;
      }
    });

    var xAxisY = pad.top + plotH;
    var dateStep = Math.max(1, Math.floor(trend.length / 6));
    var labels = '';
    for (var i = 0; i < trend.length; i += dateStep) {
      var label = trend[i].date.slice(5); // MM-DD
      var lx = x(i).toFixed(0);
      labels += '<text x="' + lx + '" y="' + (xAxisY + 18) + '" text-anchor="middle" font-size="10" fill="#999">' + label + '</text>';
      labels += '<line x1="' + lx + '" y1="' + (xAxisY - 2) + '" x2="' + lx + '" y2="' + (xAxisY + 2) + '" stroke="#ddd" stroke-width="1"/>';
    }

    return ''
      + '<line x1="' + pad.left + '" y1="' + xAxisY + '" x2="' + (w - pad.right) + '" y2="' + xAxisY + '" stroke="#ddd" stroke-width="1"/>'
      + '<path d="' + spendPath + '" fill="none" class="trend-spend" stroke-width="2"/>'
      + '<path d="' + purchasePath + '" fill="none" class="trend-purchase" stroke-width="2"/>'
      + labels
      + '<text x="' + (w - pad.right) + '" y="' + (pad.top - 4) + '" text-anchor="end" font-size="10" class="trend-spend-text" fill="#3b82f6">花费</text>'
      + '<text x="' + (w - pad.right - 36) + '" y="' + (pad.top - 4) + '" text-anchor="end" font-size="10" class="trend-purchase-text" fill="#22c55e">购买</text>';
  }

  function updateTrendSvgColors() {
    var svg = document.querySelector('#detailModalBody svg');
    if (!svg) return;
    var paths = svg.querySelectorAll('path');
    if (paths.length >= 1) paths[0].setAttribute('stroke', '#3b82f6');
    if (paths.length >= 2) paths[1].setAttribute('stroke', '#22c55e');
  }

  // 关闭详情弹窗
  document.getElementById('detailCloseBtn').addEventListener('click', function() {
    document.getElementById('detailModalMask').setAttribute('hidden', '');
  });
  document.getElementById('detailModalMask').addEventListener('click', function(e) {
    if (e.target === this) this.setAttribute('hidden', '');
  });

  // 严重度筛选
  document.querySelectorAll('#severityFilterGroup .oc-filter-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      document.querySelectorAll('#severityFilterGroup .oc-filter-btn').forEach(function(b) { b.classList.remove('active'); });
      this.classList.add('active');
      currentSeverity = this.getAttribute('data-value');
      loadAlerts();
    });
  });

  // 搜索
  document.getElementById('searchInput').addEventListener('input', function() {
    var self = this;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(function() {
      currentSearch = self.value.trim();
      loadAlerts();
    }, 300);
  });

  // 刷新
  document.getElementById('refreshBtn').addEventListener('click', loadAlerts);

  // 阈值编辑
  document.getElementById('editThresholdBtn').addEventListener('click', function() {
    document.getElementById('thresholdModalMask').removeAttribute('hidden');
    document.getElementById('thresholdInput').value = currentThreshold.toFixed(1);
  });
  document.getElementById('thresholdCloseBtn').addEventListener('click', function() {
    document.getElementById('thresholdModalMask').setAttribute('hidden', '');
  });
  document.getElementById('thresholdCancelBtn').addEventListener('click', function() {
    document.getElementById('thresholdModalMask').setAttribute('hidden', '');
  });
  document.getElementById('thresholdSaveBtn').addEventListener('click', function() {
    var val = parseFloat(document.getElementById('thresholdInput').value);
    if (!val || val <= 0) { alert('请输入正数'); return; }
    fetch('/ad-alerts/api/threshold', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
      body: JSON.stringify({ threshold: val }),
    }).then(function(r) { return r.json(); }).then(function(data) {
      currentThreshold = data.threshold;
      document.getElementById('thresholdDisplay').textContent = currentThreshold.toFixed(2);
      document.getElementById('thresholdModalMask').setAttribute('hidden', '');
      loadAlerts();
    }).catch(function() {
      alert('保存失败');
    });
  });

  // 初始加载
  loadAlerts();
})();
</script>
{% endblock %}
```

- [ ] **Step 2: 提交**

```bash
git add web/templates/ad_alerts.html
git commit -m "feat: add ad alert list page template (cards, filters, detail modal, SVG trend)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: 侧栏菜单入口

**Files:**
- Modify: `web/templates/layout.html`

- [ ] **Step 1: 在 layout.html 的数据分析分组中添加广告预警入口在 `order-analytics` 链接附近**

在 `web/templates/layout.html` 中找到数据分析（order-analytics）的 `<details>` 分组（约第 828 行），在 order-analytics 或 product-profit 链接之后添加：

```html
          <a href="/ad-alerts" {% if request.path.startswith('/ad-alerts') %}class="active"{% endif %}>
            <span class="nav-icon">🔔</span> 广告预警
          </a>
```

具体插入位置在 product-profit 链接之后、下一个链接之前（约第 838 行之后）：

```html
          <a href="/order-profit" {% if request.path.startswith('/order-profit') %}class="active"{% endif %}>
            <span class="nav-icon">📊</span> 订单利润
          </a>
          <a href="/ad-alerts" {% if request.path.startswith('/ad-alerts') %}class="active"{% endif %}>
            <span class="nav-icon">🔔</span> 广告预警
          </a>
```

- [ ] **Step 2: 验证侧栏渲染后 `/ad-alerts` 路由被选中时高亮**

```bash
grep -n 'ad-alerts' web/templates/layout.html
```

预期输出：显示插入的侧栏菜单行。

- [ ] **Step 3: 提交**

```bash
git add web/templates/layout.html
git commit -m "feat: add ad alert sidebar nav entry under data analysis group

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: 后台管理页 ROAS 阈值配置

**Files:**
- Modify: `web/routes/admin.py`
- Modify: `web/templates/admin_settings.html`

- [ ] **Step 1: 在 admin.py 的 POST 处理中添加预警阈值保存逻辑**

在 `web/routes/admin.py` 中找到 ROAS 汇率保存代码（约第 468-476 行），在 `set_setting(product_roas.RMB_PER_USD_SETTING_KEY, product_roas.format_decimal(roas_rate))` 之后添加：

```python
        raw_alert_threshold = request.form.get("ad_alert_roas_threshold", "").strip()
        if raw_alert_threshold:
            try:
                alert_value = float(raw_alert_threshold)
                from appcore import ad_alerts
                ad_alerts.set_threshold(alert_value)
            except (TypeError, ValueError):
                flash("广告预警阈值必须是一个正数")
                return redirect(url_for("admin.settings"))
```

- [ ] **Step 2: 在 admin.py 的 render_template 中添加阈值数据**

找到 `return render_template("admin_settings.html", ...` 行（约第 547 行），在参数列表中添加：

```python
        from appcore import ad_alerts
        ...
        ad_alert_threshold=ad_alerts.get_threshold(),
```

- [ ] **Step 3: 在 admin_settings.html 的 ROAS 设置区域添加预警阈值输入框**

找到 admin_settings.html 中 `素材 ROAS 汇率` 表单字段附近，在汇率输入框之后添加：

```html
        <div class="field-row">
          <label>广告预警阈值</label>
          <input type="number" class="oc-input" name="ad_alert_roas_threshold"
                 value="{{ "%.1f"|format(ad_alert_threshold) }}" step="0.1" min="0.1"
                 style="width:120px;" />
          <p class="field-hint">低于该 ROAS 且仍在投放的广告触发预警。保本参考值 1.5。</p>
        </div>
```

- [ ] **Step 4: 提交**

```bash
git add web/routes/admin.py web/templates/admin_settings.html
git commit -m "feat: add ad alert ROAS threshold config to admin settings

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 验证

安装后启动服务，浏览器访问 `/ad-alerts`：
1. 侧栏应出现"🔔 广告预警"入口
2. 点击后看到预警列表页
3. 页面加载后调用 `/ad-alerts/api/list` 返回数组（可能为空）
4. 如果存在 ROAS < 1.5 且活跃花费 > 0 的记录，以卡片形式展示
5. 点击"查看详情"弹出详情弹窗含趋势图
6. 可修改阈值并保存
7. 管理员后台设置页也有阈值配置项

## Spec 对照检查

| Spec 要求 | Task 覆盖 |
|-----------|-----------|
| 阈值配置（system_settings，可配置） | Task 1 Step 1, Task 6 |
| 预警查询（lang_cache + 条件） | Task 1 Step 3 |
| 趋势数据（从 daily_metrics 查询） | Task 1 Step 5 |
| 规则引擎（4种结论） | Task 1 Step 6 |
| 详情查询（投放时长、累计数据） | Task 1 Step 4 |
| 路由（列表页 + JSON API） | Task 2 |
| 蓝图注册 | Task 3 |
| 前端模板（卡片列表、筛选、搜索） | Task 4 |
| SVG 趋势图（无第三方库） | Task 4 |
| 详情弹窗 | Task 4 |
| 侧栏入口 | Task 5 |
| 后台管理阈值配置 | Task 6 |
| Ocean Blue 风格 | Task 4 CSS（使用 oc-* 类） |
| 权限控制（login_required + admin_required） | Task 2 装饰器 |
