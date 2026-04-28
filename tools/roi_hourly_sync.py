from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore.db import execute, get_conn, query, query_one

TIMEZONE = "Asia/Shanghai"
STORE_SCOPE = "newjoy,omurio"
AD_PLATFORM_SCOPE = "meta"
META_CUTOVER_HOUR_BJ = 16


def _bj_now() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None, microsecond=0)


def _floor_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def _meta_business_date(value: datetime):
    return (value - timedelta(hours=META_CUTOVER_HOUR_BJ)).date()


def _meta_business_window_start(business_date) -> datetime:
    return datetime(business_date.year, business_date.month, business_date.day, META_CUTOVER_HOUR_BJ, 0, 0)


def _meta_node_hour(snapshot_at: datetime, business_date) -> int:
    window_start = _meta_business_window_start(business_date)
    return max(0, min(23, int((snapshot_at - window_start).total_seconds() // 3600)))


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def _start_run(window_start: datetime, window_end: datetime, lookback_hours: int) -> int:
    return int(execute(
        "INSERT INTO roi_hourly_sync_runs "
        "(status, window_start_at, window_end_at, lookback_hours) "
        "VALUES ('running', %s, %s, %s)",
        (window_start, window_end, lookback_hours),
    ))


def _finish_run(run_id: int, status: str, summary: dict[str, Any], error: str | None = None) -> None:
    execute(
        "UPDATE roi_hourly_sync_runs SET status=%s, sync_finished_at=NOW(), "
        "duration_seconds=TIMESTAMPDIFF(SECOND, sync_started_at, NOW()), "
        "order_hours_upserted=%s, meta_hours_upserted=%s, overview_hours_upserted=%s, "
        "dxm_import_batch_id=%s, summary_json=%s, error_message=%s "
        "WHERE id=%s",
        (
            status,
            int(summary.get("order_hours_upserted") or 0),
            int(summary.get("meta_hours_upserted") or 0),
            int(summary.get("overview_hours_upserted") or 0),
            summary.get("dxm_import_batch_id"),
            json.dumps(summary, ensure_ascii=False, default=_json_default),
            error,
            run_id,
        ),
    )


def _run_dxm_recent_import(window_start: datetime, window_end: datetime, *, max_scan_pages: int) -> dict[str, Any]:
    from tools import dianxiaomi_order_import as dxm_import

    dates = sorted({window_start.date(), (window_end - timedelta(seconds=1)).date()})
    report: dict[str, Any] = {"reports": []}
    for day in dates:
        item = dxm_import.run_import_from_server_browser(
            start_date_text=day.isoformat(),
            end_date_text=day.isoformat(),
            site_codes=["newjoy", "omurio"],
            states=[""],
            dxm_env="DXM-01",
            dry_run=False,
            skip_login_prompt=True,
            date_filter_mode="recent-scan",
            max_scan_pages=max_scan_pages,
        )
        report["reports"].append(item)
    batch_ids = [item.get("batch_id") for item in report["reports"] if item.get("batch_id")]
    report["batch_id"] = batch_ids[-1] if batch_ids else None
    return report


def _hour_ranges(window_start: datetime, window_end: datetime) -> list[tuple[datetime, datetime]]:
    hours = []
    current = window_start
    while current < window_end:
        hours.append((current, current + timedelta(hours=1)))
        current += timedelta(hours=1)
    return hours


def _upsert_order_hour(run_id: int, hour_start: datetime, hour_end: datetime) -> int:
    order_time_expr = "COALESCE(order_paid_at, attribution_time_at, order_created_at)"
    row = query_one(
        "SELECT COUNT(DISTINCT dxm_package_id) AS order_count, "
        "COUNT(*) AS line_count, "
        "SUM(quantity) AS units, "
        "SUM(COALESCE(amount_with_shipping, line_amount, 0)) AS order_revenue_usd, "
        "SUM(COALESCE(line_amount, 0)) AS line_revenue_usd, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping_revenue_usd, "
        "MIN(" + order_time_expr + ") AS first_order_at, "
        "MAX(" + order_time_expr + ") AS last_order_at, "
        "MAX(updated_at) AS source_updated_at "
        "FROM dianxiaomi_order_lines "
        "WHERE site_code IN ('newjoy', 'omurio') "
        "AND " + order_time_expr + " >= %s AND " + order_time_expr + " < %s",
        (hour_start, hour_end),
    ) or {}
    execute(
        "INSERT INTO roi_hourly_order_facts "
        "(hour_start_at, hour_end_at, timezone, order_source, store_scope, "
        "order_count, line_count, units, order_revenue_usd, line_revenue_usd, shipping_revenue_usd, "
        "first_order_at, last_order_at, last_run_id, source_updated_at) "
        "VALUES (%s,%s,%s,'dianxiaomi',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE "
        "hour_end_at=VALUES(hour_end_at), order_count=VALUES(order_count), "
        "line_count=VALUES(line_count), units=VALUES(units), "
        "order_revenue_usd=VALUES(order_revenue_usd), line_revenue_usd=VALUES(line_revenue_usd), "
        "shipping_revenue_usd=VALUES(shipping_revenue_usd), first_order_at=VALUES(first_order_at), "
        "last_order_at=VALUES(last_order_at), last_run_id=VALUES(last_run_id), "
        "source_updated_at=VALUES(source_updated_at), updated_at=NOW()",
        (
            hour_start,
            hour_end,
            TIMEZONE,
            STORE_SCOPE,
            int(row.get("order_count") or 0),
            int(row.get("line_count") or 0),
            int(row.get("units") or 0),
            round(float(row.get("order_revenue_usd") or 0), 2),
            round(float(row.get("line_revenue_usd") or 0), 2),
            round(float(row.get("shipping_revenue_usd") or 0), 2),
            row.get("first_order_at"),
            row.get("last_order_at"),
            run_id,
            row.get("source_updated_at"),
        ),
    )
    return 1


def _ensure_meta_pending_hour(run_id: int, hour_start: datetime, hour_end: datetime) -> int:
    execute(
        "INSERT INTO roi_hourly_meta_facts "
        "(hour_start_at, hour_end_at, timezone, ad_platform, account_scope, source_status, last_run_id) "
        "VALUES (%s,%s,%s,'meta','all','pending_source',%s) "
        "ON DUPLICATE KEY UPDATE hour_end_at=VALUES(hour_end_at), "
        "last_run_id=VALUES(last_run_id), updated_at=NOW()",
        (hour_start, hour_end, TIMEZONE, run_id),
    )
    return 1


def _upsert_overview_hour(run_id: int, hour_start: datetime, hour_end: datetime) -> int:
    order_row = query_one(
        "SELECT * FROM roi_hourly_order_facts "
        "WHERE hour_start_at=%s AND order_source='dianxiaomi' AND store_scope=%s",
        (hour_start, STORE_SCOPE),
    ) or {}
    meta_row = query_one(
        "SELECT SUM(spend_usd) AS ad_spend_usd, "
        "SUM(purchase_value_usd) AS purchase_value_usd, "
        "SUM(purchases) AS purchases, "
        "MIN(source_status) AS source_status "
        "FROM roi_hourly_meta_facts "
        "WHERE hour_start_at=%s AND ad_platform='meta'",
        (hour_start,),
    ) or {}
    revenue = round(float(order_row.get("order_revenue_usd") or 0), 2)
    spend = round(float(meta_row.get("ad_spend_usd") or 0), 4)
    ad_status = str(meta_row.get("source_status") or "pending_source")
    roas = round(revenue / spend, 6) if spend > 0 and ad_status == "ok" else None
    execute(
        "INSERT INTO roi_hourly_overview_facts "
        "(hour_start_at, hour_end_at, timezone, store_scope, ad_platform_scope, "
        "order_count, units, order_revenue_usd, shipping_revenue_usd, ad_spend_usd, "
        "true_roas, order_data_status, ad_data_status, last_run_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ok',%s,%s) "
        "ON DUPLICATE KEY UPDATE hour_end_at=VALUES(hour_end_at), "
        "order_count=VALUES(order_count), units=VALUES(units), "
        "order_revenue_usd=VALUES(order_revenue_usd), shipping_revenue_usd=VALUES(shipping_revenue_usd), "
        "ad_spend_usd=VALUES(ad_spend_usd), true_roas=VALUES(true_roas), "
        "order_data_status=VALUES(order_data_status), ad_data_status=VALUES(ad_data_status), "
        "last_run_id=VALUES(last_run_id), updated_at=NOW()",
        (
            hour_start,
            hour_end,
            TIMEZONE,
            STORE_SCOPE,
            AD_PLATFORM_SCOPE,
            int(order_row.get("order_count") or 0),
            int(order_row.get("units") or 0),
            revenue,
            round(float(order_row.get("shipping_revenue_usd") or 0), 2),
            spend,
            roas,
            ad_status,
            run_id,
        ),
    )
    return 1


def _snapshot_at_node(value: datetime) -> datetime:
    minute = (value.minute // 10) * 10
    return value.replace(minute=minute, second=0, microsecond=0)


def _insert_daily_snapshot(run_id: int, snapshot_at: datetime) -> int:
    business_date = _meta_business_date(snapshot_at)
    day_start = _meta_business_window_start(business_date)
    order_time_expr = "COALESCE(order_paid_at, attribution_time_at, order_created_at)"
    order_row = query_one(
        "SELECT COUNT(DISTINCT dxm_package_id) AS order_count, "
        "COUNT(*) AS line_count, "
        "SUM(quantity) AS units, "
        "SUM(COALESCE(amount_with_shipping, line_amount, 0)) AS order_revenue_usd, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping_revenue_usd, "
        "MAX(" + order_time_expr + ") AS last_order_at "
        "FROM dianxiaomi_order_lines "
        "WHERE site_code IN ('newjoy', 'omurio') "
        "AND " + order_time_expr + " >= %s AND " + order_time_expr + " <= %s",
        (day_start, snapshot_at),
    ) or {}
    ad_row = query_one(
        "SELECT SUM(spend_usd) AS ad_spend_usd "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date=%s",
        (business_date,),
    ) or {}
    ad_spend = round(float(ad_row.get("ad_spend_usd") or 0), 4)
    ad_status = "ok" if ad_spend > 0 else "pending_source"
    execute(
        "INSERT INTO roi_realtime_daily_snapshots "
        "(snapshot_at, business_date, timezone, store_scope, ad_platform_scope, "
        "order_count, line_count, units, order_revenue_usd, shipping_revenue_usd, "
        "ad_spend_usd, order_data_status, ad_data_status, last_order_at, source_run_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ok',%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE order_count=VALUES(order_count), line_count=VALUES(line_count), "
        "units=VALUES(units), order_revenue_usd=VALUES(order_revenue_usd), "
        "shipping_revenue_usd=VALUES(shipping_revenue_usd), ad_spend_usd=VALUES(ad_spend_usd), "
        "order_data_status=VALUES(order_data_status), ad_data_status=VALUES(ad_data_status), "
        "last_order_at=VALUES(last_order_at), source_run_id=VALUES(source_run_id)",
        (
            snapshot_at,
            business_date,
            TIMEZONE,
            STORE_SCOPE,
            AD_PLATFORM_SCOPE,
            int(order_row.get("order_count") or 0),
            int(order_row.get("line_count") or 0),
            int(order_row.get("units") or 0),
            round(float(order_row.get("order_revenue_usd") or 0), 2),
            round(float(order_row.get("shipping_revenue_usd") or 0), 2),
            ad_spend,
            ad_status,
            order_row.get("last_order_at"),
            run_id,
        ),
    )
    row = query_one(
        "SELECT id FROM roi_realtime_daily_snapshots "
        "WHERE business_date=%s AND snapshot_at=%s AND store_scope=%s AND ad_platform_scope=%s "
        "ORDER BY id DESC LIMIT 1",
        (business_date, snapshot_at, STORE_SCOPE, AD_PLATFORM_SCOPE),
    ) or {}
    snapshot_id = int(row.get("id") or 0)
    _upsert_daily_roas_node(snapshot_id, snapshot_at)
    return snapshot_id


def _upsert_daily_roas_node(snapshot_id: int, snapshot_at: datetime) -> int:
    snap = query_one(
        "SELECT * FROM roi_realtime_daily_snapshots WHERE id=%s",
        (snapshot_id,),
    )
    if not snap:
        return 0
    revenue = round(float(snap.get("order_revenue_usd") or 0), 2)
    spend = round(float(snap.get("ad_spend_usd") or 0), 4)
    ad_status = str(snap.get("ad_data_status") or "pending_source")
    roas = round(revenue / spend, 6) if spend > 0 and ad_status == "ok" else None
    execute(
        "INSERT INTO roi_daily_roas_nodes "
        "(business_date, node_hour, node_at, timezone, store_scope, ad_platform_scope, snapshot_id, "
        "order_count, units, order_revenue_usd, shipping_revenue_usd, ad_spend_usd, true_roas, "
        "order_data_status, ad_data_status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE node_at=VALUES(node_at), snapshot_id=VALUES(snapshot_id), "
        "order_count=VALUES(order_count), units=VALUES(units), order_revenue_usd=VALUES(order_revenue_usd), "
        "shipping_revenue_usd=VALUES(shipping_revenue_usd), ad_spend_usd=VALUES(ad_spend_usd), "
        "true_roas=VALUES(true_roas), order_data_status=VALUES(order_data_status), "
        "ad_data_status=VALUES(ad_data_status), updated_at=NOW()",
        (
            snap.get("business_date"),
            _meta_node_hour(snapshot_at, snap.get("business_date")),
            snapshot_at,
            TIMEZONE,
            STORE_SCOPE,
            AD_PLATFORM_SCOPE,
            snapshot_id,
            int(snap.get("order_count") or 0),
            int(snap.get("units") or 0),
            revenue,
            round(float(snap.get("shipping_revenue_usd") or 0), 2),
            spend,
            roas,
            snap.get("order_data_status") or "ok",
            ad_status,
        ),
    )
    return 1


def _snapshot_before_or_at(business_date, node_at: datetime) -> dict[str, Any] | None:
    return query_one(
        "SELECT * FROM roi_realtime_daily_snapshots "
        "WHERE business_date=%s AND snapshot_at <= %s "
        "AND store_scope=%s AND ad_platform_scope=%s "
        "ORDER BY snapshot_at DESC, id DESC LIMIT 1",
        (business_date, node_at, STORE_SCOPE, AD_PLATFORM_SCOPE),
    )


def _derive_hour_delta(run_id: int, hour_start: datetime, hour_end: datetime) -> int:
    business_date = hour_start.date()
    start_snapshot = _snapshot_before_or_at(business_date, hour_start)
    end_snapshot = _snapshot_before_or_at(business_date, hour_end)
    if not end_snapshot:
        return 0
    if not start_snapshot:
        start_snapshot = {
            "id": None,
            "order_count": 0,
            "units": 0,
            "order_revenue_usd": 0,
            "shipping_revenue_usd": 0,
            "ad_spend_usd": 0,
            "ad_data_status": end_snapshot.get("ad_data_status") or "pending_source",
        }
    order_count = max(0, int(end_snapshot.get("order_count") or 0) - int(start_snapshot.get("order_count") or 0))
    units = max(0, int(end_snapshot.get("units") or 0) - int(start_snapshot.get("units") or 0))
    revenue = max(0.0, round(float(end_snapshot.get("order_revenue_usd") or 0) - float(start_snapshot.get("order_revenue_usd") or 0), 2))
    shipping = max(0.0, round(float(end_snapshot.get("shipping_revenue_usd") or 0) - float(start_snapshot.get("shipping_revenue_usd") or 0), 2))
    spend = max(0.0, round(float(end_snapshot.get("ad_spend_usd") or 0) - float(start_snapshot.get("ad_spend_usd") or 0), 4))
    ad_status = str(end_snapshot.get("ad_data_status") or "pending_source")
    roas = round(revenue / spend, 6) if spend > 0 and ad_status == "ok" else None
    execute(
        "INSERT INTO roi_hourly_delta_facts "
        "(hour_start_at, hour_end_at, business_date, timezone, store_scope, ad_platform_scope, "
        "start_snapshot_id, end_snapshot_id, order_count, units, order_revenue_usd, "
        "shipping_revenue_usd, ad_spend_usd, true_roas, order_data_status, ad_data_status, last_run_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ok',%s,%s) "
        "ON DUPLICATE KEY UPDATE hour_end_at=VALUES(hour_end_at), start_snapshot_id=VALUES(start_snapshot_id), "
        "end_snapshot_id=VALUES(end_snapshot_id), order_count=VALUES(order_count), units=VALUES(units), "
        "order_revenue_usd=VALUES(order_revenue_usd), shipping_revenue_usd=VALUES(shipping_revenue_usd), "
        "ad_spend_usd=VALUES(ad_spend_usd), true_roas=VALUES(true_roas), "
        "order_data_status=VALUES(order_data_status), ad_data_status=VALUES(ad_data_status), "
        "last_run_id=VALUES(last_run_id), updated_at=NOW()",
        (
            hour_start,
            hour_end,
            business_date,
            TIMEZONE,
            STORE_SCOPE,
            AD_PLATFORM_SCOPE,
            start_snapshot.get("id"),
            end_snapshot.get("id"),
            order_count,
            units,
            revenue,
            shipping,
            spend,
            roas,
            ad_status,
            run_id,
        ),
    )
    return 1


def run_sync(
    *,
    now: datetime | None = None,
    lookback_hours: int = 3,
    max_scan_pages: int = 40,
    skip_dxm_fetch: bool = False,
) -> dict[str, Any]:
    now = now or _bj_now()
    snapshot_at = _snapshot_at_node(now)
    window_end = _floor_hour(now) + timedelta(hours=1)
    window_start = window_end - timedelta(hours=max(1, lookback_hours))
    run_id = _start_run(window_start, window_end, lookback_hours)
    summary: dict[str, Any] = {
        "run_id": run_id,
        "window_start_at": window_start,
        "window_end_at": window_end,
        "lookback_hours": lookback_hours,
        "order_hours_upserted": 0,
        "meta_hours_upserted": 0,
        "overview_hours_upserted": 0,
    }
    try:
        if not skip_dxm_fetch:
            dxm_report = _run_dxm_recent_import(window_start, window_end, max_scan_pages=max_scan_pages)
            summary["dxm_import_batch_id"] = dxm_report.get("batch_id")
            summary["dxm_report"] = dxm_report
        summary["snapshot_id"] = _insert_daily_snapshot(run_id, snapshot_at)
        summary["snapshot_at"] = snapshot_at
        # Current requirement: only keep the real-time day-level board fresh.
        # We retain node snapshots every 10 minutes, so hourly deltas can be
        # derived later without changing the ingestion contract.
        status = "success"
        _finish_run(run_id, status, summary)
        return {**summary, "status": status}
    except Exception as exc:
        _finish_run(run_id, "failed", summary, str(exc))
        raise


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync hourly real ROAS facts from DXM orders and Meta hourly facts.")
    parser.add_argument("--lookback-hours", type=int, default=3)
    parser.add_argument("--max-scan-pages", type=int, default=40)
    parser.add_argument("--skip-dxm-fetch", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    started = time.time()
    report = run_sync(
        lookback_hours=max(1, args.lookback_hours),
        max_scan_pages=max(1, args.max_scan_pages),
        skip_dxm_fetch=args.skip_dxm_fetch,
    )
    report["duration_seconds"] = round(time.time() - started, 2)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
