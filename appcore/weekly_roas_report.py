"""ROAS 周报：把"真实 ROAS"列表的 7 天对比固化成周快照。

- 用户每周二 09:00 北京时间得到上一个完整 ISO 周（周一到周日）的对比报告。
- 数据复用 ``get_true_roas_summary``，按 ``meta_business_date`` 聚合。
- 快照存到 ``weekly_roas_report_snapshots``；前端访问时优先读快照（保证报告
  内容跟周二定格一致），缺快照时回退实时计算。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from appcore import scheduled_tasks
from appcore.db import execute, query, query_one
from appcore.order_analytics import current_meta_business_date, get_true_roas_summary

log = logging.getLogger(__name__)

TASK_CODE = "weekly_roas_report"


def _week_start_of(value: date) -> date:
    """ISO 周的周一。"""
    return value - timedelta(days=value.weekday())


def previous_complete_week(now: datetime | None = None) -> tuple[date, date]:
    """返回上一个完整 ISO 周 (week_start=Mon, week_end=Sun)。"""
    today = current_meta_business_date(now)
    this_week_monday = _week_start_of(today)
    last_week_monday = this_week_monday - timedelta(days=7)
    last_week_sunday = last_week_monday + timedelta(days=6)
    return last_week_monday, last_week_sunday


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _serialize(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: _serialize(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_serialize(item) for item in payload]
    return _serialize_value(payload)


def compute_weekly_report(week_start: date, week_end: date) -> dict[str, Any]:
    """实时算一周的对比报告。复用真实 ROAS summary，外层加店铺-Meta 销售额差。"""
    raw = get_true_roas_summary(week_start.isoformat(), week_end.isoformat())
    summary = dict(raw["summary"])
    summary["sales_gap"] = round(
        float(summary.get("revenue_with_shipping") or 0)
        - float(summary.get("meta_purchase_value") or 0),
        2,
    )
    rows = []
    for row in raw["rows"]:
        item = dict(row)
        item["sales_gap"] = round(
            float(item.get("revenue_with_shipping") or 0)
            - float(item.get("meta_purchase_value") or 0),
            2,
        )
        rows.append(item)
    return {
        "period": {
            "week_start": week_start,
            "week_end": week_end,
            "timezone": "Asia/Shanghai",
        },
        "summary": summary,
        "rows": rows,
    }


def upsert_snapshot(week_start: date, week_end: date, *, generated_by: str = "scheduler") -> dict[str, Any]:
    report = compute_weekly_report(week_start, week_end)
    summary_json = json.dumps(_serialize(report["summary"]), ensure_ascii=False)
    rows_json = json.dumps(_serialize(report["rows"]), ensure_ascii=False)
    execute(
        "INSERT INTO weekly_roas_report_snapshots "
        "(week_start_date, week_end_date, generated_at, generated_by, summary_json, rows_json) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  week_end_date=VALUES(week_end_date), "
        "  generated_at=VALUES(generated_at), "
        "  generated_by=VALUES(generated_by), "
        "  summary_json=VALUES(summary_json), "
        "  rows_json=VALUES(rows_json)",
        (week_start, week_end, datetime.now().replace(microsecond=0), generated_by, summary_json, rows_json),
    )
    return report


def get_snapshot(week_start: date) -> dict[str, Any] | None:
    row = query_one(
        "SELECT week_start_date, week_end_date, generated_at, generated_by, summary_json, rows_json "
        "FROM weekly_roas_report_snapshots WHERE week_start_date=%s",
        (week_start,),
    )
    if not row:
        return None
    summary = json.loads(row["summary_json"]) if isinstance(row["summary_json"], str) else row["summary_json"]
    rows = json.loads(row["rows_json"]) if isinstance(row["rows_json"], str) else row["rows_json"]
    return {
        "period": {
            "week_start": row["week_start_date"],
            "week_end": row["week_end_date"],
            "timezone": "Asia/Shanghai",
        },
        "summary": summary,
        "rows": rows,
        "snapshot": {
            "generated_at": row["generated_at"],
            "generated_by": row["generated_by"],
        },
    }


def list_recent_snapshot_weeks(limit: int = 12) -> list[dict[str, Any]]:
    rows = query(
        "SELECT week_start_date, week_end_date, generated_at, generated_by "
        "FROM weekly_roas_report_snapshots ORDER BY week_start_date DESC LIMIT %s",
        (int(limit),),
    )
    return [
        {
            "week_start": row["week_start_date"],
            "week_end": row["week_end_date"],
            "generated_at": row["generated_at"],
            "generated_by": row["generated_by"],
        }
        for row in rows
    ]


def get_or_compute_report(week_start: date, week_end: date) -> dict[str, Any]:
    snap = get_snapshot(week_start)
    if snap:
        return snap
    report = compute_weekly_report(week_start, week_end)
    report["snapshot"] = None
    return report


def run_scheduled_snapshot(*, scheduled_for: datetime | None = None, now: datetime | None = None) -> dict[str, Any]:
    week_start, week_end = previous_complete_week(now)
    log.info("weekly_roas_report snapshot start: %s ~ %s", week_start, week_end)
    run_id = scheduled_tasks.start_run(TASK_CODE, scheduled_for=scheduled_for)
    try:
        report = upsert_snapshot(week_start, week_end, generated_by="scheduler")
    except Exception as exc:
        scheduled_tasks.finish_run(run_id, status="failed", error_message=str(exc))
        raise
    summary = report["summary"]
    payload = {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "true_roas": summary.get("true_roas"),
        "meta_roas": summary.get("meta_roas"),
        "sales_gap": summary.get("sales_gap"),
        "revenue_with_shipping": summary.get("revenue_with_shipping"),
        "meta_purchase_value": summary.get("meta_purchase_value"),
        "ad_spend": summary.get("ad_spend"),
        "order_count": summary.get("order_count"),
    }
    scheduled_tasks.finish_run(run_id, status="success", summary=payload)
    return payload


def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler,
        TASK_CODE,
        run_scheduled_snapshot,
        "cron",
        day_of_week="tue",
        hour=9,
        minute=0,
        id=TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
