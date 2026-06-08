from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable

from appcore import media_product_ad_status_cache
from appcore.db import execute, query
from appcore.order_analytics import current_meta_business_date

STATUS_STABLE = "stable"
STATUS_SECONDARY_STABLE = "secondary_stable"
STATUS_POTENTIAL = "potential"
STATUS_TEST = "test"
STATUS_STOPPED = "stopped"
STATUS_NEVER = "never"
STATUS_INSUFFICIENT_HISTORY = "insufficient_history"

STATUS_LABELS = {
    STATUS_STABLE: "稳定品",
    STATUS_SECONDARY_STABLE: "二级稳定品",
    STATUS_POTENTIAL: "潜力品",
    STATUS_TEST: "测试品",
    STATUS_STOPPED: "已停投",
    STATUS_NEVER: "未投放",
    STATUS_INSUFFICIENT_HISTORY: "投放未满7天",
}

FILTER_ALL = "all"
FILTER_STABLE_7D = "stable_7d"
FILTER_STABLE_30D = "stable_30d"

STABILITY_STATUS_FILTERS = (
    FILTER_ALL,
    STATUS_STABLE,
    FILTER_STABLE_7D,
    FILTER_STABLE_30D,
    STATUS_SECONDARY_STABLE,
    STATUS_TEST,
    STATUS_STOPPED,
    STATUS_NEVER,
    STATUS_INSUFFICIENT_HISTORY,
)


def normalize_stability_status_filter(value: Any) -> str:
    normalized = str(value or FILTER_ALL).strip().lower()
    return normalized if normalized in STABILITY_STATUS_FILTERS else FILTER_ALL


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _nullable_float(value: Any) -> float | None:
    if value is None:
        return None
    return _safe_float(value)


def _iso(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value) if value else None


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _round_money(value: Any) -> float:
    return round(_safe_float(value), 2)


def _round_avg(value: float) -> float:
    return round(float(value or 0), 2)


def _product_ids(product_ids: Iterable[int] | None) -> list[int]:
    ids: set[int] = set()
    for value in product_ids or ():
        try:
            pid = int(value)
        except (TypeError, ValueError):
            continue
        if pid > 0:
            ids.add(pid)
    return sorted(ids)


def _placeholders(values: list[int]) -> str:
    return ",".join(["%s"] * len(values))


def _dates_ending(today: date, days: int) -> list[date]:
    start = today - timedelta(days=days - 1)
    return [start + timedelta(days=offset) for offset in range(days)]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def empty_stability_summary(*, warning: str | None = None) -> dict[str, Any]:
    warnings = [{"code": "product_stability_unavailable", "message": warning}] if warning else []
    return {
        "counts": {
            "total": 0,
            "stable_total": 0,
            "stable_7d": 0,
            "stable_30d": 0,
            "secondary_stable": 0,
            "potential": 0,
            "test": 0,
            "stopped": 0,
            "never": 0,
            "insufficient_history": 0,
        },
        "buckets": {
            STATUS_STABLE: [],
            STATUS_SECONDARY_STABLE: [],
            STATUS_POTENTIAL: [],
            STATUS_TEST: [],
            STATUS_STOPPED: [],
            STATUS_NEVER: [],
            STATUS_INSUFFICIENT_HISTORY: [],
        },
        "warnings": warnings,
        "computed_at": None,
    }


def classify_product(
    *,
    product_id: int,
    product_code: str = "",
    product_name: str = "",
    daily_orders: dict[date, int],
    ad_summary: dict[str, Any] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Classify one media product by 7d / 30d orders and ad delivery status.

    Docs-anchor: docs/superpowers/specs/2026-06-07-weekly-ai-analysis-report-design.md#产品稳定分级2026-06-07-追加
    """
    business_today = today or current_meta_business_date()
    ad_summary = ad_summary or {}
    last_7d_dates = _dates_ending(business_today, 7)
    last_30d_dates = _dates_ending(business_today, 30)
    last_7d_counts = [_safe_int(daily_orders.get(day)) for day in last_7d_dates]
    last_30d_counts = [_safe_int(daily_orders.get(day)) for day in last_30d_dates]
    last_7d_orders = sum(last_7d_counts)
    last_30d_orders = sum(last_30d_counts)
    min_7d = min(last_7d_counts) if last_7d_counts else 0
    min_30d = min(last_30d_counts) if last_30d_counts else 0
    avg_7d = _round_avg(last_7d_orders / 7)
    avg_30d = _round_avg(last_30d_orders / 30)

    delivery_status = media_product_ad_status_cache.normalize_delivery_status_filter(
        ad_summary.get("delivery_status")
    )
    if delivery_status == media_product_ad_status_cache.STATUS_ALL:
        delivery_status = media_product_ad_status_cache.STATUS_NEVER
    total_ad_spend = _safe_float(ad_summary.get("ad_spend_usd"))
    active_7d_spend = _safe_float(ad_summary.get("active_7d_ad_spend_usd"))
    delivery_start_date = _date_value(ad_summary.get("delivery_start_time"))
    delivery_age_days = (
        (business_today - delivery_start_date).days + 1
        if delivery_start_date and delivery_start_date <= business_today
        else 0
    )
    has_ad_data = (
        total_ad_spend > 0
        or active_7d_spend > 0
        or delivery_status in (
            media_product_ad_status_cache.STATUS_ACTIVE,
            media_product_ad_status_cache.STATUS_STOPPED,
        )
    )
    is_active = (
        delivery_status == media_product_ad_status_cache.STATUS_ACTIVE
        or active_7d_spend > 0
    )
    eligible_for_weekly_analysis = bool(has_ad_data and delivery_age_days >= 7)

    stable_7d = bool(
        eligible_for_weekly_analysis
        and is_active
        and (
            last_7d_orders >= 210
            or (last_7d_orders >= 140 and min_7d >= 10)
        )
    )
    stable_30d = bool(
        eligible_for_weekly_analysis
        and is_active
        and last_30d_orders >= 600
        and min_30d >= 10
    )
    secondary_stable = bool(
        eligible_for_weekly_analysis
        and is_active
        and not (stable_7d or stable_30d)
        and min_7d >= 5
        and avg_7d > 10
    )

    if not has_ad_data:
        status = STATUS_NEVER
    elif delivery_status == media_product_ad_status_cache.STATUS_STOPPED:
        status = STATUS_STOPPED
    elif not eligible_for_weekly_analysis:
        status = STATUS_INSUFFICIENT_HISTORY
    elif stable_7d or stable_30d:
        status = STATUS_STABLE
    elif secondary_stable:
        status = STATUS_SECONDARY_STABLE
    else:
        status = STATUS_TEST

    # 动态判定并升级潜力品 (potential)
    potential = False
    if has_ad_data and is_active and status not in {STATUS_STABLE, STATUS_SECONDARY_STABLE}:
        roas_val = ad_summary.get("overall_roas")
        if not eligible_for_weekly_analysis:
            # 1. 潜力新品（未满7天）：单量 >= 5 或者 ROAS >= 1.2（有单）
            if last_7d_orders >= 5 or (roas_val is not None and _safe_float(roas_val) >= 1.2 and last_7d_orders >= 1):
                potential = True
        else:
            # 2. 潜力旧品（已满7天）：单量 >= 35 或者 ROAS >= 1.2 且单量 >= 3
            if last_7d_orders >= 35 or (roas_val is not None and _safe_float(roas_val) >= 1.2 and last_7d_orders >= 3):
                potential = True

    if potential:
        status = STATUS_POTENTIAL

    stable_marks: list[str] = []
    if stable_7d:
        stable_marks.append("7天稳定")
    if stable_30d:
        stable_marks.append("30天稳定")
    if status == STATUS_SECONDARY_STABLE:
        stable_marks.append("二级稳定")
    if status == STATUS_POTENTIAL:
        stable_marks.append("潜力品")

    reasons: list[str] = []
    if stable_7d:
        reasons.append("最近 7 天达到稳定品阈值")
    if stable_30d:
        reasons.append("最近 30 天达到稳定品阈值")
    if status == STATUS_SECONDARY_STABLE:
        reasons.append("最近 7 天每日不少于 5 单且日均超过 10 单，未达稳定品阈值")
    elif status == STATUS_POTENTIAL:
        reasons.append("达到潜力品判定标准")
    elif status == STATUS_TEST:
        reasons.append("已满 7 天但未达到稳定品或二级稳定品阈值")
    elif status == STATUS_STOPPED:
        reasons.append("历史有广告消耗但当前已停投")
    elif status == STATUS_NEVER:
        reasons.append("暂无广告消耗")
    elif status == STATUS_INSUFFICIENT_HISTORY:
        reasons.append("投放未满 7 天，暂不进入周报经营评估")

    return {
        "product_id": int(product_id),
        "product_code": str(product_code or "").strip(),
        "product_name": str(product_name or "").strip(),
        "status": status,
        "display_label": STATUS_LABELS[status],
        "stable_7d": stable_7d,
        "stable_30d": stable_30d,
        "stable_marks": stable_marks,
        "last_7d_orders": last_7d_orders,
        "last_30d_orders": last_30d_orders,
        "avg_7d_orders": avg_7d,
        "avg_30d_orders": avg_30d,
        "min_daily_orders_7d": min_7d,
        "min_daily_orders_30d": min_30d,
        "active_7d_ad_spend_usd": _round_money(active_7d_spend),
        "total_ad_spend_usd": _round_money(total_ad_spend),
        "overall_roas": _nullable_float(ad_summary.get("overall_roas")),
        "delivery_status": delivery_status,
        "delivery_start_time": ad_summary.get("delivery_start_time"),
        "delivery_start_date": delivery_start_date,
        "delivery_age_days": delivery_age_days,
        "eligible_for_weekly_analysis": eligible_for_weekly_analysis,
        "delivery_end_time": ad_summary.get("delivery_end_time"),
        "active_days": _safe_int(ad_summary.get("active_days")),
        "computed_for_date": business_today,
        "computed_at": datetime.now().replace(microsecond=0),
        "details": {
            "reasons": reasons,
            "delivery_start_date": _iso(delivery_start_date),
            "delivery_age_days": delivery_age_days,
            "eligible_for_weekly_analysis": eligible_for_weekly_analysis,
            "daily_orders_7d": [
                {"date": day.isoformat(), "orders": _safe_int(daily_orders.get(day))}
                for day in last_7d_dates
            ],
        },
    }


def _load_products(product_ids: list[int] | None = None) -> list[dict[str, Any]]:
    ids = _product_ids(product_ids)
    if ids:
        return query(
            "SELECT id, product_code, name FROM media_products "
            f"WHERE deleted_at IS NULL AND id IN ({_placeholders(ids)}) "
            "ORDER BY id ASC",
            tuple(ids),
        )
    return query(
        "SELECT id, product_code, name FROM media_products "
        "WHERE deleted_at IS NULL ORDER BY id ASC"
    )


def _load_daily_order_counts(product_ids: list[int], *, today: date) -> dict[int, dict[date, int]]:
    ids = _product_ids(product_ids)
    if not ids:
        return {}
    start = today - timedelta(days=29)
    rows = query(
        "SELECT "
        "  opl.product_id, "
        "  dol.meta_business_date AS business_date, "
        "  COUNT(DISTINCT NULLIF(TRIM(dol.dxm_package_id), '')) AS order_count "
        "FROM order_profit_lines opl "
        "JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id "
        f"WHERE opl.product_id IN ({_placeholders(ids)}) "
        "  AND dol.meta_business_date BETWEEN %s AND %s "
        "GROUP BY opl.product_id, dol.meta_business_date",
        (*ids, start, today),
    )
    out: dict[int, dict[date, int]] = defaultdict(dict)
    for row in rows:
        pid = _safe_int(row.get("product_id"))
        business_date = _date_value(row.get("business_date"))
        if not pid or business_date is None:
            continue
        out[pid][business_date] = _safe_int(row.get("order_count"))
    return out


def compute_product_stability_snapshots(
    *,
    today: date | None = None,
    product_ids: Iterable[int] | None = None,
) -> list[dict[str, Any]]:
    business_today = today or current_meta_business_date()
    products = _load_products(_product_ids(product_ids) if product_ids is not None else None)
    pids = [_safe_int(row.get("id")) for row in products if _safe_int(row.get("id")) > 0]
    if not pids:
        return []
    ad_summary_map = media_product_ad_status_cache.get_product_ad_summary_cache(pids)
    orders_map = _load_daily_order_counts(pids, today=business_today)
    rows: list[dict[str, Any]] = []
    for product in products:
        pid = _safe_int(product.get("id"))
        if not pid:
            continue
        rows.append(
            classify_product(
                product_id=pid,
                product_code=product.get("product_code") or "",
                product_name=product.get("name") or "",
                daily_orders=orders_map.get(pid, {}),
                ad_summary=ad_summary_map.get(pid, {}),
                today=business_today,
            )
        )
    return rows


def _upsert_snapshot(row: dict[str, Any]) -> int:
    details = row.get("details") or {}
    return execute(
        "INSERT INTO media_product_stability_snapshots "
        "(product_id, product_code, product_name, status, display_label, stable_7d, stable_30d, "
        " last_7d_orders, last_30d_orders, avg_7d_orders, avg_30d_orders, "
        " min_daily_orders_7d, min_daily_orders_30d, active_7d_ad_spend_usd, total_ad_spend_usd, "
        " overall_roas, delivery_status, computed_for_date, computed_at, details_json) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        " product_code=VALUES(product_code), product_name=VALUES(product_name), "
        " status=VALUES(status), display_label=VALUES(display_label), "
        " stable_7d=VALUES(stable_7d), stable_30d=VALUES(stable_30d), "
        " last_7d_orders=VALUES(last_7d_orders), last_30d_orders=VALUES(last_30d_orders), "
        " avg_7d_orders=VALUES(avg_7d_orders), avg_30d_orders=VALUES(avg_30d_orders), "
        " min_daily_orders_7d=VALUES(min_daily_orders_7d), min_daily_orders_30d=VALUES(min_daily_orders_30d), "
        " active_7d_ad_spend_usd=VALUES(active_7d_ad_spend_usd), total_ad_spend_usd=VALUES(total_ad_spend_usd), "
        " overall_roas=VALUES(overall_roas), delivery_status=VALUES(delivery_status), "
        " computed_for_date=VALUES(computed_for_date), computed_at=VALUES(computed_at), "
        " details_json=VALUES(details_json)",
        (
            row["product_id"],
            row.get("product_code") or "",
            row.get("product_name") or "",
            row["status"],
            row["display_label"],
            1 if row.get("stable_7d") else 0,
            1 if row.get("stable_30d") else 0,
            _safe_int(row.get("last_7d_orders")),
            _safe_int(row.get("last_30d_orders")),
            _safe_float(row.get("avg_7d_orders")),
            _safe_float(row.get("avg_30d_orders")),
            _safe_int(row.get("min_daily_orders_7d")),
            _safe_int(row.get("min_daily_orders_30d")),
            _safe_float(row.get("active_7d_ad_spend_usd")),
            _safe_float(row.get("total_ad_spend_usd")),
            row.get("overall_roas"),
            row.get("delivery_status") or media_product_ad_status_cache.STATUS_NEVER,
            row["computed_for_date"],
            row["computed_at"],
            _json_dumps(details),
        ),
    )


def refresh_all(*, today: date | None = None) -> dict[str, Any]:
    rows = compute_product_stability_snapshots(today=today)
    writes = 0
    for row in rows:
        writes += 1 if _upsert_snapshot(row) is not None else 0
    summary = stability_summary_from_rows(rows, limit=0)
    return {
        "updated": writes,
        "computed_for_date": (today or current_meta_business_date()).isoformat(),
        "counts": summary["counts"],
    }


def _stable_marks_from_row(row: dict[str, Any]) -> list[str]:
    marks: list[str] = []
    if bool(row.get("stable_7d")):
        marks.append("7天稳定")
    if bool(row.get("stable_30d")):
        marks.append("30天稳定")
    if str(row.get("status") or "").strip().lower() == STATUS_SECONDARY_STABLE:
        marks.append("二级稳定")
    return marks


def _serialize_snapshot_row(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or STATUS_NEVER).strip().lower()
    if status not in STATUS_LABELS:
        status = STATUS_NEVER
    details = row.get("details")
    if details is None:
        raw_details = row.get("details_json")
        if isinstance(raw_details, str) and raw_details.strip():
            try:
                parsed = json.loads(raw_details)
                details = parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, ValueError):
                details = {}
        elif isinstance(raw_details, dict):
            details = raw_details
        else:
            details = {}
    return {
        "product_id": _safe_int(row.get("product_id")),
        "product_code": str(row.get("product_code") or "").strip(),
        "product_name": str(row.get("product_name") or row.get("name") or "").strip(),
        "status": status,
        "display_label": str(row.get("display_label") or STATUS_LABELS[status]),
        "stable_7d": bool(row.get("stable_7d")),
        "stable_30d": bool(row.get("stable_30d")),
        "stable_marks": row.get("stable_marks") or _stable_marks_from_row(row),
        "last_7d_orders": _safe_int(row.get("last_7d_orders")),
        "last_30d_orders": _safe_int(row.get("last_30d_orders")),
        "avg_7d_orders": _safe_float(row.get("avg_7d_orders")),
        "avg_30d_orders": _safe_float(row.get("avg_30d_orders")),
        "min_daily_orders_7d": _safe_int(row.get("min_daily_orders_7d")),
        "min_daily_orders_30d": _safe_int(row.get("min_daily_orders_30d")),
        "active_7d_ad_spend_usd": _safe_float(row.get("active_7d_ad_spend_usd")),
        "total_ad_spend_usd": _safe_float(row.get("total_ad_spend_usd")),
        "overall_roas": _nullable_float(row.get("overall_roas")),
        "delivery_status": str(row.get("delivery_status") or media_product_ad_status_cache.STATUS_NEVER),
        "delivery_start_date": details.get("delivery_start_date"),
        "delivery_age_days": _safe_int(details.get("delivery_age_days")),
        "eligible_for_weekly_analysis": bool(details.get("eligible_for_weekly_analysis")),
        "computed_for_date": _iso(row.get("computed_for_date")),
        "computed_at": _iso(row.get("computed_at")),
        "details": details or {},
    }


def get_product_stability_cache(product_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    ids = _product_ids(product_ids)
    if not ids:
        return {}
    rows = query(
        "SELECT product_id, product_code, product_name, status, display_label, stable_7d, stable_30d, "
        "last_7d_orders, last_30d_orders, avg_7d_orders, avg_30d_orders, "
        "min_daily_orders_7d, min_daily_orders_30d, active_7d_ad_spend_usd, total_ad_spend_usd, "
        "overall_roas, delivery_status, computed_for_date, computed_at, details_json "
        f"FROM media_product_stability_snapshots WHERE product_id IN ({_placeholders(ids)})",
        tuple(ids),
    )
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = _serialize_snapshot_row(row)
        if item["product_id"]:
            out[item["product_id"]] = item
    return out


def stability_summary_from_rows(rows: Iterable[dict[str, Any]], *, limit: int = 50) -> dict[str, Any]:
    items = [_serialize_snapshot_row(row) for row in rows]
    counts = {
        "total": len(items),
        "stable_total": 0,
        "stable_7d": 0,
        "stable_30d": 0,
        "secondary_stable": 0,
        "potential": 0,
        "test": 0,
        "stopped": 0,
        "never": 0,
        "insufficient_history": 0,
    }
    buckets: dict[str, list[dict[str, Any]]] = {
        STATUS_STABLE: [],
        STATUS_SECONDARY_STABLE: [],
        STATUS_POTENTIAL: [],
        STATUS_TEST: [],
        STATUS_STOPPED: [],
        STATUS_NEVER: [],
        STATUS_INSUFFICIENT_HISTORY: [],
    }
    computed_at_values: list[str] = []
    for item in items:
        status = item["status"]
        if status == STATUS_STABLE:
            counts["stable_total"] += 1
        elif status == STATUS_SECONDARY_STABLE:
            counts["secondary_stable"] += 1
        elif status in (STATUS_POTENTIAL, STATUS_TEST, STATUS_STOPPED, STATUS_NEVER):
            counts[status] += 1
        elif status == STATUS_INSUFFICIENT_HISTORY:
            counts["insufficient_history"] += 1
        if item.get("stable_7d"):
            counts["stable_7d"] += 1
        if item.get("stable_30d"):
            counts["stable_30d"] += 1
        buckets.setdefault(status, []).append(item)
        if item.get("computed_at"):
            computed_at_values.append(str(item["computed_at"]))

    for key, values in buckets.items():
        values.sort(
            key=lambda item: (
                -_safe_int(item.get("last_7d_orders")),
                -_safe_int(item.get("last_30d_orders")),
                str(item.get("product_code") or ""),
            )
        )
        if limit > 0:
            buckets[key] = values[:limit]

    return {
        "counts": counts,
        "buckets": buckets,
        "warnings": [],
        "computed_at": max(computed_at_values) if computed_at_values else None,
    }


def load_stability_summary(*, limit: int = 50) -> dict[str, Any]:
    rows = query(
        "SELECT product_id, product_code, product_name, status, display_label, stable_7d, stable_30d, "
        "last_7d_orders, last_30d_orders, avg_7d_orders, avg_30d_orders, "
        "min_daily_orders_7d, min_daily_orders_30d, active_7d_ad_spend_usd, total_ad_spend_usd, "
        "overall_roas, delivery_status, computed_for_date, computed_at, details_json "
        "FROM media_product_stability_snapshots "
        "ORDER BY CASE status "
        "  WHEN 'stable' THEN 1 WHEN 'secondary_stable' THEN 2 WHEN 'potential' THEN 3 "
        "  WHEN 'test' THEN 4 WHEN 'stopped' THEN 5 WHEN 'insufficient_history' THEN 6 ELSE 7 END, "
        "last_7d_orders DESC, last_30d_orders DESC"
    )
    return stability_summary_from_rows(rows, limit=limit)
