from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import order_analytics as oa
from appcore.db import execute, query, query_one

TIMEZONE = "Asia/Shanghai"
STORE_SCOPE = "newjoy,omurio"
AD_PLATFORM_SCOPE = "meta"
META_CUTOVER_HOUR_BJ = 16
META_AD_EXPORT_SCRIPT = REPO_ROOT / "scripts" / "run_meta_ads_backfill_range.py"
META_AD_EXPORT_ACCOUNT_ID = os.environ.get("META_AD_EXPORT_ACCOUNT_ID", "2110407576446225")
META_AD_EXPORT_BUSINESS_ID = os.environ.get("META_AD_EXPORT_BUSINESS_ID", "476723373113063")
META_AD_EXPORT_CDP_URL = os.environ.get("META_AD_EXPORT_CDP_URL", "http://127.0.0.1:9845")
META_REALTIME_EXPORT_ROOT = Path(os.environ.get("META_REALTIME_EXPORT_DIR", REPO_ROOT / "output" / "meta_realtime_exports"))
META_EXPORT_TIMEOUT_SECONDS = int(os.environ.get("META_AD_REALTIME_EXPORT_TIMEOUT_SECONDS", "600"))


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


def _start_meta_run(business_date, snapshot_at: datetime, accounts: list[str]) -> int:
    return int(execute(
        "INSERT INTO meta_ad_realtime_import_runs "
        "(status, business_date, snapshot_at, graph_api_version, ad_account_ids) "
        "VALUES ('running', %s, %s, %s, %s)",
        (business_date, snapshot_at, "ads_manager_csv", ",".join(accounts)),
    ))


def _finish_meta_run(run_id: int, status: str, summary: dict[str, Any], error: str | None = None) -> None:
    execute(
        "UPDATE meta_ad_realtime_import_runs SET status=%s, finished_at=NOW(), "
        "duration_seconds=TIMESTAMPDIFF(SECOND, started_at, NOW()), rows_imported=%s, "
        "spend_usd=%s, summary_json=%s, error_message=%s WHERE id=%s",
        (
            status,
            int(summary.get("rows_imported") or 0),
            round(float(summary.get("spend_usd") or 0), 4),
            json.dumps(summary, ensure_ascii=False, default=_json_default),
            error,
            run_id,
        ),
    )


def _read_meta_report_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("rb") as file_obj:
        try:
            return oa.parse_shopify_file(file_obj, path.name)
        except UnicodeDecodeError:
            pass
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        return list(csv.DictReader(io.StringIO(text)))
    text = raw.decode("utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def _pick_value(row: dict[str, Any], exact: tuple[str, ...], contains: tuple[tuple[str, ...], ...] = ()) -> Any:
    for key in exact:
        if key in row:
            return row.get(key)
    for key, value in row.items():
        normalized = str(key or "").strip().lower()
        for parts in contains:
            if all(part.lower() in normalized for part in parts):
                return value
    return None


def _safe_report_number(value: Any) -> float:
    if value is None:
        return 0.0
    cleaned = re.sub(r"[^\d.\-]", "", str(value).replace(",", "").strip())
    if cleaned in ("", "-", ".", "-."):
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _revenue_with_shipping(order_revenue: float, shipping_revenue: float) -> float:
    return round(float(order_revenue or 0) + float(shipping_revenue or 0), 2)


def _true_roas(order_revenue: float, shipping_revenue: float, spend: float, ad_status: str) -> float | None:
    if spend <= 0 or ad_status != "ok":
        return None
    return round(_revenue_with_shipping(order_revenue, shipping_revenue) / spend, 6)


def _fit_report_identifier(value: Any, *, fallback: str, prefix: str, max_length: int = 64) -> str:
    candidate = str(value or "").strip()
    if candidate and len(candidate) <= max_length:
        return candidate
    source = candidate or fallback
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def _fit_text(value: str, max_length: int) -> str:
    value = str(value or "").strip()
    if len(value) <= max_length:
        return value
    return value[:max_length]


def _run_meta_ads_manager_export(business_date, snapshot_at: datetime) -> dict[str, Any]:
    if not META_AD_EXPORT_SCRIPT.exists():
        raise FileNotFoundError(f"Meta export script not found: {META_AD_EXPORT_SCRIPT}")
    export_dir = META_REALTIME_EXPORT_ROOT / business_date.isoformat() / snapshot_at.strftime("%Y%m%d_%H%M%S")
    export_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(META_AD_EXPORT_SCRIPT),
        "--start",
        business_date.isoformat(),
        "--end",
        business_date.isoformat(),
        "--out",
        str(export_dir),
        "--long-rest-every-days",
        "99",
        "--min-day-seconds",
        "0",
        "--account-id",
        META_AD_EXPORT_ACCOUNT_ID,
        "--business-id",
        META_AD_EXPORT_BUSINESS_ID,
        "--cdp-url",
        META_AD_EXPORT_CDP_URL,
    ]
    completed = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=META_EXPORT_TIMEOUT_SECONDS,
    )
    return {
        "command": cmd,
        "returncode": completed.returncode,
        "export_dir": str(export_dir),
        "stdout_tail": completed.stdout[-3000:],
        "stderr_tail": completed.stderr[-3000:],
        "campaigns_path": str(export_dir / f"newjoyloo_campaigns_{business_date.isoformat()}.csv"),
        "ads_path": str(export_dir / f"newjoyloo_ads_{business_date.isoformat()}.csv"),
    }


def _import_meta_realtime_campaign_rows_legacy(
    *,
    run_id: int,
    business_date,
    snapshot_at: datetime,
    campaign_path: Path,
) -> dict[str, Any]:
    rows = _read_meta_report_rows(campaign_path)
    imported = 0
    spend_total = 0.0
    for row in rows:
        campaign_name = str(_pick_value(
            row,
            ("广告系列名称", "Campaign name", "Campaign Name"),
            (("广告系列", "名称"), ("campaign", "name")),
        ) or "").strip()
        if not campaign_name:
            continue
        campaign_id = str(_pick_value(
            row,
            ("广告系列编号", "Campaign ID", "Campaign id"),
            (("广告系列", "编号"), ("campaign", "id")),
        ) or campaign_name).strip()
        account_id = str(_pick_value(
            row,
            ("账户编号", "广告账户编号", "Account ID", "Ad account ID"),
            (("account", "id"), ("账户", "编号")),
        ) or META_AD_EXPORT_ACCOUNT_ID).strip().removeprefix("act_")
        account_name = str(_pick_value(
            row,
            ("账户名称", "广告账户名称", "Account name", "Ad account name"),
            (("account", "name"), ("账户", "名称")),
        ) or "").strip() or None
        spend = round(_safe_report_number(_pick_value(
            row,
            ("已花费金额 (USD)", "花费金额 (USD)", "Amount spent (USD)", "Amount spent", "Spend"),
            (("amount", "spent"), ("花费",), ("spend",)),
        )), 4)
        result_count = int(round(_safe_report_number(_pick_value(
            row,
            ("成效", "Results"),
            (("result",), ("成效",)),
        ))))
        purchase_value = round(_safe_report_number(_pick_value(
            row,
            ("购物转化价值", "购买转化价值", "Website purchases conversion value", "Purchase conversion value"),
            (("purchase", "value"), ("购物", "价值"), ("购买", "价值")),
        )), 4)
        impressions = int(round(_safe_report_number(_pick_value(
            row,
            ("展示次数", "Impressions"),
            (("impression",), ("展示",)),
        ))))
        clicks = int(round(_safe_report_number(_pick_value(
            row,
            ("链接点击量", "Clicks (all)", "Clicks", "Link clicks"),
            (("click",), ("点击",)),
        ))))
        execute(
            "INSERT INTO meta_ad_realtime_daily_campaign_metrics "
            "(import_run_id, business_date, snapshot_at, data_completeness, ad_account_id, ad_account_name, "
            "campaign_id, campaign_name, normalized_campaign_code, result_count, spend_usd, purchase_value_usd, "
            "impressions, clicks, raw_json) "
            "VALUES (%s,%s,%s,'realtime_partial',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE import_run_id=VALUES(import_run_id), data_completeness=VALUES(data_completeness), "
            "ad_account_name=VALUES(ad_account_name), campaign_name=VALUES(campaign_name), "
            "normalized_campaign_code=VALUES(normalized_campaign_code), result_count=VALUES(result_count), "
            "spend_usd=VALUES(spend_usd), purchase_value_usd=VALUES(purchase_value_usd), "
            "impressions=VALUES(impressions), clicks=VALUES(clicks), raw_json=VALUES(raw_json), updated_at=NOW()",
            (
                run_id,
                business_date,
                snapshot_at,
                account_id or META_AD_EXPORT_ACCOUNT_ID,
                account_name,
                campaign_id,
                campaign_name,
                campaign_name.lower(),
                result_count,
                spend,
                purchase_value,
                impressions,
                clicks,
                json.dumps(row, ensure_ascii=False),
            ),
        )
        imported += 1
        spend_total = round(spend_total + spend, 4)
    return {"rows_imported": imported, "spend_usd": spend_total}


def _import_meta_realtime_campaign_rows(
    *,
    run_id: int,
    business_date,
    snapshot_at: datetime,
    campaign_path: Path,
) -> dict[str, Any]:
    rows = _read_meta_report_rows(campaign_path)
    execute(
        "DELETE FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE business_date=%s AND snapshot_at=%s AND data_completeness='realtime_partial'",
        (business_date, snapshot_at),
    )
    imported = 0
    spend_total = 0.0
    for row in rows:
        campaign_name_raw = str(_pick_value(
            row,
            ("广告系列名称", "Campaign name", "Campaign Name"),
            (("广告系列", "名称"), ("campaign", "name")),
        ) or "").strip()
        if not campaign_name_raw:
            continue
        campaign_name = _fit_text(campaign_name_raw, 255)
        campaign_id_value = _pick_value(
            row,
            ("广告系列编号", "Campaign ID", "Campaign id"),
            (("广告系列", "编号"), ("campaign", "id")),
        )
        campaign_id = _fit_report_identifier(campaign_id_value, fallback=campaign_name_raw, prefix="campaign")
        account_id = str(_pick_value(
            row,
            ("账户编号", "广告账户编号", "Account ID", "Ad account ID"),
            (("account", "id"), ("账户", "编号")),
        ) or META_AD_EXPORT_ACCOUNT_ID).strip().removeprefix("act_")
        account_name = str(_pick_value(
            row,
            ("账户名称", "广告账户名称", "Account name", "Ad account name"),
            (("account", "name"), ("账户", "名称")),
        ) or "").strip() or None
        spend = round(_safe_report_number(_pick_value(
            row,
            ("已花费金额 (USD)", "花费金额 (USD)", "Amount spent (USD)", "Amount spent", "Spend"),
            (("amount", "spent"), ("花费",), ("spend",)),
        )), 4)
        result_count = int(round(_safe_report_number(_pick_value(
            row,
            ("成效", "Results"),
            (("result",), ("成效",)),
        ))))
        purchase_value = round(_safe_report_number(_pick_value(
            row,
            ("购物转化价值", "购买转化价值", "Website purchases conversion value", "Purchase conversion value"),
            (("purchase", "value"), ("购物", "价值"), ("购买", "价值")),
        )), 4)
        impressions = int(round(_safe_report_number(_pick_value(
            row,
            ("展示次数", "Impressions"),
            (("impression",), ("展示",)),
        ))))
        clicks = int(round(_safe_report_number(_pick_value(
            row,
            ("链接点击量", "Clicks (all)", "Clicks", "Link clicks"),
            (("click",), ("点击",)),
        ))))
        execute(
            "INSERT INTO meta_ad_realtime_daily_campaign_metrics "
            "(import_run_id, business_date, snapshot_at, data_completeness, ad_account_id, ad_account_name, "
            "campaign_id, campaign_name, normalized_campaign_code, result_count, spend_usd, purchase_value_usd, "
            "impressions, clicks, raw_json) "
            "VALUES (%s,%s,%s,'realtime_partial',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE import_run_id=VALUES(import_run_id), data_completeness=VALUES(data_completeness), "
            "ad_account_name=VALUES(ad_account_name), campaign_name=VALUES(campaign_name), "
            "normalized_campaign_code=VALUES(normalized_campaign_code), result_count=VALUES(result_count), "
            "spend_usd=VALUES(spend_usd), purchase_value_usd=VALUES(purchase_value_usd), "
            "impressions=VALUES(impressions), clicks=VALUES(clicks), raw_json=VALUES(raw_json), updated_at=NOW()",
            (
                run_id,
                business_date,
                snapshot_at,
                account_id or META_AD_EXPORT_ACCOUNT_ID,
                account_name,
                campaign_id,
                campaign_name,
                _fit_text(campaign_name_raw.lower(), 255),
                result_count,
                spend,
                purchase_value,
                impressions,
                clicks,
                json.dumps(row, ensure_ascii=False),
            ),
        )
        imported += 1
        spend_total = round(spend_total + spend, 4)
    return {"rows_imported": imported, "spend_usd": spend_total}


def _sync_meta_realtime_daily(business_date, snapshot_at: datetime) -> dict[str, Any]:
    accounts = [META_AD_EXPORT_ACCOUNT_ID]
    summary = {
        "business_date": business_date,
        "snapshot_at": snapshot_at,
        "rows_imported": 0,
        "spend_usd": 0.0,
        "accounts": accounts,
        "source": "ads_manager_daily_export_script",
        "data_completeness": "realtime_partial",
    }
    run_id = _start_meta_run(business_date, snapshot_at, accounts)
    summary["run_id"] = run_id
    try:
        export_report = _run_meta_ads_manager_export(business_date, snapshot_at)
        summary["export_report"] = export_report
        if int(export_report.get("returncode") or 0) != 0:
            if "FAILED_AUTH" in str(export_report.get("stdout_tail") or ""):
                error = "Meta Ads Manager export failed: server browser is not logged in"
            else:
                error = f"Meta Ads Manager export failed with code {export_report.get('returncode')}"
            _finish_meta_run(run_id, "failed", summary, error)
            summary["status"] = "failed"
            summary["error"] = error
            return summary
        campaign_path = Path(str(export_report["campaigns_path"]))
        if not campaign_path.exists() or campaign_path.stat().st_size <= 100:
            error = f"Meta campaign export missing or empty: {campaign_path}"
            _finish_meta_run(run_id, "failed", summary, error)
            summary["status"] = "failed"
            summary["error"] = error
            return summary
        import_report = _import_meta_realtime_campaign_rows(
            run_id=run_id,
            business_date=business_date,
            snapshot_at=snapshot_at,
            campaign_path=campaign_path,
        )
        summary.update(import_report)
        _finish_meta_run(run_id, "success", summary)
        summary["status"] = "success"
        return summary
    except Exception as exc:
        summary["status"] = "failed"
        summary["error"] = str(exc)
        _finish_meta_run(run_id, "failed", summary, str(exc))
        return summary


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
        "SUM(COALESCE(line_amount, 0)) AS order_revenue_usd, "
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
    shipping = round(float(order_row.get("shipping_revenue_usd") or 0), 2)
    spend = round(float(meta_row.get("ad_spend_usd") or 0), 4)
    ad_status = str(meta_row.get("source_status") or "pending_source")
    roas = _true_roas(revenue, shipping, spend, ad_status)
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
            shipping,
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
        "SUM(COALESCE(line_amount, 0)) AS order_revenue_usd, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping_revenue_usd, "
        "MAX(" + order_time_expr + ") AS last_order_at "
        "FROM dianxiaomi_order_lines "
        "WHERE site_code IN ('newjoy', 'omurio') "
        "AND " + order_time_expr + " >= %s AND " + order_time_expr + " <= %s",
        (day_start, snapshot_at),
    ) or {}
    ad_row = query_one(
        "SELECT SUM(spend_usd) AS ad_spend_usd "
        "FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE business_date=%s AND snapshot_at=%s AND data_completeness='realtime_partial'",
        (business_date, snapshot_at),
    ) or {}
    ad_run = query_one(
        "SELECT status FROM meta_ad_realtime_import_runs "
        "WHERE business_date=%s AND snapshot_at=%s "
        "ORDER BY id DESC LIMIT 1",
        (business_date, snapshot_at),
    ) or {}
    ad_spend = round(float(ad_row.get("ad_spend_usd") or 0), 4)
    ad_status = "ok" if ad_run.get("status") == "success" else "pending_source"
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
    shipping = round(float(snap.get("shipping_revenue_usd") or 0), 2)
    spend = round(float(snap.get("ad_spend_usd") or 0), 4)
    ad_status = str(snap.get("ad_data_status") or "pending_source")
    roas = _true_roas(revenue, shipping, spend, ad_status)
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
            shipping,
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
    roas = _true_roas(revenue, shipping, spend, ad_status)
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
    skip_meta_fetch: bool = False,
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
        business_date = _meta_business_date(snapshot_at)
        business_window_start = _meta_business_window_start(business_date)
        summary["meta_business_date"] = business_date
        summary["meta_business_window_start_at"] = business_window_start
        if not skip_dxm_fetch:
            dxm_report = _run_dxm_recent_import(business_window_start, snapshot_at, max_scan_pages=max_scan_pages)
            summary["dxm_import_batch_id"] = dxm_report.get("batch_id")
            summary["dxm_report"] = dxm_report
        if skip_meta_fetch:
            summary["meta_realtime_report"] = {
                "business_date": business_date,
                "snapshot_at": snapshot_at,
                "status": "skipped",
                "source": "local_browser_required",
                "message": "Meta Ads Manager export depends on the operator local browser environment.",
            }
        else:
            summary["meta_realtime_report"] = _sync_meta_realtime_daily(business_date, snapshot_at)
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
    parser.add_argument("--skip-meta-fetch", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    started = time.time()
    report = run_sync(
        lookback_hours=max(1, args.lookback_hours),
        max_scan_pages=max(1, args.max_scan_pages),
        skip_dxm_fetch=args.skip_dxm_fetch,
        skip_meta_fetch=args.skip_meta_fetch,
    )
    report["duration_seconds"] = round(time.time() - started, 2)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
