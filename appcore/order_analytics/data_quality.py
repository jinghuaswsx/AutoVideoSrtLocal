"""数据分析看板数据质量护栏。

Docs-anchor: docs/analytics-data-quality-guardrails.md

集中处理：
- 水位查询（订单 / Meta 日终广告 / Meta 实时广告 / 派生利润）
- 跨表对账（源广告费 = 已分摊 + 未分摊）
- 派生数据 stale 判断
- ``data_quality`` 顶层载荷构造

所有 API 输出顶层都应附带 ``build_data_quality(...)`` 的结果，前端缺失时按
``unknown`` 处理。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from appcore.db import query, query_one

from ._constants import META_ATTRIBUTION_TIMEZONE
from ._helpers import _beijing_now, current_meta_business_date
from .ad_market_country import is_single_market_country, normalize_market_country

log = logging.getLogger(__name__)


# ── 状态枚举 ────────────────────────────────────────────────

STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_STALE = "stale"
STATUS_MISMATCH = "mismatch"
STATUS_ERROR = "error"

_STATUS_ORDER = (
    STATUS_OK,
    STATUS_WARNING,
    STATUS_STALE,
    STATUS_MISMATCH,
    STATUS_ERROR,
)
_STATUS_RANK = {status: index for index, status in enumerate(_STATUS_ORDER)}


SOURCE_MODE_DAILY_FINAL = "daily_final"
SOURCE_MODE_REALTIME_SNAPSHOT = "realtime_snapshot"
SOURCE_MODE_MIXED = "mixed"
SOURCE_MODE_DERIVED_CACHE = "derived_cache"
SOURCE_MODE_UNKNOWN = "unknown"

# 跨表对账容忍阈值（USD）
AD_SPEND_RECONCILE_TOLERANCE_USD = 0.5

# 派生数据视为 stale 的阈值（源表完成后多久未重算视为滞后）
DERIVED_PROFIT_STALE_DELTA = timedelta(minutes=30)


# ── 时间格式化 ──────────────────────────────────────────────

def _now_iso() -> str:
    return _beijing_now().replace(microsecond=0).isoformat()


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(microsecond=0).isoformat()
        return value.astimezone(ZoneInfo(META_ATTRIBUTION_TIMEZONE)).replace(
            microsecond=0
        ).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _ensure_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    # 统一转到北京时间裸 datetime 用于比较
    return value.astimezone(ZoneInfo(META_ATTRIBUTION_TIMEZONE)).replace(tzinfo=None)


# ── 核心：构造 data_quality 顶层对象 ────────────────────────

def _worst_status(check_statuses: Iterable[str]) -> str:
    worst = STATUS_OK
    for status in check_statuses:
        if not status:
            continue
        if _STATUS_RANK.get(status, 0) > _STATUS_RANK.get(worst, 0):
            worst = status
    return worst


def _split_warnings_errors(checks: list[dict]) -> tuple[list[dict], list[dict]]:
    warnings: list[dict] = []
    errors: list[dict] = []
    for check in checks:
        status = check.get("status")
        if status in (STATUS_WARNING, STATUS_STALE):
            warnings.append({
                "code": check.get("code"),
                "status": status,
                "message": check.get("message"),
            })
        elif status in (STATUS_MISMATCH, STATUS_ERROR):
            errors.append({
                "code": check.get("code"),
                "status": status,
                "message": check.get("message"),
                "diff": check.get("diff"),
            })
    return warnings, errors


def build_data_quality(
    *,
    business_date_from: date,
    business_date_to: date,
    source_mode: str = SOURCE_MODE_UNKNOWN,
    checks: list[dict] | None = None,
    watermarks: dict | None = None,
    generated_at: datetime | str | None = None,
) -> dict:
    """构造统一的 ``data_quality`` 顶层对象。

    - ``status`` 由 ``checks`` 中状态最差的一项推导；无 checks 且 source_mode 未知
      时强制降级为 warning，避免静默 ok。
    - ``warnings`` 收集 warning + stale；``errors`` 收集 mismatch + error。
    """

    checks_list = list(checks or [])
    if checks_list:
        status = _worst_status(check.get("status") for check in checks_list)
    elif source_mode == SOURCE_MODE_UNKNOWN:
        status = STATUS_WARNING
    else:
        status = STATUS_OK

    warnings, errors = _split_warnings_errors(checks_list)

    if isinstance(generated_at, datetime):
        generated_iso = _isoformat(generated_at)
    elif isinstance(generated_at, str) and generated_at:
        generated_iso = generated_at
    else:
        generated_iso = _now_iso()

    return {
        "status": status,
        "source_mode": source_mode,
        "business_date_from": business_date_from.isoformat(),
        "business_date_to": business_date_to.isoformat(),
        "generated_at": generated_iso,
        "watermarks": watermarks or {},
        "checks": checks_list,
        "warnings": warnings,
        "errors": errors,
    }


# ── 水位查询 ────────────────────────────────────────────────

def _fetch_orders_watermark() -> dict:
    row = query_one(
        "SELECT MAX(meta_business_date) AS latest_business_date, "
        "       MAX(updated_at) AS latest_updated_at "
        "FROM dianxiaomi_order_lines"
    ) or {}
    return {
        "latest_business_date": _isoformat(row.get("latest_business_date")),
        "latest_updated_at": _isoformat(row.get("latest_updated_at")),
    }


def _fetch_meta_daily_ads_watermark() -> dict:
    row = query_one(
        "SELECT MAX(COALESCE(meta_business_date, report_date)) AS latest_business_date, "
        "       MAX(updated_at) AS latest_import_finished_at "
        "FROM meta_ad_daily_campaign_metrics"
    ) or {}
    return {
        "latest_business_date": _isoformat(row.get("latest_business_date")),
        "latest_import_finished_at": _isoformat(row.get("latest_import_finished_at")),
    }


def _fetch_meta_realtime_ads_watermark() -> dict:
    row = query_one(
        "SELECT MAX(business_date) AS latest_business_date, "
        "       MAX(snapshot_at) AS latest_snapshot_at "
        "FROM meta_ad_realtime_daily_campaign_metrics"
    ) or {}
    return {
        "latest_business_date": _isoformat(row.get("latest_business_date")),
        "latest_snapshot_at": _isoformat(row.get("latest_snapshot_at")),
    }


def _fetch_derived_profit_watermark() -> dict:
    row = query_one(
        "SELECT MAX(d.meta_business_date) AS latest_business_date, "
        "       MAX(p.updated_at) AS latest_run_finished_at "
        "FROM order_profit_lines p "
        "JOIN dianxiaomi_order_lines d ON d.id = p.dxm_order_line_id"
    ) or {}
    return {
        "latest_business_date": _isoformat(row.get("latest_business_date")),
        "latest_run_finished_at": _isoformat(row.get("latest_run_finished_at")),
    }


def fetch_watermarks() -> dict:
    """查询四类水位：订单、日终广告、实时广告、派生利润。

    任何一类查询失败时返回空 dict（避免拖垮 API 主路径）。
    """
    watermarks: dict[str, dict] = {}
    for key, fn in (
        ("orders", _fetch_orders_watermark),
        ("meta_daily_ads", _fetch_meta_daily_ads_watermark),
        ("meta_realtime_ads", _fetch_meta_realtime_ads_watermark),
        ("derived_profit", _fetch_derived_profit_watermark),
    ):
        try:
            watermarks[key] = fn()
        except Exception as exc:  # noqa: BLE001
            log.warning("data_quality watermark %s failed: %s", key, exc)
            watermarks[key] = {}
    return watermarks


# ── 源数据模式判定 ──────────────────────────────────────────

def resolve_source_mode(
    *,
    business_date_from: date,
    business_date_to: date,
    country: str | None = None,
) -> str:
    """根据日期范围内日终 / 实时表的覆盖情况判断数据源模式。"""
    market_country = normalize_market_country(country)
    daily_table = (
        "meta_ad_daily_ad_metrics"
        if is_single_market_country(market_country)
        else "meta_ad_daily_campaign_metrics"
    )
    realtime_table = (
        "meta_ad_realtime_daily_ad_metrics"
        if is_single_market_country(market_country)
        else "meta_ad_realtime_daily_campaign_metrics"
    )
    daily_where = ""
    realtime_where = ""
    daily_args: list[Any] = [business_date_from, business_date_to]
    realtime_args: list[Any] = [business_date_from, business_date_to]
    if is_single_market_country(market_country):
        daily_where = " AND market_country = %s"
        realtime_where = " AND UPPER(COALESCE(country_code, '')) = %s"
        daily_args.append(market_country)
        realtime_args.append(market_country)
    try:
        daily_rows = query(
            "SELECT DISTINCT COALESCE(meta_business_date, report_date) AS business_date "
            f"FROM {daily_table} "
            "WHERE COALESCE(meta_business_date, report_date) BETWEEN %s AND %s"
            f"{daily_where}",
            tuple(daily_args),
        ) or []
        realtime_rows = query(
            "SELECT DISTINCT business_date "
            f"FROM {realtime_table} "
            "WHERE business_date BETWEEN %s AND %s"
            f"{realtime_where}",
            tuple(realtime_args),
        ) or []
    except Exception as exc:  # noqa: BLE001
        log.warning("data_quality resolve_source_mode query failed: %s", exc)
        return SOURCE_MODE_UNKNOWN

    daily_dates = {
        parsed
        for row in daily_rows
        if (parsed := _date_from_value(row.get("business_date"))) is not None
    }
    realtime_dates = {
        parsed
        for row in realtime_rows
        if (parsed := _date_from_value(row.get("business_date"))) is not None
    }

    expected = _expected_dates(business_date_from, business_date_to)
    if expected and daily_dates >= expected:
        return SOURCE_MODE_DAILY_FINAL
    if daily_dates and realtime_dates and not (daily_dates >= expected):
        return SOURCE_MODE_MIXED
    if not daily_dates and realtime_dates:
        return SOURCE_MODE_REALTIME_SNAPSHOT
    if daily_dates and not realtime_dates:
        # 部分日期有日终但未覆盖全部范围
        if daily_dates >= expected:
            return SOURCE_MODE_DAILY_FINAL
        return SOURCE_MODE_MIXED
    return SOURCE_MODE_UNKNOWN


def _expected_dates(date_from: date, date_to: date) -> set[date]:
    if date_from > date_to:
        return set()
    days: set[date] = set()
    cursor = date_from
    while cursor <= date_to:
        days.add(cursor)
        cursor += timedelta(days=1)
    return days


# ── 跨表对账：广告费 ────────────────────────────────────────

def _date_from_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _source_mode_from_breakdown(daily_total: float, realtime_total: float) -> str:
    has_daily = abs(float(daily_total or 0)) > 0.0001
    has_realtime = abs(float(realtime_total or 0)) > 0.0001
    if has_daily and has_realtime:
        return SOURCE_MODE_MIXED
    if has_realtime:
        return SOURCE_MODE_REALTIME_SNAPSHOT
    if has_daily:
        return SOURCE_MODE_DAILY_FINAL
    return SOURCE_MODE_UNKNOWN


def _latest_realtime_totals(
    *,
    table_name: str,
    date_from: date,
    date_to: date,
    country: str | None = None,
) -> tuple[float, float]:
    """读取开放业务日最新 snapshot 总广告费。

    返回 ``(source_total, product_null_total)``。product_null 只用于调用方未显式传
    unallocated 时的保守 fallback；完整未分摊口径仍应由业务聚合层传入。
    """

    country_filter = ""
    params: list[Any] = [date_from, date_to, date_from, date_to]
    market_country = normalize_market_country(country)
    if table_name == "meta_ad_realtime_daily_ad_metrics" and is_single_market_country(market_country):
        country_filter = " AND UPPER(COALESCE(m.country_code, '')) = %s"
        params.append(market_country)

    rows = query(
        "SELECT COALESCE(SUM(m.spend_usd), 0) AS source_total, "
        "       0 AS unallocated_total "
        f"FROM {table_name} m "
        "INNER JOIN ("
        "  SELECT business_date, ad_account_id, MAX(snapshot_at) AS snapshot_at "
        f"  FROM {table_name} "
        "  WHERE business_date BETWEEN %s AND %s "
        "    AND data_completeness='realtime_partial' "
        "  GROUP BY business_date, ad_account_id"
        ") latest "
        "ON m.business_date = latest.business_date "
        "AND (m.ad_account_id = latest.ad_account_id "
        "     OR (m.ad_account_id IS NULL AND latest.ad_account_id IS NULL)) "
        "AND m.snapshot_at = latest.snapshot_at "
        "WHERE m.business_date BETWEEN %s AND %s "
        "  AND m.data_completeness='realtime_partial'"
        f"{country_filter}",
        tuple(params),
    ) or []
    row = (rows[0] if rows else {}) or {}
    return float(row.get("source_total") or 0), float(row.get("unallocated_total") or 0)


def load_source_ad_spend_totals(
    *,
    business_date_from: date,
    business_date_to: date,
    country: str | None = None,
    product_id: int | None = None,
) -> dict[str, Any]:
    """按会计对账口径读取源广告费总账。

    Docs-anchor:
    docs/superpowers/specs/2026-06-13-accounting-reconcilable-analytics-profit-remediation.md#t1-统一广告费来源与-data_quality-对账

    历史已收盘业务日读 daily 表；未收盘业务日读最新 realtime snapshot。国家维度
    使用 ad 层表，全部国家使用 campaign 层表。
    """

    from tools.meta_daily_final_sync import completed_meta_business_date

    market_country = normalize_market_country(country)
    single_country = is_single_market_country(market_country)
    closed_through = completed_meta_business_date()
    daily_to = min(business_date_to, closed_through)
    realtime_from = max(business_date_from, closed_through + timedelta(days=1))

    daily_total = 0.0
    daily_unallocated = 0.0
    realtime_total = 0.0
    realtime_unallocated = 0.0
    checks: list[dict[str, Any]] = []

    if daily_to >= business_date_from:
        product_filter = ""
        product_args: list[Any] = []
        if product_id is not None:
            product_filter = " AND product_id = %s"
            product_args.append(int(product_id))
        if single_country:
            rows = query(
                "SELECT COALESCE(SUM(spend_usd), 0) AS source_total, "
                "       COALESCE(SUM(CASE WHEN product_id IS NULL THEN spend_usd ELSE 0 END), 0) "
                "         AS unallocated_total "
                "FROM meta_ad_daily_ad_metrics "
                "WHERE COALESCE(meta_business_date, report_date) BETWEEN %s AND %s "
                "  AND market_country = %s"
                f"{product_filter}",
                (business_date_from, daily_to, market_country, *product_args),
            ) or []
        else:
            rows = query(
                "SELECT COALESCE(SUM(spend_usd), 0) AS source_total, "
                "       COALESCE(SUM(CASE WHEN product_id IS NULL THEN spend_usd ELSE 0 END), 0) "
                "         AS unallocated_total "
                "FROM meta_ad_daily_campaign_metrics "
                "WHERE COALESCE(meta_business_date, report_date) BETWEEN %s AND %s"
                f"{product_filter}",
                (business_date_from, daily_to, *product_args),
            ) or []
        row = (rows[0] if rows else {}) or {}
        daily_total = float(row.get("source_total") or 0)
        daily_unallocated = (
            0.0
            if product_id is not None
            else float(row.get("unallocated_total") or 0)
        )

    if realtime_from <= business_date_to:
        if product_id is not None:
            from .order_profit_aggregation import _load_realtime_ad_snapshot_fallback

            rt = _load_realtime_ad_snapshot_fallback(
                date_from=realtime_from,
                date_to=business_date_to,
                product_id=int(product_id),
                country=market_country,
            )
            realtime_total = float(
                sum(float(v or 0) for v in (rt.get("spend_by_product") or {}).values())
            ) + float(rt.get("unallocated_spend") or 0)
            realtime_unallocated = float(rt.get("unallocated_spend") or 0)
        else:
            realtime_table = (
                "meta_ad_realtime_daily_ad_metrics"
                if single_country
                else "meta_ad_realtime_daily_campaign_metrics"
            )
            realtime_total, realtime_unallocated = _latest_realtime_totals(
                table_name=realtime_table,
                date_from=realtime_from,
                date_to=business_date_to,
                country=market_country,
            )
        if realtime_total <= 0:
            checks.append({
                "code": "realtime_ad_source_missing",
                "status": STATUS_WARNING,
                "message": "开放业务日尚无可用于广告对账的实时快照",
            })

    source_total = daily_total + realtime_total
    source_mode = _source_mode_from_breakdown(daily_total, realtime_total)
    return {
        "source_total": source_total,
        "unallocated_total": daily_unallocated + realtime_unallocated,
        "source_mode": source_mode,
        "daily_total": daily_total,
        "realtime_total": realtime_total,
        "daily_unallocated_total": daily_unallocated,
        "realtime_unallocated_total": realtime_unallocated,
        "checks": checks,
    }


def reconcile_ad_spend(
    *,
    business_date_from: date,
    business_date_to: date,
    allocated_ad_spend_usd: float,
    unallocated_ad_spend_usd: float | None = None,
    country: str | None = None,
    product_id: int | None = None,
    tolerance_usd: float = AD_SPEND_RECONCILE_TOLERANCE_USD,
) -> dict:
    """校验：``源广告费 = 已分摊 + 未分摊``。

    入参 ``allocated_ad_spend_usd`` 是调用方现场重算或从聚合接口拿到的
    "已分摊到订单/产品的广告费"。本函数自己负责取源表 + 未分摊金额。
    """
    try:
        source_totals = load_source_ad_spend_totals(
            business_date_from=business_date_from,
            business_date_to=business_date_to,
            country=country,
            product_id=product_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("data_quality reconcile_ad_spend query failed: %s", exc)
        return {
            "code": "ad_spend_reconciled",
            "status": STATUS_WARNING,
            "expected": None,
            "actual": None,
            "diff": None,
                "message": f"广告对账查询失败：{exc}",
        }

    source_total = float(source_totals.get("source_total") or 0)
    unallocated_total = (
        float(unallocated_ad_spend_usd)
        if unallocated_ad_spend_usd is not None
        else float(source_totals.get("unallocated_total") or 0)
    )
    allocated = float(allocated_ad_spend_usd or 0)
    actual = round(allocated + unallocated_total, 2)
    expected = round(source_total, 2)
    diff = round(abs(actual - expected), 2)

    if expected <= 0 and allocated <= 0 and unallocated_total <= 0:
        return {
            "code": "ad_spend_reconciled",
            "status": STATUS_WARNING,
            "expected": expected,
            "actual": actual,
            "diff": diff,
            "allocated_ad_spend_usd": round(allocated, 2),
            "unallocated_ad_spend_usd": round(unallocated_total, 2),
            "source_mode": source_totals.get("source_mode") or SOURCE_MODE_UNKNOWN,
            "source_breakdown": {
                "daily_total_usd": round(float(source_totals.get("daily_total") or 0), 2),
                "realtime_total_usd": round(float(source_totals.get("realtime_total") or 0), 2),
            },
            "message": "选定业务日尚无可用于广告对账的源数据",
        }

    source_checks = list(source_totals.get("checks") or [])
    if source_checks and diff <= tolerance_usd:
        warning = source_checks[0]
        return {
            "code": "ad_spend_reconciled",
            "status": warning.get("status") or STATUS_WARNING,
            "expected": expected,
            "actual": actual,
            "diff": diff,
            "allocated_ad_spend_usd": round(allocated, 2),
            "unallocated_ad_spend_usd": round(unallocated_total, 2),
            "source_mode": source_totals.get("source_mode") or SOURCE_MODE_UNKNOWN,
            "source_breakdown": {
                "daily_total_usd": round(float(source_totals.get("daily_total") or 0), 2),
                "realtime_total_usd": round(float(source_totals.get("realtime_total") or 0), 2),
            },
            "message": warning.get("message") or "广告源数据不完整",
        }

    if diff <= tolerance_usd:
        return {
            "code": "ad_spend_reconciled",
            "status": STATUS_OK,
            "expected": expected,
            "actual": actual,
            "diff": diff,
            "allocated_ad_spend_usd": round(allocated, 2),
            "unallocated_ad_spend_usd": round(unallocated_total, 2),
            "source_mode": source_totals.get("source_mode") or SOURCE_MODE_UNKNOWN,
            "source_breakdown": {
                "daily_total_usd": round(float(source_totals.get("daily_total") or 0), 2),
                "realtime_total_usd": round(float(source_totals.get("realtime_total") or 0), 2),
            },
            "message": "广告源表总额与已分摊+未分摊金额一致",
        }

    return {
        "code": "ad_spend_reconciled",
        "status": STATUS_MISMATCH,
        "expected": expected,
        "actual": actual,
        "diff": diff,
        "allocated_ad_spend_usd": round(allocated, 2),
        "unallocated_ad_spend_usd": round(unallocated_total, 2),
        "source_mode": source_totals.get("source_mode") or SOURCE_MODE_UNKNOWN,
        "source_breakdown": {
            "daily_total_usd": round(float(source_totals.get("daily_total") or 0), 2),
            "realtime_total_usd": round(float(source_totals.get("realtime_total") or 0), 2),
        },
        "message": (
            f"广告对账失败：源表 {expected:.2f} ≠ 已分摊 {allocated:.2f} + 未分摊 "
            f"{unallocated_total:.2f}"
        ),
    }


def _query_meta_ad_day_uniqueness(
    table_name: str,
    entity_column: str,
    *,
    date_from: date,
    date_to: date,
) -> dict:
    return query_one(
        "SELECT COUNT(*) AS duplicate_groups, "
        "       COALESCE(SUM(affected_spend), 0) AS affected_spend "
        "FROM ("
        f"  SELECT ad_account_id, report_start_date, {entity_column} AS entity_name, "
        "         COUNT(DISTINCT COALESCE(meta_business_date, report_date)) AS business_dates, "
        "         COALESCE(SUM(spend_usd), 0) AS affected_spend, "
        "         SUM(CASE WHEN ("
        "                    (report_start_date IS NOT NULL "
        "                     AND report_start_date <> COALESCE(meta_business_date, report_date)) "
        "                    OR (report_end_date IS NOT NULL "
        "                        AND report_end_date <> COALESCE(meta_business_date, report_date))"
        "                  ) AND COALESCE(spend_usd, 0) > 0 "
        "                  THEN 1 ELSE 0 END) AS off_target_rows "
        f"  FROM {table_name} "
        "  WHERE COALESCE(meta_business_date, report_date) BETWEEN %s AND %s "
        f"  GROUP BY ad_account_id, report_start_date, {entity_column} "
        "  HAVING affected_spend > 0 "
        "     AND (business_dates > 1 OR off_target_rows > 0)"
        ") bad",
        (date_from, date_to),
    ) or {}


def check_meta_ad_day_uniqueness(
    *,
    business_date_from: date,
    business_date_to: date,
) -> dict:
    """检查 Meta daily 表是否把同一广告自然日写进多个业务日。

    Docs-anchor: docs/superpowers/specs/2026-05-10-meta-ads-one-row-per-ad-day.md
    """
    try:
        campaign_row = _query_meta_ad_day_uniqueness(
            "meta_ad_daily_campaign_metrics",
            "campaign_name",
            date_from=business_date_from,
            date_to=business_date_to,
        )
        ad_row = _query_meta_ad_day_uniqueness(
            "meta_ad_daily_ad_metrics",
            "ad_name",
            date_from=business_date_from,
            date_to=business_date_to,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("data_quality meta ad day uniqueness query failed: %s", exc)
        return {
            "code": "meta_ad_day_uniqueness",
            "status": STATUS_WARNING,
            "message": f"Meta 广告自然日唯一性查询失败：{exc}",
        }

    campaign_groups = int(campaign_row.get("duplicate_groups") or 0)
    ad_groups = int(ad_row.get("duplicate_groups") or 0)
    affected_spend = round(
        float(campaign_row.get("affected_spend") or 0)
        + float(ad_row.get("affected_spend") or 0),
        2,
    )
    duplicate_groups = campaign_groups + ad_groups

    if duplicate_groups <= 0:
        return {
            "code": "meta_ad_day_uniqueness",
            "status": STATUS_OK,
            "duplicate_groups": 0,
            "affected_spend_usd": 0.0,
            "message": "Meta 广告自然日未发现跨业务日重复",
        }

    return {
        "code": "meta_ad_day_uniqueness",
        "status": STATUS_MISMATCH,
        "duplicate_groups": duplicate_groups,
        "campaign_duplicate_groups": campaign_groups,
        "ad_duplicate_groups": ad_groups,
        "affected_spend_usd": affected_spend,
        "message": (
            "Meta 广告日表存在跨业务日重复或错挂：每个广告自然日只能保留一份，"
            f"受影响分组 {duplicate_groups} 个，涉及广告费约 {affected_spend:.2f}"
        ),
    }


# ── 派生数据新鲜度 ──────────────────────────────────────────

def check_derived_profit_freshness(
    *,
    business_date_from: date,
    business_date_to: date,
    grace_period: timedelta = DERIVED_PROFIT_STALE_DELTA,
) -> dict:
    """对比 ``meta_ad_daily_campaign_metrics`` 与 ``order_profit_lines`` 时间戳。

    若日终广告表在派生表之后更新，且超过 grace_period，标记为 stale。
    """
    try:
        source_row = query_one(
            "SELECT MAX(updated_at) AS latest_finished "
            "FROM meta_ad_daily_campaign_metrics "
            "WHERE COALESCE(meta_business_date, report_date) BETWEEN %s AND %s",
            (business_date_from, business_date_to),
        ) or {}
        derived_row = query_one(
            "SELECT MAX(p.updated_at) AS latest_run "
            "FROM order_profit_lines p "
            "JOIN dianxiaomi_order_lines d ON d.id = p.dxm_order_line_id "
            "WHERE d.meta_business_date BETWEEN %s AND %s",
            (business_date_from, business_date_to),
        ) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("data_quality derived freshness query failed: %s", exc)
        return {
            "code": "derived_profit_freshness",
            "status": STATUS_WARNING,
            "message": f"派生表新鲜度查询失败：{exc}",
        }

    source_at = _ensure_naive(source_row.get("latest_finished"))
    derived_at = _ensure_naive(derived_row.get("latest_run"))

    if source_at is None and derived_at is None:
        return {
            "code": "derived_profit_freshness",
            "status": STATUS_WARNING,
            "message": "派生表与源表均无数据",
            "source_at": None,
            "derived_at": None,
        }

    if source_at is None:
        return {
            "code": "derived_profit_freshness",
            "status": STATUS_OK,
            "message": "源表无新数据，派生表保持最新",
            "source_at": None,
            "derived_at": _isoformat(derived_at),
        }

    if derived_at is None:
        return {
            "code": "derived_profit_freshness",
            "status": STATUS_STALE,
            "message": "源表已就绪但派生利润行尚未生成",
            "source_at": _isoformat(source_at),
            "derived_at": None,
        }

    if source_at - derived_at > grace_period:
        return {
            "code": "derived_profit_freshness",
            "status": STATUS_STALE,
            "message": (
                f"日终广告表 {_isoformat(source_at)} 晚于派生利润行 "
                f"{_isoformat(derived_at)}，可能尚未重算"
            ),
            "source_at": _isoformat(source_at),
            "derived_at": _isoformat(derived_at),
        }

    return {
        "code": "derived_profit_freshness",
        "status": STATUS_OK,
        "message": "派生利润行已覆盖最新源表",
        "source_at": _isoformat(source_at),
        "derived_at": _isoformat(derived_at),
    }


PAYMENTS_STALE_DAYS = 9          # 每周手动导一次，超过约 9 天未导入即告警
EXCHANGE_RATE_STALE_DAYS = 2     # 日汇率应每日同步


def check_payments_freshness(*, today: date | None = None, stale_days: int = PAYMENTS_STALE_DAYS) -> dict:
    """Shopify Payments 断更监控：用 imported_at（transaction_date 当前全 NULL）。"""
    today = today or current_meta_business_date()
    try:
        row = query_one(
            "SELECT MAX(imported_at) AS latest_import FROM shopify_payments_transactions"
        ) or {}
    except Exception as exc:  # noqa: BLE001
        return {"code": "payments_freshness", "status": STATUS_WARNING,
                "message": f"payments 水位查询失败：{exc}"}
    latest = _ensure_naive(row.get("latest_import"))
    if latest is None:
        return {"code": "payments_freshness", "status": STATUS_WARNING,
                "message": "无 Shopify Payments 数据，手续费全部走估算"}
    lag_days = (today - latest.date()).days
    if lag_days > stale_days:
        return {"code": "payments_freshness", "status": STATUS_STALE, "lag_days": lag_days,
                "latest_import_at": _isoformat(latest),
                "message": f"Shopify Payments 已 {lag_days} 天未导入，请上传最新 Payments/Transactions CSV"}
    return {"code": "payments_freshness", "status": STATUS_OK, "lag_days": lag_days,
            "latest_import_at": _isoformat(latest), "message": "payments 数据新鲜"}


def check_exchange_rate_freshness(*, today: date | None = None, stale_days: int = EXCHANGE_RATE_STALE_DAYS) -> dict:
    """日汇率断更监控：usd_cny_daily_exchange_rates 最新 rate_date 距今天数。"""
    today = today or current_meta_business_date()
    try:
        row = query_one("SELECT MAX(rate_date) AS latest FROM usd_cny_daily_exchange_rates") or {}
    except Exception as exc:  # noqa: BLE001
        return {"code": "exchange_rate_freshness", "status": STATUS_WARNING,
                "message": f"汇率水位查询失败：{exc}"}
    latest = _date_from_value(row.get("latest"))
    if latest is None:
        return {"code": "exchange_rate_freshness", "status": STATUS_WARNING,
                "message": "无日汇率数据，采购/物流换算走配置 fallback"}
    lag_days = (today - latest).days
    if lag_days > stale_days:
        return {"code": "exchange_rate_freshness", "status": STATUS_STALE, "lag_days": lag_days,
                "latest_rate_date": latest.isoformat(),
                "message": f"日汇率最新 {latest.isoformat()}，距今 {lag_days} 天未同步"}
    return {"code": "exchange_rate_freshness", "status": STATUS_OK, "lag_days": lag_days,
            "latest_rate_date": latest.isoformat(), "message": "日汇率新鲜"}


# ── 高层 helpers：为各页面构造 data_quality ─────────────────


def build_for_order_profit(
    *,
    date_from: date,
    date_to: date,
    allocated_ad_spend_usd: float | None = None,
    unallocated_ad_spend_usd: float | None = None,
    product_id: int | None = None,
) -> dict:
    """``/order-profit/api/*`` 的统一入口。"""
    checks: list[dict] = []
    if allocated_ad_spend_usd is not None:
        checks.append(
            reconcile_ad_spend(
                business_date_from=date_from,
                business_date_to=date_to,
                allocated_ad_spend_usd=allocated_ad_spend_usd,
                unallocated_ad_spend_usd=unallocated_ad_spend_usd,
                product_id=product_id,
            )
        )
    checks.append(
        check_meta_ad_day_uniqueness(
            business_date_from=date_from,
            business_date_to=date_to,
        )
    )
    checks.append(
        check_derived_profit_freshness(
            business_date_from=date_from,
            business_date_to=date_to,
        )
    )
    checks.append(check_payments_freshness())
    checks.append(check_exchange_rate_freshness())
    return build_data_quality(
        business_date_from=date_from,
        business_date_to=date_to,
        source_mode=resolve_source_mode(
            business_date_from=date_from,
            business_date_to=date_to,
        ),
        checks=checks,
        watermarks=fetch_watermarks(),
    )


def build_for_realtime_overview(
    *,
    business_date: date,
    business_date_to: date | None = None,
    source_mode: str,
    last_order_at: datetime | None = None,
    last_ad_snapshot_at: datetime | None = None,
    snapshot_grace_period: timedelta = timedelta(minutes=45),
) -> dict:
    """``/order-analytics/realtime-overview`` 的统一入口。

    比较订单截止时间和广告快照时间；如广告快照明显早于订单截止时间，标记 warning。
    """
    business_date_end = business_date_to or business_date
    checks: list[dict] = []
    last_order_at = _ensure_naive(last_order_at)
    last_ad_snapshot_at = _ensure_naive(last_ad_snapshot_at)
    if last_order_at and last_ad_snapshot_at:
        if last_order_at - last_ad_snapshot_at > snapshot_grace_period:
            checks.append({
                "code": "realtime_ad_snapshot_lag",
                "status": STATUS_WARNING,
                "message": (
                    f"广告快照 {_isoformat(last_ad_snapshot_at)} 早于订单截止 "
                    f"{_isoformat(last_order_at)} 超过 "
                    f"{int(snapshot_grace_period.total_seconds() // 60)} 分钟"
                ),
            })
    if source_mode == SOURCE_MODE_REALTIME_SNAPSHOT:
        checks.append({
            "code": "using_realtime_fallback",
            "status": STATUS_WARNING,
            "message": "日终广告表暂未生成，使用实时快照兜底",
        })
    elif source_mode == SOURCE_MODE_MIXED:
        checks.append({
            "code": "using_mixed_ad_sources",
            "status": STATUS_WARNING,
            "message": "日期范围内部分业务日使用日终广告表，部分业务日使用实时快照兜底",
        })
    checks.append(
        check_meta_ad_day_uniqueness(
            business_date_from=business_date,
            business_date_to=business_date_end,
        )
    )
    return build_data_quality(
        business_date_from=business_date,
        business_date_to=business_date_end,
        source_mode=source_mode or SOURCE_MODE_UNKNOWN,
        checks=checks,
        watermarks=fetch_watermarks(),
    )


def build_for_product_profit(
    *,
    date_from: date,
    date_to: date,
    allocated_ad_spend_usd: float | None = None,
    unallocated_ad_spend_usd: float | None = None,
    country: str | None = None,
    product_id: int | None = None,
) -> dict:
    """``/order-analytics/product-profit/*`` 的统一入口。"""
    checks: list[dict] = []
    if allocated_ad_spend_usd is not None:
        checks.append(
            reconcile_ad_spend(
                business_date_from=date_from,
                business_date_to=date_to,
                allocated_ad_spend_usd=allocated_ad_spend_usd,
                unallocated_ad_spend_usd=unallocated_ad_spend_usd,
                country=country,
                product_id=product_id,
            )
        )
    checks.append(
        check_meta_ad_day_uniqueness(
            business_date_from=date_from,
            business_date_to=date_to,
        )
    )
    return build_data_quality(
        business_date_from=date_from,
        business_date_to=date_to,
        source_mode=resolve_source_mode(
            business_date_from=date_from,
            business_date_to=date_to,
            country=country,
        ),
        checks=checks,
        watermarks=fetch_watermarks(),
    )


# ── 巡检：跨页面差异 ────────────────────────────────────────


def run_recent_inspection(*, lookback_days: int = 7, today: date | None = None) -> dict:
    """供 ``appcore/scheduled_tasks.py`` 调用的巡检入口。

    扫描最近 ``lookback_days`` 个业务日，对每天做：
    - 广告费跨表对账
    - 派生数据新鲜度判断

    返回结构便于写日志或后续展示给前端。
    """
    today = today or current_meta_business_date()
    days: list[dict] = []
    overall_status = STATUS_OK
    for offset in range(lookback_days):
        target = today - timedelta(days=offset)
        try:
            recon = reconcile_ad_spend(
                business_date_from=target,
                business_date_to=target,
                allocated_ad_spend_usd=_query_allocated_ad_spend(target, target),
            )
            freshness = check_derived_profit_freshness(
                business_date_from=target,
                business_date_to=target,
            )
            uniqueness = check_meta_ad_day_uniqueness(
                business_date_from=target,
                business_date_to=target,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("data_quality inspection %s failed: %s", target, exc)
            days.append({
                "business_date": target.isoformat(),
                "status": STATUS_ERROR,
                "message": str(exc),
            })
            overall_status = STATUS_ERROR
            continue
        worst = _worst_status([
            recon.get("status"),
            freshness.get("status"),
            uniqueness.get("status"),
        ])
        if _STATUS_RANK.get(worst, 0) > _STATUS_RANK.get(overall_status, 0):
            overall_status = worst
        days.append({
            "business_date": target.isoformat(),
            "status": worst,
            "checks": [recon, freshness, uniqueness],
        })

    payments_check = check_payments_freshness(today=today)
    exchange_check = check_exchange_rate_freshness(today=today)
    for extra in (payments_check, exchange_check):
        if _STATUS_RANK.get(extra.get("status"), 0) > _STATUS_RANK.get(overall_status, 0):
            overall_status = extra["status"]

    return {
        "generated_at": _now_iso(),
        "lookback_days": lookback_days,
        "status": overall_status,
        "days": days,
        "freshness": [payments_check, exchange_check],
    }


def _query_allocated_ad_spend(date_from: date, date_to: date) -> float:
    try:
        row = query_one(
            "SELECT COALESCE(SUM(ad_cost_usd), 0) AS total "
            "FROM order_profit_lines p "
            "JOIN dianxiaomi_order_lines d ON d.id = p.dxm_order_line_id "
            "WHERE d.meta_business_date BETWEEN %s AND %s",
            (date_from, date_to),
        ) or {}
        return float(row.get("total") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("data_quality allocated_ad_spend query failed: %s", exc)
        return 0.0
