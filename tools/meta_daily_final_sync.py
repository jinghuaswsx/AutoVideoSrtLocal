from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import order_analytics as oa
from appcore import scheduled_tasks
from appcore.db import execute, query_one
from tools import roi_hourly_sync as realtime_sync

TIMEZONE = "Asia/Shanghai"
META_CUTOVER_HOUR_BJ = 16
TASK_CODE = "meta_daily_final"
STORE_SCOPE = "newjoy,omurio"
AD_PLATFORM_SCOPE = "meta"
META_AD_EXPORT_ACCOUNT_ID = os.environ.get("META_AD_EXPORT_ACCOUNT_ID", realtime_sync.META_AD_EXPORT_ACCOUNT_ID)
META_AD_EXPORT_BUSINESS_ID = os.environ.get("META_AD_EXPORT_BUSINESS_ID", realtime_sync.META_AD_EXPORT_BUSINESS_ID)
META_AD_EXPORT_CDP_URL = os.environ.get("META_AD_EXPORT_CDP_URL", realtime_sync.META_AD_EXPORT_CDP_URL)
META_DAILY_FINAL_EXPORT_ROOT = Path(
    os.environ.get("META_DAILY_FINAL_EXPORT_DIR", REPO_ROOT / "output" / "meta_daily_final_exports")
)
META_DAILY_FINAL_EXPORT_TIMEOUT_SECONDS = int(os.environ.get("META_DAILY_FINAL_EXPORT_TIMEOUT_SECONDS", "900"))
META_AD_EXPORT_SCRIPT = REPO_ROOT / "scripts" / "run_meta_ads_backfill_range.py"


def _bj_now() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None, microsecond=0)


def completed_meta_business_date(now: datetime | None = None) -> date:
    value = (now or _bj_now()).replace(microsecond=0)
    closed_today = value.replace(hour=META_CUTOVER_HOUR_BJ, minute=0, second=0, microsecond=0)
    if value >= closed_today:
        return value.date() - timedelta(days=1)
    return value.date() - timedelta(days=2)


def _meta_business_window_start(target: date) -> datetime:
    return datetime(target.year, target.month, target.day, META_CUTOVER_HOUR_BJ, 0, 0)


def _meta_business_window(target: date) -> tuple[datetime, datetime]:
    start = _meta_business_window_start(target)
    return start, start + timedelta(days=1)


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_optional_report_date(value: Any, fallback: date) -> date:
    text = str(value or "").strip()
    if not text:
        return fallback
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return fallback


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def already_successful(target_date: date, *, account_id: str = META_AD_EXPORT_ACCOUNT_ID) -> bool:
    row = query_one(
        "SELECT id, status FROM scheduled_task_runs "
        "WHERE task_code=%s AND status='success' "
        "AND JSON_UNQUOTE(JSON_EXTRACT(summary_json, '$.target_date'))=%s "
        "ORDER BY started_at DESC, id DESC LIMIT 1",
        (TASK_CODE, target_date.isoformat()),
    )
    return bool(row)


def _run_meta_ads_export(target_date: date, export_dir: Path) -> dict[str, Any]:
    export_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(META_AD_EXPORT_SCRIPT),
        "--start",
        target_date.isoformat(),
        "--end",
        target_date.isoformat(),
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
        timeout=META_DAILY_FINAL_EXPORT_TIMEOUT_SECONDS,
    )
    return {
        "command": cmd,
        "returncode": completed.returncode,
        "export_dir": str(export_dir),
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "campaigns_path": str(export_dir / f"newjoyloo_campaigns_{target_date.isoformat()}.csv"),
        "ads_path": str(export_dir / f"newjoyloo_ads_{target_date.isoformat()}.csv"),
    }


def _pick(row: dict[str, Any], exact: tuple[str, ...], contains: tuple[tuple[str, ...], ...] = ()) -> Any:
    return realtime_sync._pick_value(row, exact, contains)


def _num(value: Any) -> float:
    return realtime_sync._safe_report_number(value)


def _text(value: Any, length: int) -> str:
    return realtime_sync._fit_text(str(value or "").strip(), length)


def _account_id(row: dict[str, Any]) -> str:
    return str(_pick(
        row,
        ("账户编号", "广告账户编号", "Account ID", "Ad account ID"),
        (("account", "id"), ("账户", "编号")),
    ) or META_AD_EXPORT_ACCOUNT_ID).strip().removeprefix("act_")


def _account_name(row: dict[str, Any]) -> str | None:
    return str(_pick(
        row,
        ("账户名称", "广告账户名称", "Account name", "Ad account name"),
        (("account", "name"), ("账户", "名称")),
    ) or "").strip() or None


def _extract_product_code_from_ad_name(ad_name: str) -> str | None:
    text = ad_name.strip().lower()
    if not text:
        return None
    head = text.split("(", 1)[0].strip()
    return head[:255] or None


def _common_metrics(row: dict[str, Any]) -> dict[str, Any]:
    spend = round(_num(_pick(
        row,
        ("已花费金额 (USD)", "花费金额 (USD)", "Amount spent (USD)", "Amount spent", "Spend"),
        (("amount", "spent"), ("花费",), ("spend",)),
    )), 4)
    purchase_value = round(_num(_pick(
        row,
        ("购物转化价值", "购买转化价值", "Website purchases conversion value", "Purchase conversion value"),
        (("purchase", "value"), ("购物", "价值"), ("购买", "价值")),
    )), 4)
    roas = _num(_pick(
        row,
        ("广告花费回报 (ROAS) - 购物", "Purchase ROAS (return on ad spend)", "ROAS"),
        (("roas",), ("回报",)),
    ))
    return {
        "result_count": int(round(_num(_pick(row, ("成效", "Results"), (("result",), ("成效",)))))),
        "result_metric": _text(_pick(row, ("成效指标", "Result indicator", "Result type"), (("result", "indicator"), ("成效", "指标"))) or "", 128) or None,
        "spend_usd": spend,
        "purchase_value_usd": purchase_value,
        "roas_purchase": round(roas, 6) if roas else None,
    }


def _normalize_campaign_rows(path: Path, target_date: date) -> list[dict[str, Any]]:
    rows = []
    for row in realtime_sync._read_meta_report_rows(path):
        campaign_name_raw = str(_pick(
            row,
            ("广告系列名称", "Campaign name", "Campaign Name"),
            (("广告系列", "名称"), ("campaign", "name")),
        ) or "").strip()
        if not campaign_name_raw:
            continue
        report_start = _parse_optional_report_date(_pick(row, ("报告开始日期", "Reporting starts", "Report start date")), target_date)
        report_end = _parse_optional_report_date(_pick(row, ("报告结束日期", "Reporting ends", "Report end date")), target_date)
        campaign_name = _text(campaign_name_raw, 255)
        item = {
            "ad_account_id": _account_id(row),
            "ad_account_name": _account_name(row),
            "report_date": target_date,
            "report_start_date": report_start,
            "report_end_date": report_end,
            "campaign_name": campaign_name,
            "normalized_campaign_code": campaign_name.lower(),
            "product_code": _text(campaign_name_raw.lower(), 255) or None,
            "raw": dict(row),
        }
        item.update(_common_metrics(row))
        rows.append(item)
    return rows


def _normalize_ad_rows(path: Path, target_date: date) -> list[dict[str, Any]]:
    rows = []
    for row in realtime_sync._read_meta_report_rows(path):
        ad_name_raw = str(_pick(
            row,
            ("广告名称", "Ad name", "Ad Name"),
            (("ad", "name"), ("广告", "名称")),
        ) or "").strip()
        if not ad_name_raw:
            continue
        report_start = _parse_optional_report_date(_pick(row, ("报告开始日期", "Reporting starts", "Report start date")), target_date)
        report_end = _parse_optional_report_date(_pick(row, ("报告结束日期", "Reporting ends", "Report end date")), target_date)
        ad_name = _text(ad_name_raw, 512)
        item = {
            "ad_account_id": _account_id(row),
            "ad_account_name": _account_name(row),
            "report_date": target_date,
            "report_start_date": report_start,
            "report_end_date": report_end,
            "ad_name": ad_name,
            "normalized_ad_code": ad_name.lower(),
            "product_code": _extract_product_code_from_ad_name(ad_name_raw),
            "raw": dict(row),
        }
        item.update(_common_metrics(row))
        rows.append(item)
    return rows


def aggregate_daily_entity_rows(rows: list[dict[str, Any]], *, entity_key: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("ad_account_id"), row.get("report_date"), row.get(entity_key))
        current = grouped.get(key)
        if current is None:
            current = dict(row)
            current["raw"] = {"merged_rows": 1, "rows": [row.get("raw") or {}]}
            grouped[key] = current
            continue
        current["result_count"] = int(current.get("result_count") or 0) + int(row.get("result_count") or 0)
        current["spend_usd"] = round(float(current.get("spend_usd") or 0) + float(row.get("spend_usd") or 0), 4)
        current["purchase_value_usd"] = round(
            float(current.get("purchase_value_usd") or 0) + float(row.get("purchase_value_usd") or 0),
            4,
        )
        raw = current.setdefault("raw", {"merged_rows": 0, "rows": []})
        raw["merged_rows"] = int(raw.get("merged_rows") or 0) + 1
        raw.setdefault("rows", []).append(row.get("raw") or {})

    for row in grouped.values():
        spend = float(row.get("spend_usd") or 0)
        purchase_value = float(row.get("purchase_value_usd") or 0)
        row["roas_purchase"] = round(purchase_value / spend, 6) if spend else None
    return list(grouped.values())


def _insert_batch(path: Path, *, target_date: date, raw_row_count: int, level: str) -> int:
    return int(execute(
        "INSERT INTO meta_ad_import_batches "
        "(source_filename, file_sha256, import_frequency, report_start_date, report_end_date, raw_row_count) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (path.name, _hash_file(path), f"daily-{level}", target_date, target_date, raw_row_count),
    ))


def _finish_batch(batch_id: int, *, imported: int, matched: int) -> None:
    execute(
        "UPDATE meta_ad_import_batches SET imported_rows=%s, matched_rows=%s WHERE id=%s",
        (imported, matched, int(batch_id)),
    )


def _match_product(product_code: str | None) -> dict[str, Any] | None:
    if not product_code:
        return None
    try:
        return oa.resolve_ad_product_match(product_code)
    except Exception:
        return None


def _replace_campaign_daily_rows(path: Path, target_date: date) -> dict[str, Any]:
    rows = aggregate_daily_entity_rows(_normalize_campaign_rows(path, target_date), entity_key="campaign_name")
    batch_id = _insert_batch(path, target_date=target_date, raw_row_count=len(rows), level="campaign")
    window_start, window_end = _meta_business_window(target_date)
    execute(
        "DELETE FROM meta_ad_daily_campaign_metrics WHERE meta_business_date=%s AND ad_account_id=%s",
        (target_date, META_AD_EXPORT_ACCOUNT_ID),
    )
    imported = 0
    matched = 0
    spend_total = 0.0
    for row in rows:
        product = _match_product(row.get("product_code"))
        product_id = product.get("id") if product else None
        matched_product_code = product.get("product_code") if product else None
        if product_id:
            matched += 1
        execute(
            "INSERT INTO meta_ad_daily_campaign_metrics "
            "(import_batch_id, ad_account_id, ad_account_name, report_date, report_start_date, report_end_date, "
            "campaign_name, normalized_campaign_code, product_code, matched_product_code, product_id, "
            "result_count, result_metric, spend_usd, purchase_value_usd, roas_purchase, raw_json, "
            "meta_business_date, meta_window_start_at, meta_window_end_at, attribution_timezone) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                batch_id,
                row.get("ad_account_id") or META_AD_EXPORT_ACCOUNT_ID,
                row.get("ad_account_name"),
                row["report_date"],
                row["report_start_date"],
                row["report_end_date"],
                row["campaign_name"],
                row["normalized_campaign_code"],
                row.get("product_code"),
                matched_product_code,
                product_id,
                row.get("result_count") or 0,
                row.get("result_metric"),
                row.get("spend_usd") or 0,
                row.get("purchase_value_usd") or 0,
                row.get("roas_purchase"),
                json.dumps(row.get("raw") or {}, ensure_ascii=False),
                target_date,
                window_start,
                window_end,
                TIMEZONE,
            ),
        )
        imported += 1
        spend_total = round(spend_total + float(row.get("spend_usd") or 0), 4)
    _finish_batch(batch_id, imported=imported, matched=matched)
    return {"batch_id": batch_id, "rows": imported, "matched": matched, "spend_usd": spend_total}


def _replace_ad_daily_rows(path: Path, target_date: date) -> dict[str, Any]:
    rows = aggregate_daily_entity_rows(_normalize_ad_rows(path, target_date), entity_key="ad_name")
    batch_id = _insert_batch(path, target_date=target_date, raw_row_count=len(rows), level="ad")
    window_start, window_end = _meta_business_window(target_date)
    execute(
        "DELETE FROM meta_ad_daily_ad_metrics WHERE meta_business_date=%s AND ad_account_id=%s",
        (target_date, META_AD_EXPORT_ACCOUNT_ID),
    )
    imported = 0
    matched = 0
    spend_total = 0.0
    for row in rows:
        product = _match_product(row.get("product_code"))
        product_id = product.get("id") if product else None
        matched_product_code = product.get("product_code") if product else None
        if product_id:
            matched += 1
        execute(
            "INSERT INTO meta_ad_daily_ad_metrics "
            "(import_batch_id, ad_account_id, ad_account_name, report_date, report_start_date, report_end_date, "
            "ad_name, normalized_ad_code, product_code, matched_product_code, product_id, "
            "result_count, result_metric, spend_usd, purchase_value_usd, roas_purchase, raw_json, "
            "meta_business_date, meta_window_start_at, meta_window_end_at, attribution_timezone) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                batch_id,
                row.get("ad_account_id") or META_AD_EXPORT_ACCOUNT_ID,
                row.get("ad_account_name"),
                row["report_date"],
                row["report_start_date"],
                row["report_end_date"],
                row["ad_name"],
                row["normalized_ad_code"],
                row.get("product_code"),
                matched_product_code,
                product_id,
                row.get("result_count") or 0,
                row.get("result_metric"),
                row.get("spend_usd") or 0,
                row.get("purchase_value_usd") or 0,
                row.get("roas_purchase"),
                json.dumps(row.get("raw") or {}, ensure_ascii=False),
                target_date,
                window_start,
                window_end,
                TIMEZONE,
            ),
        )
        imported += 1
        spend_total = round(spend_total + float(row.get("spend_usd") or 0), 4)
    _finish_batch(batch_id, imported=imported, matched=matched)
    return {"batch_id": batch_id, "rows": imported, "matched": matched, "spend_usd": spend_total}


def _refresh_final_roas_snapshot(target_date: date, source_run_id: int) -> int:
    day_start, day_end = _meta_business_window(target_date)
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
        "AND " + order_time_expr + " >= %s AND " + order_time_expr + " < %s",
        (day_start, day_end),
    ) or {}
    ad_row = query_one(
        "SELECT SUM(spend_usd) AS ad_spend_usd "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date=%s AND ad_account_id=%s",
        (target_date, META_AD_EXPORT_ACCOUNT_ID),
    ) or {}
    ad_spend = round(float(ad_row.get("ad_spend_usd") or 0), 4)
    execute(
        "INSERT INTO roi_realtime_daily_snapshots "
        "(snapshot_at, business_date, timezone, store_scope, ad_platform_scope, "
        "order_count, line_count, units, order_revenue_usd, shipping_revenue_usd, "
        "ad_spend_usd, order_data_status, ad_data_status, last_order_at, source_run_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ok','ok',%s,%s) "
        "ON DUPLICATE KEY UPDATE order_count=VALUES(order_count), line_count=VALUES(line_count), "
        "units=VALUES(units), order_revenue_usd=VALUES(order_revenue_usd), "
        "shipping_revenue_usd=VALUES(shipping_revenue_usd), ad_spend_usd=VALUES(ad_spend_usd), "
        "order_data_status=VALUES(order_data_status), ad_data_status=VALUES(ad_data_status), "
        "last_order_at=VALUES(last_order_at), source_run_id=VALUES(source_run_id)",
        (
            day_end,
            target_date,
            TIMEZONE,
            STORE_SCOPE,
            AD_PLATFORM_SCOPE,
            int(order_row.get("order_count") or 0),
            int(order_row.get("line_count") or 0),
            int(order_row.get("units") or 0),
            round(float(order_row.get("order_revenue_usd") or 0), 2),
            round(float(order_row.get("shipping_revenue_usd") or 0), 2),
            ad_spend,
            order_row.get("last_order_at"),
            source_run_id,
        ),
    )
    row = query_one(
        "SELECT id FROM roi_realtime_daily_snapshots "
        "WHERE business_date=%s AND snapshot_at=%s AND store_scope=%s AND ad_platform_scope=%s "
        "ORDER BY id DESC LIMIT 1",
        (target_date, day_end, STORE_SCOPE, AD_PLATFORM_SCOPE),
    ) or {}
    snapshot_id = int(row.get("id") or 0)
    if snapshot_id:
        realtime_sync._upsert_daily_roas_node(snapshot_id, day_end)
    return snapshot_id


def run_final_sync(target_date: date, *, mode: str = "run") -> dict[str, Any]:
    if mode == "check" and already_successful(target_date, account_id=META_AD_EXPORT_ACCOUNT_ID):
        return {
            "status": "skipped",
            "reason": "already_successful",
            "target_date": target_date.isoformat(),
        }

    run_id = scheduled_tasks.start_run(TASK_CODE)
    started = time.time()
    export_dir = META_DAILY_FINAL_EXPORT_ROOT / target_date.isoformat() / _bj_now().strftime("%Y%m%d_%H%M%S")
    summary: dict[str, Any] = {
        "target_date": target_date.isoformat(),
        "window_start_at": _meta_business_window(target_date)[0],
        "window_end_at": _meta_business_window(target_date)[1],
        "account_id": META_AD_EXPORT_ACCOUNT_ID,
        "mode": mode,
        "export_dir": str(export_dir),
    }
    try:
        export_report = _run_meta_ads_export(target_date, export_dir)
        summary["export_report"] = export_report
        if int(export_report.get("returncode") or 0) != 0:
            error = f"Meta Ads Manager final daily export failed with code {export_report.get('returncode')}"
            if "FAILED_AUTH" in str(export_report.get("stdout_tail") or ""):
                error = "Meta Ads Manager final daily export failed: server browser is not logged in"
            raise RuntimeError(error)

        campaign_path = Path(str(export_report["campaigns_path"]))
        ad_path = Path(str(export_report["ads_path"]))
        if not campaign_path.exists() or campaign_path.stat().st_size <= 100:
            raise RuntimeError(f"Meta campaign final export missing or empty: {campaign_path}")
        if not ad_path.exists() or ad_path.stat().st_size <= 100:
            raise RuntimeError(f"Meta ad final export missing or empty: {ad_path}")

        campaign_report = _replace_campaign_daily_rows(campaign_path, target_date)
        ad_report = _replace_ad_daily_rows(ad_path, target_date)
        snapshot_id = _refresh_final_roas_snapshot(target_date, run_id)
        summary.update({
            "campaign_report": campaign_report,
            "ad_report": ad_report,
            "snapshot_id": snapshot_id,
            "duration_seconds": round(time.time() - started, 2),
        })
        scheduled_tasks.finish_run(run_id, status="success", summary=summary, output_file=str(export_dir))
        summary["status"] = "success"
        summary["run_id"] = run_id
        return summary
    except Exception as exc:
        summary["duration_seconds"] = round(time.time() - started, 2)
        summary["error"] = str(exc)
        scheduled_tasks.finish_run(
            run_id,
            status="failed",
            summary=summary,
            error_message=str(exc),
            output_file=str(export_dir),
        )
        raise


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync the just-closed Meta Ads Manager day into final daily ROAS tables.")
    parser.add_argument("--date", help="Meta business date to fetch, YYYY-MM-DD. Defaults to the just-closed day.")
    parser.add_argument("--mode", choices=("run", "check"), default="run")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    target = _parse_date(args.date) if args.date else completed_meta_business_date()
    result = run_final_sync(target, mode=args.mode)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
