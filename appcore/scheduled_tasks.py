from __future__ import annotations

import json
import logging
from typing import Any

from appcore.db import query, query_one

log = logging.getLogger(__name__)

TASK_DEFINITIONS: dict[str, dict[str, str]] = {
    "shopifyid": {
        "code": "shopifyid",
        "name": "Shopify ID 获取",
        "description": "每天从店小秘 Shopify 在线商品库抓取 shopifyProductId，并回填 media_products.shopifyid。",
        "schedule": "每天 12:10",
    }
}


def task_definitions() -> list[dict[str, str]]:
    return list(TASK_DEFINITIONS.values())


def get_task_definition(task_code: str) -> dict[str, str]:
    return TASK_DEFINITIONS.get(task_code) or TASK_DEFINITIONS["shopifyid"]


def _decode_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}


def _normalize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    item["summary"] = _decode_summary(item.pop("summary_json", None))
    return item


def list_runs(task_code: str = "shopifyid", *, limit: int = 60) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 200))
    rows = query(
        """
        SELECT id, task_code, task_name, status, scheduled_for, started_at, finished_at,
               duration_seconds, summary_json, error_message, output_file
        FROM scheduled_task_runs
        WHERE task_code = %s
        ORDER BY started_at DESC, id DESC
        LIMIT %s
        """,
        (task_code, safe_limit),
    )
    return [_normalize_row(row) for row in rows if row]


def latest_run(task_code: str = "shopifyid") -> dict[str, Any] | None:
    row = query_one(
        """
        SELECT id, task_code, task_name, status, scheduled_for, started_at, finished_at,
               duration_seconds, summary_json, error_message, output_file
        FROM scheduled_task_runs
        WHERE task_code = %s
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """,
        (task_code,),
    )
    return _normalize_row(row)


def latest_failure_alert() -> dict[str, Any] | None:
    """Return the latest failed run only if it is still the latest run for that task."""
    for task in task_definitions():
        try:
            row = latest_run(task["code"])
        except Exception:
            log.warning("failed to load scheduled task alert", exc_info=True)
            continue
        if row and row.get("status") == "failed":
            return row
    return None
