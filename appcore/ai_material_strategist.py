"""AI素材军师项目服务。

Docs anchor:
docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import secrets
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from appcore import db, llm_client

log = logging.getLogger(__name__)

RANK_USE_CASE = "medias.ai_material_strategist_rank_products"
PRODUCT_ANALYSIS_USE_CASE = "medias.ai_material_strategist_product_analysis"
PROVIDER_CODE = "google_wj"
MODEL_ID = "gemini-3.5-flash"

_RJC_SUFFIX_RE = re.compile(r"[-_]?rjc$", re.IGNORECASE)
_MAX_AI_CANDIDATES = 60
_PROJECT_TOP_N = 30
_AI_RANKING_BATCH_SIZE = 20
_AI_RANKING_BATCH_TOP_N = 10
_DEFAULT_LLM_SPACING_SECONDS = 10.0

TARGET_COUNTRIES: tuple[dict[str, str], ...] = (
    {"country_code": "EN", "country_name": "英语", "lang": "en", "lang_name": "英语", "tier": "source"},
    {"country_code": "DE", "country_name": "德国", "lang": "de", "lang_name": "德语", "tier": "tier_1"},
    {"country_code": "FR", "country_name": "法国", "lang": "fr", "lang_name": "法语", "tier": "tier_1"},
    {"country_code": "IT", "country_name": "意大利", "lang": "it", "lang_name": "意大利语", "tier": "tier_2"},
    {"country_code": "ES", "country_name": "西班牙", "lang": "es", "lang_name": "西班牙语", "tier": "tier_2"},
    {"country_code": "JP", "country_name": "日本", "lang": "ja", "lang_name": "日语", "tier": "tier_2"},
    {"country_code": "SE", "country_name": "瑞典", "lang": "sv", "lang_name": "瑞典语", "tier": "tier_3"},
    {"country_code": "NL", "country_name": "荷兰", "lang": "nl", "lang_name": "荷兰语", "tier": "tier_3"},
    {"country_code": "PT", "country_name": "葡萄牙", "lang": "pt", "lang_name": "葡萄牙语", "tier": "tier_3"},
)

_TARGET_BY_COUNTRY = {item["country_code"]: item for item in TARGET_COUNTRIES}
_TARGET_BY_LANG = {item["lang"]: item for item in TARGET_COUNTRIES}
_TASK_PENDING_STATUSES = {"pending", "blocked"}
_TASK_IN_PROGRESS_STATUSES = {"raw_in_progress", "raw_review", "raw_done", "assigned", "review"}
_TASK_COMPLETED_STATUSES = {"done", "all_done"}
_TASK_CANCELLED_STATUSES = {"cancelled"}
_TASK_STATUS_LABELS = {
    "pending": "待处理",
    "in_progress": "进行中",
    "completed": "已完成",
    "cancelled": "已取消",
}
_PROJECT_LOCK_NAME = "ai_material_strategist_single_running_project"
_SHARE_TOKEN_BYTES = 24
_PROGRESS_LOG_LIMIT = 12
PROGRESS_STEPS: tuple[dict[str, str], ...] = (
    {"key": "snapshot", "label": "读取数据窗口", "description": "读取产品、广告、订单、明空素材新鲜度。"},
    {"key": "candidate_score", "label": "规则预筛打分", "description": "按消耗、订单、ROAS、广告数筛选候选品。"},
    {"key": "ai_ranking", "label": f"Top {_PROJECT_TOP_N} AI 复评", "description": "分批调用 GoogleWJ Gemini 复评候选产品。"},
    {"key": "material_context", "label": "补齐素材上下文", "description": "读取国家反馈、本地素材、明空素材和任务中心排程。"},
    {"key": "product_analysis", "label": "逐产品分析", "description": "逐个产品分析国家、素材、任务去重和补素材建议。"},
    {"key": "persist", "label": "保存结果", "description": "写入 Top 产品、AI 建议和操作入口。"},
    {"key": "summary", "label": "汇总结论", "description": "生成项目级统计和完成状态。"},
)
_PROGRESS_STEP_KEYS = tuple(step["key"] for step in PROGRESS_STEPS)
_STEP_START_PERCENT = {
    "snapshot": 0,
    "candidate_score": 14,
    "ai_ranking": 28,
    "material_context": 42,
    "product_analysis": 54,
    "persist": 88,
    "summary": 94,
}
_CLEAR_SNAPSHOT_FROM_STEPS = {"snapshot"}
_CLEAR_RANKING_FROM_STEPS = {"snapshot", "candidate_score", "ai_ranking"}
_CLEAR_PRODUCT_RESULTS_FROM_STEPS = {
    "snapshot",
    "candidate_score",
    "ai_ranking",
    "material_context",
    "product_analysis",
}
_RUNNER_STALE_SECONDS = 10 * 60
_SCHEDULED_RUNNER_STATES = {
    "resume_scheduled",
    "manual_resume_scheduled",
    "checkpoint_resume_scheduled",
}


class ProjectAlreadyRunningError(RuntimeError):
    def __init__(self, project: Mapping[str, Any] | None = None):
        super().__init__("已有 AI素材军师项目正在运行")
        self.project = dict(project or {})


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default, separators=(",", ":"))


def _json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _now_iso() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _initial_progress(*, message: str = "已创建，等待后台任务启动。") -> dict[str, Any]:
    now = _now_iso()
    return {
        "status": "running",
        "runner_state": "queued",
        "runner_heartbeat_at": None,
        "recovery": {},
        "percent": 0,
        "current_step": "queued",
        "current_step_label": "等待启动",
        "message": message,
        "steps": [
            {
                "key": step["key"],
                "label": step["label"],
                "description": step["description"],
                "status": "pending",
                "message": "",
                "started_at": None,
                "finished_at": None,
            }
            for step in PROGRESS_STEPS
        ],
        "product_progress": {
            "current_index": 0,
            "total": 0,
            "current_product_id": None,
            "current_product_code": "",
            "current_product_name": "",
        },
        "logs": [{"time": now, "level": "info", "message": message}],
        "updated_at": now,
    }


def _step_label(step_key: str) -> str:
    return next((step["label"] for step in PROGRESS_STEPS if step["key"] == step_key), step_key)


def _progress_step_index(step_key: str) -> int:
    for index, step in enumerate(PROGRESS_STEPS):
        if step["key"] == step_key:
            return index
    return -1


def _progress_update(
    progress: dict[str, Any],
    *,
    step_key: str,
    step_status: str,
    percent: float,
    message: str,
    project_status: str = "running",
    product_progress: Mapping[str, Any] | None = None,
    level: str = "info",
) -> dict[str, Any]:
    now = _now_iso()
    step_index = _progress_step_index(step_key)
    for index, step in enumerate(progress.get("steps") or []):
        if step.get("key") == step_key:
            step["status"] = step_status
            step["message"] = message
            if step_status == "running" and not step.get("started_at"):
                step["started_at"] = now
            if step_status in {"done", "failed", "interrupted"}:
                step["finished_at"] = now
                if not step.get("started_at"):
                    step["started_at"] = now
        elif step_index >= 0:
            if index < step_index and step.get("status") in {"pending", "running"}:
                step["status"] = "done"
                if not step.get("started_at"):
                    step["started_at"] = now
                step["finished_at"] = step.get("finished_at") or now
            if step_status in {"failed", "interrupted"} and index > step_index and step.get("status") == "pending":
                step["status"] = "skipped"

    if product_progress is not None:
        current_product = dict(progress.get("product_progress") or {})
        current_product.update(dict(product_progress))
        progress["product_progress"] = current_product

    logs = list(progress.get("logs") or [])
    logs.append({"time": now, "level": level, "message": message})
    progress.update({
        "status": project_status,
        "runner_state": project_status,
        "runner_heartbeat_at": now,
        "percent": int(round(_clamp(float(percent), 0, 100))),
        "current_step": step_key,
        "current_step_label": _step_label(step_key),
        "message": message,
        "logs": logs[-_PROGRESS_LOG_LIMIT:],
        "updated_at": now,
    })
    return progress


def _save_progress(project_id: int, progress: Mapping[str, Any]) -> None:
    db.execute(
        "UPDATE ai_material_strategist_projects SET progress_json=%s, updated_at=NOW() WHERE id=%s",
        (_json_dumps(progress), project_id),
    )


def _normalize_progress(progress: Mapping[str, Any] | None, *, message: str) -> dict[str, Any]:
    base = _initial_progress(message=message)
    if not isinstance(progress, Mapping) or not progress:
        return base

    for key in (
        "status", "runner_state", "runner_heartbeat_at", "recovery",
        "percent", "current_step", "current_step_label", "message", "updated_at",
    ):
        if key in progress:
            base[key] = progress[key]

    existing_steps = {
        str(step.get("key") or ""): dict(step)
        for step in progress.get("steps") or []
        if isinstance(step, Mapping)
    }
    merged_steps = []
    for step in base["steps"]:
        existing = existing_steps.get(step["key"]) or {}
        merged = dict(step)
        merged.update(existing)
        merged_steps.append(merged)
    base["steps"] = merged_steps

    product_progress = progress.get("product_progress")
    if isinstance(product_progress, Mapping):
        merged_product_progress = dict(base["product_progress"])
        merged_product_progress.update(dict(product_progress))
        base["product_progress"] = merged_product_progress

    logs = progress.get("logs")
    if isinstance(logs, list) and logs:
        base["logs"] = logs[-_PROGRESS_LOG_LIMIT:]
    return base


def _mark_recovery_state(
    progress: dict[str, Any],
    status: str,
    *,
    timestamp_key: str,
) -> dict[str, Any]:
    recovery = progress.get("recovery")
    if not isinstance(recovery, Mapping) or not recovery:
        return progress
    updated = dict(recovery)
    updated["status"] = status
    updated[timestamp_key] = _now_iso()
    progress["recovery"] = updated
    return progress


def _interrupted_progress(progress: dict[str, Any], *, message: str, reason: str) -> dict[str, Any]:
    current_step = str(progress.get("current_step") or "snapshot")
    progress = _progress_update(
        progress,
        step_key=current_step if current_step != "queued" else "snapshot",
        step_status="interrupted",
        percent=_safe_float(progress.get("percent")),
        message=message,
        project_status="interrupted",
        level="error",
    )
    progress["runner_state"] = "interrupted"
    recovery = progress.get("recovery") if isinstance(progress.get("recovery"), Mapping) else {}
    recovery = dict(recovery or {})
    recovery.update({
        "reason": reason,
        "status": "interrupted",
        "interrupted_at": _now_iso(),
        "auto_resume": bool(recovery.get("auto_resume")),
    })
    progress["recovery"] = recovery
    return progress


def _reset_progress_from_step(progress: dict[str, Any], step_key: str, *, message: str) -> dict[str, Any]:
    step_index = _progress_step_index(step_key)
    now = _now_iso()
    for index, step in enumerate(progress.get("steps") or []):
        if index < step_index:
            step["status"] = "done"
            step["message"] = step.get("message") or "保留已有断点。"
            if not step.get("started_at"):
                step["started_at"] = now
            step["finished_at"] = step.get("finished_at") or now
        elif step.get("key") == step_key:
            step["status"] = "pending"
            step["message"] = "将从此步骤起点重新执行。"
            step["started_at"] = None
            step["finished_at"] = None
        else:
            step["status"] = "pending"
            step["message"] = ""
            step["started_at"] = None
            step["finished_at"] = None

    logs = list(progress.get("logs") or [])
    logs.append({"time": now, "level": "warning", "message": message})
    progress.update({
        "status": "running",
        "runner_state": "manual_resume_scheduled",
        "runner_heartbeat_at": None,
        "percent": int(_STEP_START_PERCENT.get(step_key, 0)),
        "current_step": step_key,
        "current_step_label": _step_label(step_key),
        "message": message,
        "logs": logs[-_PROGRESS_LOG_LIMIT:],
        "product_progress": {
            "current_index": 0,
            "total": 0,
            "current_product_id": None,
            "current_product_code": "",
            "current_product_name": "",
        },
        "updated_at": now,
    })
    progress["recovery"] = {
        "reason": "manual_step_resume",
        "status": "scheduled",
        "step_key": step_key,
        "scheduled_at": now,
        "auto_resume": False,
    }
    return progress


def _with_project_lock(timeout_seconds: int = 5):
    conn = db.get_conn()
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, %s) AS lock_ok", (_PROJECT_LOCK_NAME, timeout_seconds))
            row = cur.fetchone() or {}
            locked = _safe_int(row.get("lock_ok")) == 1
        if locked:
            return conn
    except Exception:
        log.exception("AI material strategist lock acquire failed")
    try:
        conn.close()
    except Exception as exc:
        log.warning("Close connection failed during lock acquisition cleanup: %s", exc, exc_info=True)
    return None


def _release_project_lock(conn) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT RELEASE_LOCK(%s)", (_PROJECT_LOCK_NAME,))
    finally:
        conn.close()


def _save_project_snapshot(project_id: int, snapshot: Mapping[str, Any]) -> None:
    db.execute(
        """
        UPDATE ai_material_strategist_projects
        SET data_window_json = %s,
            data_snapshot_json = %s,
            updated_at = NOW()
        WHERE id = %s
        """,
        (
            _json_dumps(snapshot.get("window") or {}),
            _json_dumps(snapshot),
            project_id,
        ),
    )


def _save_project_ranking(project_id: int, ranking: Mapping[str, Any]) -> None:
    db.execute(
        """
        UPDATE ai_material_strategist_projects
        SET ranking_prompt_json = %s,
            ranking_result_json = %s,
            updated_at = NOW()
        WHERE id = %s
        """,
        (
            _json_dumps(ranking.get("prompt_debug") or {}),
            _json_dumps(ranking),
            project_id,
        ),
    )


def _safe_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return value > 0
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _project_provider_code(project_row: Mapping[str, Any] | None) -> str:
    return str((project_row or {}).get("provider_code") or PROVIDER_CODE).strip() or PROVIDER_CODE


def _project_model_id(project_row: Mapping[str, Any] | None) -> str:
    return str((project_row or {}).get("model_id") or MODEL_ID).strip() or MODEL_ID


def _llm_spacing_seconds() -> float:
    raw = os.environ.get("AI_MATERIAL_STRATEGIST_LLM_SPACING_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_LLM_SPACING_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_LLM_SPACING_SECONDS
    return max(0.0, min(value, 60.0))


def _pace_llm_call(last_finished_at: float, *, run_ai: bool) -> float:
    if not run_ai or last_finished_at <= 0:
        return time.monotonic()
    spacing = _llm_spacing_seconds()
    if spacing <= 0:
        return time.monotonic()
    wait = spacing - (time.monotonic() - last_finished_at)
    if wait > 0:
        time.sleep(wait)
    return time.monotonic()


def _to_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value) if value else None


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone().replace(tzinfo=None)
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except ValueError:
        pass
    for fmt, size in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16)):
        try:
            return datetime.strptime(raw[:size], fmt)
        except ValueError:
            continue
    return None


def _is_stale_time(value: Any, *, now: datetime | None = None) -> bool:
    parsed = _to_datetime(value)
    if parsed is None:
        return False
    return ((now or datetime.now()) - parsed).total_seconds() >= _RUNNER_STALE_SECONDS


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _roas(numerator: Any, denominator: Any) -> float | None:
    denom = _safe_float(denominator)
    if denom <= 0:
        return None
    return round(_safe_float(numerator) / denom, 4)


def _normalize_country_code(value: Any, *, lang: Any = None) -> str:
    raw = str(value or "").strip()
    upper = raw.upper()
    if upper in _TARGET_BY_COUNTRY:
        return upper
    lower = raw.lower()
    if lower in _TARGET_BY_LANG:
        return _TARGET_BY_LANG[lower]["country_code"]
    lang_lower = str(lang or "").strip().lower()
    if lang_lower in _TARGET_BY_LANG:
        return _TARGET_BY_LANG[lang_lower]["country_code"]
    if upper == "JA":
        return "JP"
    return upper


def _lang_for_country_code(country_code: Any) -> str:
    code = _normalize_country_code(country_code)
    return (_TARGET_BY_COUNTRY.get(code) or {}).get("lang", str(country_code or "").strip().lower())


def _country_code_for_action(action: Mapping[str, Any]) -> str:
    return _normalize_country_code(action.get("country_code"), lang=action.get("lang"))


def _task_status_group(row: Mapping[str, Any]) -> str:
    status = str(row.get("status") or "").strip()
    parent_status = str(row.get("parent_status") or "").strip()
    if status in _TASK_CANCELLED_STATUSES or row.get("cancelled_at"):
        return "cancelled"
    if status in _TASK_COMPLETED_STATUSES:
        return "completed"
    if parent_status in _TASK_CANCELLED_STATUSES or row.get("parent_cancelled_at"):
        return "cancelled"
    if status in _TASK_PENDING_STATUSES:
        return "pending"
    if status in _TASK_IN_PROGRESS_STATUSES:
        return "in_progress"
    return "in_progress"


def _task_blocks_recommendation(task: Mapping[str, Any] | None) -> bool:
    return bool(task and task.get("status_group") in {"pending", "in_progress", "completed"})


def _task_sort_key(task: Mapping[str, Any]) -> tuple[int, int]:
    group_rank = {"in_progress": 0, "pending": 1, "completed": 2, "cancelled": 3}
    return (group_rank.get(str(task.get("status_group") or ""), 9), -_safe_int(task.get("task_id")))


def strip_rjc(product_code: str | None) -> str:
    """去掉本地产品 code 末尾的 -rjc / _rjc，用于匹配明空快照。"""
    return _RJC_SUFFIX_RE.sub("", str(product_code or "").strip()).strip()


def _current_meta_business_date(now: datetime | None = None) -> date:
    current = now or datetime.now()
    if current.hour < 16:
        return current.date() - timedelta(days=1)
    return current.date()


def _placeholders(values: Iterable[Any]) -> str:
    return ",".join(["%s"] * len(list(values)))


def _chunked(items: list[dict], size: int) -> Iterable[list[dict]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _query_one_safe(sql: str, args: tuple[Any, ...] = ()) -> dict | None:
    try:
        return db.query_one(sql, args)
    except Exception:
        log.exception("AI material strategist data quality query failed")
        return None


def _data_quality() -> dict[str, Any]:
    daily = _query_one_safe(
        "SELECT MAX(DATE(COALESCE(meta_business_date, report_date))) AS value "
        "FROM meta_ad_daily_campaign_metrics"
    )
    realtime = _query_one_safe(
        "SELECT MAX(snapshot_at) AS value FROM meta_ad_realtime_daily_campaign_metrics"
    )
    orders = _query_one_safe(
        "SELECT MAX(updated_at) AS value FROM dianxiaomi_order_lines"
    )
    mk = _query_one_safe(
        "SELECT MAX(snapshot_at) AS value FROM mingkong_material_daily_snapshots"
    )
    return {
        "meta_daily_max_business_date": _iso((daily or {}).get("value")),
        "meta_realtime_max_snapshot_at": _iso((realtime or {}).get("value")),
        "orders_max_updated_at": _iso((orders or {}).get("value")),
        "mingkong_max_snapshot_at": _iso((mk or {}).get("value")),
    }


def _load_products() -> list[dict]:
    return db.query(
        """
        SELECT
          p.id, p.user_id, p.name, p.product_code, p.source, p.ai_score,
          p.ai_evaluation_result, p.ai_evaluation_detail, p.created_at,
          p.purchase_price, p.packet_cost_estimated, p.packet_cost_actual,
          p.standalone_price, p.standalone_shipping_fee,
          c.delivery_status, c.ad_spend_usd AS cached_ad_spend_usd,
          c.active_7d_ad_spend_usd AS cached_active_7d_ad_spend_usd,
          c.overall_roas AS cached_overall_roas
        FROM media_products p
        LEFT JOIN media_product_ad_summary_cache c ON c.product_id = p.id
        WHERE p.deleted_at IS NULL
          AND COALESCE(p.archived, 0) = 0
        ORDER BY p.id DESC
        """
    )


def _load_ad_rows(date_from: date, current_day: date) -> list[dict]:
    rows: list[dict] = []
    daily_to = current_day - timedelta(days=1)
    if daily_to >= date_from:
        rows.extend(db.query(
            """
            SELECT
              product_id,
              DATE(COALESCE(meta_business_date, report_date)) AS business_date,
              SUM(COALESCE(spend_usd, 0)) AS spend_usd,
              SUM(COALESCE(purchase_value_usd, 0)) AS purchase_value_usd,
              SUM(COALESCE(result_count, 0)) AS result_count,
              COUNT(DISTINCT NULLIF(campaign_name, '')) AS ad_count
            FROM meta_ad_daily_campaign_metrics
            WHERE product_id IS NOT NULL
              AND DATE(COALESCE(meta_business_date, report_date)) BETWEEN %s AND %s
            GROUP BY product_id, DATE(COALESCE(meta_business_date, report_date))
            """,
            (date_from, daily_to),
        ))

    rows.extend(db.query(
        """
        SELECT
          p.id AS product_id,
          m.business_date,
          SUM(COALESCE(m.spend_usd, 0)) AS spend_usd,
          SUM(COALESCE(m.purchase_value_usd, 0)) AS purchase_value_usd,
          SUM(COALESCE(m.result_count, 0)) AS result_count,
          COUNT(DISTINCT NULLIF(m.campaign_name, '')) AS ad_count
        FROM (
          SELECT m.*
          FROM (
            SELECT latest_day.business_date, latest_day.ad_account_id, MAX(rt.snapshot_at) AS max_snapshot_at
            FROM meta_ad_realtime_daily_campaign_metrics rt
            INNER JOIN (
              SELECT ad_account_id, MAX(business_date) AS business_date
              FROM meta_ad_realtime_daily_campaign_metrics
              WHERE data_completeness = 'realtime_partial'
                AND business_date BETWEEN %s AND %s
              GROUP BY ad_account_id
            ) latest_day
              ON rt.business_date = latest_day.business_date
             AND (rt.ad_account_id <=> latest_day.ad_account_id)
            WHERE rt.data_completeness = 'realtime_partial'
            GROUP BY latest_day.business_date, latest_day.ad_account_id
          ) latest
          STRAIGHT_JOIN meta_ad_realtime_daily_campaign_metrics m
            ON m.business_date = latest.business_date
           AND (m.ad_account_id <=> latest.ad_account_id)
           AND m.snapshot_at = latest.max_snapshot_at
          WHERE m.data_completeness = 'realtime_partial'
        ) m
        JOIN media_products p
          ON p.deleted_at IS NULL
         AND p.product_code IS NOT NULL
         AND p.product_code <> ''
         AND (
           LOWER(COALESCE(m.normalized_campaign_code, '')) LIKE CONCAT(LOWER(p.product_code), '%%')
           OR LOWER(COALESCE(m.campaign_name, '')) LIKE CONCAT(LOWER(p.product_code), '%%')
         )
        GROUP BY p.id, m.business_date
        """,
        (date_from, current_day),
    ))
    return rows


def _load_order_rows(date_from: date, current_day: date) -> list[dict]:
    return db.query(
        """
        SELECT
          op.product_id,
          op.business_date,
          COUNT(DISTINCT d.dxm_package_id) AS order_count,
          SUM(COALESCE(
              op.revenue_usd,
              COALESCE(op.line_amount_usd, d.line_amount, 0)
              + COALESCE(op.shipping_allocated_usd, d.ship_amount, 0)
          )) AS revenue_usd,
          SUM(COALESCE(op.profit_usd, 0)) AS profit_usd,
          SUM(COALESCE(op.ad_cost_usd, 0)) AS attributed_ad_cost_usd
        FROM order_profit_lines op
        JOIN dianxiaomi_order_lines d ON d.id = op.dxm_order_line_id
        WHERE op.product_id IS NOT NULL
          AND op.business_date BETWEEN %s AND %s
        GROUP BY op.product_id, op.business_date
        """,
        (date_from, current_day),
    )


def _load_local_counts() -> dict[int, dict[str, Any]]:
    rows = db.query(
        """
        SELECT product_id, LOWER(COALESCE(lang, 'en')) AS lang, COUNT(*) AS item_count
        FROM media_items
        WHERE deleted_at IS NULL
        GROUP BY product_id, LOWER(COALESCE(lang, 'en'))
        """
    )
    out: dict[int, dict[str, Any]] = defaultdict(lambda: {"item_count": 0, "langs": {}})
    for row in rows:
        pid = _safe_int(row.get("product_id"))
        lang = str(row.get("lang") or "en").strip().lower()
        count = _safe_int(row.get("item_count"))
        out[pid]["item_count"] += count
        out[pid]["langs"][lang] = count
    return dict(out)


def _add_window_metrics(target: dict, row: Mapping[str, Any], current_day: date, prefix: str) -> None:
    business_date = _to_date(row.get("business_date"))
    if business_date is None:
        return
    windows = {
        "today": current_day,
        "yesterday": current_day - timedelta(days=1),
        "7d": current_day - timedelta(days=6),
        "30d": current_day - timedelta(days=29),
    }
    if business_date == windows["today"]:
        suffixes = ("today", "7d", "30d")
    elif business_date == windows["yesterday"]:
        suffixes = ("yesterday", "7d", "30d")
    elif business_date >= windows["7d"]:
        suffixes = ("7d", "30d")
    elif business_date >= windows["30d"]:
        suffixes = ("30d",)
    else:
        return
    if prefix == "ad":
        mapping = {
            "spend": "spend_usd",
            "purchase_value": "purchase_value_usd",
            "results": "result_count",
            "ad_count": "ad_count",
        }
    else:
        mapping = {
            "orders": "order_count",
            "revenue": "revenue_usd",
            "profit": "profit_usd",
            "attributed_ad_cost": "attributed_ad_cost_usd",
        }
    for suffix in suffixes:
        for key, source_key in mapping.items():
            target[f"{key}_{suffix}"] = target.get(f"{key}_{suffix}", 0.0) + _safe_float(row.get(source_key))


def _empty_metrics() -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for suffix in ("today", "yesterday", "7d", "30d"):
        for key in (
            "spend", "purchase_value", "results", "ad_count",
            "orders", "revenue", "profit", "attributed_ad_cost",
        ):
            metrics[f"{key}_{suffix}"] = 0.0
    return metrics


def build_data_snapshot(now: datetime | None = None) -> dict[str, Any]:
    """读取当前素材、广告、订单和明空数据，返回一次项目运行的输入快照。"""
    from appcore import product_roas

    current_day = _current_meta_business_date(now)
    date_from = current_day - timedelta(days=29)
    products = _load_products()
    metrics_by_product: dict[int, dict[str, Any]] = defaultdict(_empty_metrics)
    for row in _load_ad_rows(date_from, current_day):
        _add_window_metrics(metrics_by_product[_safe_int(row.get("product_id"))], row, current_day, "ad")
    for row in _load_order_rows(date_from, current_day):
        _add_window_metrics(metrics_by_product[_safe_int(row.get("product_id"))], row, current_day, "order")
    local_counts = _load_local_counts()

    # Get configured RMB/USD rate
    rmb_per_usd = product_roas.get_configured_rmb_per_usd()

    product_rows: list[dict[str, Any]] = []
    for product in products:
        pid = _safe_int(product.get("id"))
        metrics = dict(metrics_by_product.get(pid) or _empty_metrics())
        metrics["true_roas_30d"] = _roas(metrics.get("revenue_30d"), metrics.get("spend_30d"))
        metrics["meta_roas_30d"] = _roas(metrics.get("purchase_value_30d"), metrics.get("spend_30d"))
        metrics["profit_margin_30d"] = _roas(metrics.get("profit_30d"), metrics.get("revenue_30d"))
        local = local_counts.get(pid) or {"item_count": 0, "langs": {}}

        # Calculate breakeven ROAS
        roas_calc = product_roas.calculate_break_even_roas(
            purchase_price=product.get("purchase_price"),
            estimated_packet_cost=product.get("packet_cost_estimated"),
            actual_packet_cost=product.get("packet_cost_actual"),
            standalone_price=product.get("standalone_price"),
            standalone_shipping_fee=product.get("standalone_shipping_fee"),
            rmb_per_usd=rmb_per_usd,
        )
        effective_breakeven_roas = roas_calc.get("effective_roas")

        row = {
            "product_id": pid,
            "product_name": product.get("name") or "",
            "product_code": product.get("product_code") or "",
            "user_id": product.get("user_id"),
            "source": product.get("source") or "",
            "ai_score": _safe_float(product.get("ai_score")),
            "ai_evaluation_result": product.get("ai_evaluation_result") or "",
            "delivery_status": product.get("delivery_status") or "never",
            "cached_overall_roas": _safe_float(product.get("cached_overall_roas")),
            "cached_ad_spend_usd": _safe_float(product.get("cached_ad_spend_usd")),
            "cached_active_7d_ad_spend_usd": _safe_float(product.get("cached_active_7d_ad_spend_usd")),
            "local_material_count": _safe_int(local.get("item_count")),
            "local_material_langs": dict(local.get("langs") or {}),
            "effective_breakeven_roas": effective_breakeven_roas,
            **metrics,
        }
        product_rows.append(row)

    return {
        "generated_at": datetime.now().isoformat(sep=" "),
        "window": {
            "current_meta_business_date": current_day.isoformat(),
            "yesterday": (current_day - timedelta(days=1)).isoformat(),
            "last_7d_from": (current_day - timedelta(days=6)).isoformat(),
            "last_30d_from": date_from.isoformat(),
        },
        "data_quality": _data_quality(),
        "products": product_rows,
    }


def _selection_reasons(row: Mapping[str, Any]) -> list[str]:
    reasons = []
    if _safe_float(row.get("spend_30d")) >= 50:
        reasons.append("30天消耗有量")
    if _safe_float(row.get("orders_30d")) >= 8:
        reasons.append("30天订单有量")
    if _safe_float(row.get("spend_7d")) >= 25:
        reasons.append("近7天仍有消耗")
    if _safe_float(row.get("spend_yesterday")) >= 10:
        reasons.append("昨天仍在投放")
    roas = row.get("true_roas_30d")
    if roas is not None and _safe_float(roas) >= 1.5:
        reasons.append("真实ROAS较好")
    if _safe_float(row.get("local_material_count")) <= 2:
        reasons.append("本地素材可补空间大")
    return reasons


def _score_product(row: Mapping[str, Any]) -> float:
    spend30 = _safe_float(row.get("spend_30d"))
    orders30 = _safe_float(row.get("orders_30d"))
    results30 = _safe_float(row.get("results_30d"))
    adcnt30 = _safe_float(row.get("ad_count_30d"))
    spend7 = _safe_float(row.get("spend_7d"))
    spend_y = _safe_float(row.get("spend_yesterday"))
    spend_t = _safe_float(row.get("spend_today"))
    orders7 = _safe_float(row.get("orders_7d"))
    true_roas = _safe_float(row.get("true_roas_30d"))
    meta_roas = _safe_float(row.get("meta_roas_30d"))
    profit30 = _safe_float(row.get("profit_30d"))

    volume = 2.2 * math.log1p(spend30) + 3.0 * math.log1p(orders30)
    volume += 0.9 * math.log1p(results30) + 0.8 * math.log1p(adcnt30)
    efficiency = min(true_roas, 4.0) * 4.0 + min(meta_roas, 4.0) * 2.0
    freshness = min(spend7 / 250.0, 1.5) * 4.0
    freshness += min(spend_y / 80.0, 1.5) * 3.0
    freshness += min(spend_t / 80.0, 1.2) * 2.0
    freshness += min(orders7 / 20.0, 2.0) * 3.0
    profit_bonus = _clamp(profit30 / 500.0, -4.0, 3.0)
    return round(volume + efficiency + freshness + profit_bonus, 4)


def score_product_rows(rows: list[dict], limit: int = _MAX_AI_CANDIDATES) -> list[dict]:
    candidates: list[dict] = []
    for row in rows:
        if not (
            _safe_float(row.get("spend_30d")) >= 50
            or _safe_float(row.get("orders_30d")) >= 8
            or _safe_float(row.get("spend_7d")) >= 25
            or _safe_float(row.get("spend_yesterday")) >= 10
        ):
            continue
        scored = dict(row)
        scored["score"] = _score_product(row)
        scored["selection_reasons"] = _selection_reasons(row)
        candidates.append(scored)
    candidates.sort(
        key=lambda item: (
            -_safe_float(item.get("score")),
            -_safe_float(item.get("spend_30d")),
            -_safe_float(item.get("orders_30d")),
            str(item.get("product_code") or ""),
        )
    )
    return candidates[:limit]


def _llm_json(result: dict) -> dict:
    if isinstance(result.get("json"), dict):
        return result["json"]
    text = str(result.get("text") or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return _json_loads(text, {})


def _rank_input(row: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "product_id", "product_code", "product_name", "score", "selection_reasons",
        "spend_today", "spend_yesterday", "spend_7d", "spend_30d",
        "orders_today", "orders_yesterday", "orders_7d", "orders_30d",
        "revenue_30d", "profit_30d", "true_roas_30d", "meta_roas_30d",
        "ad_count_30d", "results_30d", "local_material_count", "local_material_langs",
        "delivery_status",
    )
    return {key: row.get(key) for key in keys}


RANKING_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ranked_products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer"},
                    "product_code": {"type": "string"},
                    "rank": {"type": "integer"},
                    "score": {"type": "number"},
                    "volume_reason": {"type": "string"},
                    "efficiency_reason": {"type": "string"},
                    "risk_reason": {"type": "string"},
                    "why_selected": {"type": "string"},
                },
                "required": ["product_id", "rank", "score", "why_selected"],
            },
        },
        "rejected_high_roas_low_volume": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ranked_products"],
}


PRODUCT_ANALYSIS_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "product_id": {"type": "integer"},
        "product_code": {"type": "string"},
        "overall_judgement": {"type": "string"},
        "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
        "primary_action": {
            "type": "string",
            "enum": ["expand_country", "same_country_new_material", "weak_country_retest", "hold", "investigate"],
        },
        "country_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "country_code": {"type": "string"},
                    "lang": {"type": "string"},
                    "action": {"type": "string"},
                    "priority": {"type": "string"},
                    "reason": {"type": "string"},
                    "material_key": {"type": "string"},
                    "video_path": {"type": "string"},
                },
                "required": ["country_code", "lang", "action", "priority", "reason"],
            },
        },
        "material_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "source_type": {"type": "string"},
                    "media_item_id": {"type": "integer"},
                    "material_key": {"type": "string"},
                    "video_path": {"type": "string"},
                    "target_langs": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["action", "reason"],
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "next_check": {"type": "string"},
    },
    "required": ["product_id", "product_code", "overall_judgement", "priority", "primary_action"],
}


def _ranking_prompt(payload: dict) -> str:
    return (
        "你是跨境电商素材投放军师。只根据输入 JSON 判断，不编造数据。\n"
        "表现好必须同时有量和效率；1-2 单高 ROAS 不得排前。优先选择有持续消耗、订单、广告数、"
        "并且有可补素材空间的产品。输出严格 JSON，字段符合 response_schema。\n"
        f"输入数据：\n{_json_dumps(payload)}"
    )


def _product_prompt(payload: dict) -> str:
    return (
        "你是跨境电商 AI素材军师。只分析当前一个产品，不编造输入中没有的数据。\n"
        "请先判断产品阶段，再给补素材操作建议。建议必须落到国家、语言、素材或 source_material_candidates 中的素材；"
        "source_type=local_en_cjh 表示美国站已入库的自制 EN 素材，可作为搬运欧洲小语种的源素材，不能建议重复加入明空素材库；"
        "必须读取 task_assignments：已有待处理/进行中/已完成任务的国家或素材只标注任务，不要重复建议创建翻译任务；"
        "已取消任务可以建议重排，但要写明曾取消的任务ID；"
        "如果数据不足，明确缺什么。输出严格 JSON，字段符合 response_schema。\n"
        f"当前产品数据：\n{_json_dumps(payload)}"
    )


def _fallback_ranking(candidates: list[dict], error: str | None = None) -> dict[str, Any]:
    top = candidates[:_PROJECT_TOP_N]
    return {
        "selected_product_ids": [_safe_int(item.get("product_id")) for item in top],
        "ranking_result": {
            "mode": "deterministic_fallback",
            "error": error or "",
            "ranked_products": [
                {
                    "product_id": item.get("product_id"),
                    "product_code": item.get("product_code"),
                    "rank": index + 1,
                    "score": item.get("score"),
                    "why_selected": " / ".join(item.get("selection_reasons") or []) or "规则打分靠前",
                }
                for index, item in enumerate(top)
            ],
        },
        "prompt_debug": {"mode": "deterministic_fallback"},
    }


def _run_ai_ranking(
    candidates: list[dict],
    *,
    project_id: int,
    user_id: int | None,
    run_ai: bool,
    provider_code: str = PROVIDER_CODE,
    model_id: str = MODEL_ID,
) -> dict[str, Any]:
    if not run_ai:
        return _fallback_ranking(candidates)
    try:
        batch_results: list[dict[str, Any]] = []
        merged_candidates: list[dict[str, Any]] = []
        last_llm_finished_at = 0.0
        for batch_index, batch in enumerate(_chunked(candidates, _AI_RANKING_BATCH_SIZE), start=1):
            payload = {
                "batch_index": batch_index,
                "rule": f"本批最多输出 Top{_AI_RANKING_BATCH_TOP_N}，剔除高ROAS低量产品。",
                "products": [_rank_input(row) for row in batch],
            }
            _pace_llm_call(last_llm_finished_at, run_ai=run_ai)
            result = llm_client.invoke_generate(
                RANK_USE_CASE,
                prompt=_ranking_prompt(payload),
                user_id=user_id,
                project_id=str(project_id),
                response_schema=RANKING_RESPONSE_SCHEMA,
                temperature=0.15,
                max_output_tokens=4096,
                provider_override=provider_code,
                model_override=model_id,
                billing_extra={"stage": "batch_rank", "batch_index": batch_index},
                timeout_seconds=180,
            )
            last_llm_finished_at = time.monotonic()
            parsed = _llm_json(result)
            batch_results.append({
                "input": payload,
                "output": parsed,
                "usage_log_id": result.get("usage_log_id"),
                "prompt": _ranking_prompt(payload),
                "response_text": result.get("text"),
                "provider": provider_code,
                "model": model_id,
            })
            ids = {_safe_int(item.get("product_id")) for item in parsed.get("ranked_products") or []}
            by_id = {_safe_int(item.get("product_id")): item for item in batch}
            merged_candidates.extend(by_id[pid] for pid in ids if pid in by_id)

        if not merged_candidates:
            return _fallback_ranking(candidates, "AI batch ranking returned empty")
        merged_candidates = score_product_rows(merged_candidates, limit=len(merged_candidates))
        final_payload = {
            "rule": f"从所有批次候选里输出最终 Top {_PROJECT_TOP_N}，仍然坚持有量 + 效率。",
            "products": [_rank_input(row) for row in merged_candidates],
        }
        _pace_llm_call(last_llm_finished_at, run_ai=run_ai)
        final = llm_client.invoke_generate(
            RANK_USE_CASE,
            prompt=_ranking_prompt(final_payload),
            user_id=user_id,
            project_id=str(project_id),
            response_schema=RANKING_RESPONSE_SCHEMA,
            temperature=0.1,
            max_output_tokens=4096,
            provider_override=provider_code,
            model_override=model_id,
            billing_extra={"stage": "final_rank"},
            timeout_seconds=180,
        )
        parsed_final = _llm_json(final)
        ordered_ids = [
            _safe_int(item.get("product_id"))
            for item in sorted(parsed_final.get("ranked_products") or [], key=lambda item: _safe_int(item.get("rank")))
        ]
        ordered_ids = [pid for pid in ordered_ids if pid]
        if not ordered_ids:
            return _fallback_ranking(candidates, "AI final ranking returned empty")
        return {
            "selected_product_ids": ordered_ids[:_PROJECT_TOP_N],
            "ranking_result": {
                "mode": "ai",
                "batch_results": batch_results,
                "final_input": final_payload,
                "final_output": parsed_final,
                "final_usage_log_id": final.get("usage_log_id"),
                "final_prompt": _ranking_prompt(final_payload),
                "final_response_text": final.get("text"),
                "provider": provider_code,
                "model": model_id,
            },
            "prompt_debug": {
                "provider": provider_code,
                "model": model_id,
                "use_case": RANK_USE_CASE,
                "batch_count": len(batch_results),
            },
        }
    except Exception as exc:
        log.exception("AI material strategist ranking failed")
        return _fallback_ranking(candidates, str(exc))


def _load_country_summaries(product_ids: list[int]) -> dict[int, list[dict]]:
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = db.query(
        f"""
        SELECT product_id, LOWER(lang) AS lang, item_count, pushed_video_count,
               ad_spend_usd, purchase_value_usd, ad_roas, active_7d_ad_spend_usd
        FROM media_product_lang_ad_summary_cache
        WHERE product_id IN ({placeholders})
        """,
        tuple(product_ids),
    )
    by_product: dict[int, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        pid = _safe_int(row.get("product_id"))
        lang = str(row.get("lang") or "").lower()
        by_product[pid][lang] = {
            "lang": lang,
            "item_count": _safe_int(row.get("item_count")),
            "pushed_video_count": _safe_int(row.get("pushed_video_count")),
            "ad_spend_usd": _safe_float(row.get("ad_spend_usd")),
            "purchase_value_usd": _safe_float(row.get("purchase_value_usd")),
            "ad_roas": row.get("ad_roas") if row.get("ad_roas") is not None else None,
            "active_7d_ad_spend_usd": _safe_float(row.get("active_7d_ad_spend_usd")),
        }

    out: dict[int, list[dict]] = {}
    for pid in product_ids:
        items = []
        existing = by_product.get(pid) or {}
        for country in TARGET_COUNTRIES:
            lang = country["lang"]
            row = dict(existing.get(lang) or {})
            row.update(country)
            row.setdefault("item_count", 0)
            row.setdefault("pushed_video_count", 0)
            row.setdefault("ad_spend_usd", 0.0)
            row.setdefault("purchase_value_usd", 0.0)
            row.setdefault("ad_roas", None)
            row.setdefault("active_7d_ad_spend_usd", 0.0)
            row["delivery_status"] = (
                "active" if _safe_float(row["active_7d_ad_spend_usd"]) > 0
                else "stopped" if _safe_float(row["ad_spend_usd"]) > 0
                else "never"
            )
            items.append(row)
        out[pid] = items
    return out


def _local_video_url(object_key: str) -> str:
    return f"/medias/object?object_key={quote(object_key, safe='')}"


def _load_local_materials(product_ids: list[int]) -> dict[int, list[dict]]:
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = db.query(
        f"""
        SELECT
          i.id, i.product_id, LOWER(COALESCE(i.lang, 'en')) AS lang,
          i.filename, i.display_name, i.object_key, i.task_id,
          i.cover_object_key, i.duration_seconds, i.created_at,
          b.mk_video_path,
          COUNT(DISTINCT CASE WHEN l.status = 'success' THEN l.id END) AS push_count
        FROM media_items i
        LEFT JOIN media_item_mk_bindings b ON b.media_item_id = i.id
        LEFT JOIN media_push_logs l ON l.item_id = i.id
        WHERE i.deleted_at IS NULL
          AND i.product_id IN ({placeholders})
        GROUP BY i.id, i.product_id, i.lang, i.filename, i.display_name,
                 i.object_key, i.task_id, i.cover_object_key, i.duration_seconds, i.created_at, b.mk_video_path
        ORDER BY i.product_id, i.created_at DESC, i.id DESC
        """,
        tuple(product_ids),
    )
    out: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        object_key = str(row.get("object_key") or "").strip()
        cover_object_key = str(row.get("cover_object_key") or "").strip()
        item = {
            "id": _safe_int(row.get("id")),
            "product_id": _safe_int(row.get("product_id")),
            "lang": row.get("lang") or "en",
            "filename": row.get("filename") or "",
            "display_name": row.get("display_name") or row.get("filename") or "",
            "object_key": object_key,
            "cover_object_key": cover_object_key,
            "task_id": row.get("task_id") or "",
            "duration_seconds": _safe_float(row.get("duration_seconds")),
            "created_at": _iso(row.get("created_at")),
            "mk_video_path": row.get("mk_video_path") or "",
            "push_count": _safe_int(row.get("push_count")),
            "video_url": _local_video_url(object_key) if object_key else "",
            "cover_url": _local_video_url(cover_object_key) if cover_object_key else "",
        }
        out[item["product_id"]].append(item)
    return dict(out)


def _serialize_task_assignment(row: Mapping[str, Any]) -> dict[str, Any]:
    country_code = _normalize_country_code(row.get("country_code"))
    lang = _lang_for_country_code(country_code)
    status_group = _task_status_group(row)
    media_item_id = _safe_int(row.get("media_item_id")) or _safe_int(row.get("parent_media_item_id"))
    task_id = _safe_int(row.get("id"))
    return {
        "task_id": task_id,
        "parent_task_id": _safe_int(row.get("parent_task_id")),
        "product_id": _safe_int(row.get("media_product_id")),
        "media_item_id": media_item_id,
        "country_code": country_code,
        "lang": lang,
        "raw_country_code": str(row.get("country_code") or "").strip().upper(),
        "status": row.get("status") or "",
        "status_group": status_group,
        "status_label": _TASK_STATUS_LABELS.get(status_group, status_group),
        "parent_status": row.get("parent_status") or "",
        "assignee_id": row.get("assignee_id"),
        "is_urgent": bool(row.get("is_urgent")),
        "last_reason": row.get("last_reason") or "",
        "task_url": f"/tasks/detail/{task_id}" if task_id else "",
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "claimed_at": _iso(row.get("claimed_at")),
        "completed_at": _iso(row.get("completed_at")),
        "cancelled_at": _iso(row.get("cancelled_at") or row.get("parent_cancelled_at")),
    }


def _load_task_assignments(product_ids: list[int]) -> dict[int, list[dict]]:
    product_ids = sorted({_safe_int(pid) for pid in product_ids if _safe_int(pid) > 0})
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = db.query(
        f"""
        SELECT
          c.id, c.parent_task_id, c.media_product_id, c.media_item_id,
          c.country_code, c.assignee_id, c.status, c.last_reason, c.is_urgent,
          c.created_at, c.updated_at, c.claimed_at, c.completed_at, c.cancelled_at,
          p.media_item_id AS parent_media_item_id,
          p.status AS parent_status,
          p.cancelled_at AS parent_cancelled_at
        FROM tasks c
        LEFT JOIN tasks p ON p.id = c.parent_task_id
        WHERE c.media_product_id IN ({placeholders})
          AND c.parent_task_id IS NOT NULL
        ORDER BY c.media_product_id ASC, c.updated_at DESC, c.id DESC
        """,
        tuple(product_ids),
    )
    out: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        task = _serialize_task_assignment(row)
        if not task["task_id"] or not task["product_id"] or not task["country_code"]:
            continue
        out[task["product_id"]].append(task)
    return dict(out)


def _enrich_country_summaries_with_tasks(countries: list[dict], tasks: list[dict]) -> list[dict]:
    by_country: dict[str, list[dict]] = defaultdict(list)
    for task in tasks:
        code = _normalize_country_code(task.get("country_code"), lang=task.get("lang"))
        if code:
            by_country[code].append(task)
    for items in by_country.values():
        items.sort(key=_task_sort_key)

    enriched: list[dict] = []
    for country in countries:
        item = dict(country)
        code = _normalize_country_code(item.get("country_code"), lang=item.get("lang"))
        country_tasks = list(by_country.get(code) or [])
        blocking_task = next((task for task in country_tasks if _task_blocks_recommendation(task)), None)
        cancelled_task = next((task for task in country_tasks if task.get("status_group") == "cancelled"), None)
        status_counts: dict[str, int] = defaultdict(int)
        for task in country_tasks:
            status_counts[str(task.get("status_group") or "unknown")] += 1
        item["tasks"] = country_tasks[:8]
        item["blocking_task"] = blocking_task
        item["cancelled_task"] = cancelled_task
        item["has_active_task"] = bool(blocking_task)
        item["task_status_counts"] = dict(status_counts)
        enriched.append(item)
    return enriched


def _task_assignments_summary(tasks: list[dict]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    blocking = []
    cancelled = []
    for task in tasks:
        group = str(task.get("status_group") or "unknown")
        counts[group] += 1
        if _task_blocks_recommendation(task):
            blocking.append(task)
        elif group == "cancelled":
            cancelled.append(task)
    return {
        "total": len(tasks),
        "status_counts": dict(counts),
        "blocking_count": len(blocking),
        "cancelled_count": len(cancelled),
    }


def _mk_search_codes(product_codes: Iterable[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for code in product_codes:
        raw = str(code or "").strip().lower()
        stripped = strip_rjc(raw).lower()
        terms = []
        if stripped:
            terms.append(stripped)
        if raw and raw not in terms:
            terms.append(raw)
        if terms:
            out[raw] = terms
    return out


def _mk_video_url(video_path: str) -> str:
    return f"/medias/api/mk-video?path={quote(video_path, safe='')}"


def _mk_media_url(media_path: str) -> str:
    return f"/medias/api/mk-media?path={quote(media_path, safe='')}"


def _mk_import_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    meta = _json_loads(row.get("mk_video_metadata_json"), {}) or {}
    if not isinstance(meta, dict):
        meta = {}
    product_name = row.get("mk_product_name") or row.get("product_name") or ""
    video_name = str(row.get("video_name") or "").strip()
    video_path = str(row.get("video_path") or "").strip()
    cover_path = str(row.get("video_image_path") or "").strip()
    if not meta.get("product_code"):
        meta["product_code"] = row.get("product_code") or ""
    if not meta.get("product_name"):
        meta["product_name"] = product_name
    if not meta.get("product_link"):
        meta["product_link"] = row.get("product_url") or row.get("mk_product_link") or ""
    if not meta.get("mk_product_id") and row.get("mk_product_id"):
        meta["mk_product_id"] = row.get("mk_product_id")
    if not meta.get("mk_id") and row.get("mk_product_id"):
        meta["mk_id"] = row.get("mk_product_id")
    if not meta.get("mk_product_name"):
        meta["mk_product_name"] = row.get("mk_product_name") or product_name
    if not meta.get("video_name"):
        meta["video_name"] = video_name
    if not meta.get("filename"):
        meta["filename"] = video_name or Path(video_path).name
    if not meta.get("video_path"):
        meta["video_path"] = video_path
    if not meta.get("mp4_url") and video_path:
        meta["mp4_url"] = _mk_video_url(video_path)
    if not meta.get("cover_path"):
        meta["cover_path"] = cover_path
    if not meta.get("video_image_path"):
        meta["video_image_path"] = cover_path
    if not meta.get("cover_url") and cover_path:
        meta["cover_url"] = _mk_media_url(cover_path)
    if not meta.get("duration_seconds") and row.get("video_duration_seconds") is not None:
        meta["duration_seconds"] = _safe_float(row.get("video_duration_seconds"))
    if not meta.get("spends"):
        meta["spends"] = str(row.get("cumulative_90_spend") or "")
    if not meta.get("ads_count"):
        meta["ads_count"] = row.get("video_ads_count") or 0
    return meta


def _load_mingkong_materials(product_codes: list[str], per_product_limit: int = 6) -> dict[str, list[dict]]:
    search_map = _mk_search_codes(product_codes)
    terms = sorted({term for values in search_map.values() for term in values})
    if not terms:
        return {}
    placeholders = ",".join(["%s"] * len(terms))
    rows = db.query(
        f"""
        SELECT
          s.material_key, s.product_code, s.product_name, s.product_url,
          s.mk_product_id, s.mk_product_name, s.mk_product_link,
          s.video_name, s.video_path, s.video_image_path,
          s.cumulative_90_spend, s.video_ads_count, s.video_author,
          s.video_upload_time, s.video_duration_seconds, s.mk_video_metadata_json,
          COALESCE(t.yesterday_spend_delta, 0) AS yesterday_spend_delta,
          t.display_position AS top100_display_position,
          s.snapshot_at
        FROM mingkong_material_daily_snapshots s
        JOIN mingkong_material_sync_runs r ON r.id = s.run_id AND r.status = 'success'
        JOIN (
          SELECT s2.material_key, MAX(s2.snapshot_at) AS latest_snapshot_at
          FROM mingkong_material_daily_snapshots s2
          JOIN mingkong_material_sync_runs r2 ON r2.id = s2.run_id AND r2.status = 'success'
          WHERE LOWER(s2.product_code) IN ({placeholders})
          GROUP BY s2.material_key
        ) latest
          ON latest.material_key = s.material_key
         AND latest.latest_snapshot_at = s.snapshot_at
        LEFT JOIN mingkong_material_daily_top100 t
          ON t.material_key = s.material_key
         AND t.snapshot_at = s.snapshot_at
        ORDER BY s.product_code, s.cumulative_90_spend DESC, s.video_ads_count DESC
        """,
        tuple(terms),
    )
    reverse: dict[str, str] = {}
    for local_code, values in search_map.items():
        for term in values:
            reverse[term] = local_code
    grouped: dict[str, list[dict]] = defaultdict(list)
    flat_materials: list[dict] = []
    for row in rows:
        local_code = reverse.get(str(row.get("product_code") or "").strip().lower())
        if not local_code or len(grouped[local_code]) >= per_product_limit:
            continue
        video_path = str(row.get("video_path") or "").strip()
        material = {
            "source_type": "mingkong",
            "source_label": "明空",
            "material_key": row.get("material_key") or "",
            "product_code": row.get("product_code") or "",
            "product_name": row.get("product_name") or "",
            "product_url": row.get("product_url") or "",
            "mk_product_id": row.get("mk_product_id"),
            "mk_product_name": row.get("mk_product_name") or "",
            "video_name": row.get("video_name") or "",
            "video_path": video_path,
            "video_image_path": row.get("video_image_path") or "",
            "video_url": _mk_video_url(video_path) if video_path else "",
            "cover_url": _mk_media_url(row.get("video_image_path")) if row.get("video_image_path") else "",
            "cumulative_90_spend": _safe_float(row.get("cumulative_90_spend")),
            "video_ads_count": _safe_int(row.get("video_ads_count")),
            "video_duration_seconds": _safe_float(row.get("video_duration_seconds")),
            "yesterday_spend_delta": _safe_float(row.get("yesterday_spend_delta")),
            "top100_display_position": row.get("top100_display_position"),
            "snapshot_at": _iso(row.get("snapshot_at")),
            "mk_video_metadata": _mk_import_metadata(row),
        }
        grouped[local_code].append(material)
        flat_materials.append(material)
    _refresh_source_material_library_statuses(flat_materials, product_id=0, enrich_mingkong=True)
    return dict(grouped)


def _refresh_source_material_library_statuses(
    materials: list[Any],
    *,
    product_id: int = 0,
    enrich_mingkong: bool = True,
) -> list[dict]:
    normalized: list[dict] = []
    for material in materials or []:
        if isinstance(material, dict):
            normalized.append(material)
        elif isinstance(material, Mapping):
            normalized.append(dict(material))
    if not normalized:
        return []

    mingkong_materials = [
        material
        for material in normalized
        if str(material.get("source_type") or "mingkong").strip() != "local_en_cjh"
    ]
    if enrich_mingkong and mingkong_materials:
        try:
            from appcore.mingkong_materials import _enrich_cached_ad_statuses

            _enrich_cached_ad_statuses(mingkong_materials)
        except Exception as exc:
            log.warning("AI素材军师明空素材入库状态 enrich 失败: %s", exc)

    fallback_product_id = _safe_int(product_id)
    for material in normalized:
        source_type = str(material.get("source_type") or "mingkong").strip()
        product_status = material.get("product_ad_status") if isinstance(material.get("product_ad_status"), dict) else {}
        material_status = material.get("material_ad_status") if isinstance(material.get("material_ad_status"), dict) else {}
        media_product_id = _safe_int(
            material.get("media_product_id")
            or product_status.get("media_product_id")
            or material_status.get("media_product_id")
            or fallback_product_id
        )
        media_item_id = _safe_int(material.get("media_item_id") or material_status.get("media_item_id"))
        material["media_product_id"] = media_product_id
        material["media_item_id"] = media_item_id
        material["has_local_product_in_library"] = bool(
            _safe_bool(material.get("has_local_product_in_library"))
            or _safe_bool(product_status.get("has_local_match"))
            or media_product_id > 0
        )
        material["has_local_material_in_library"] = bool(
            source_type == "local_en_cjh"
            or _safe_bool(material.get("has_local_material_in_library"))
            or _safe_bool(material_status.get("has_local_match"))
            or media_item_id > 0
            or _safe_bool(material.get("is_imported"))
        )
    return normalized


def _material_name_without_suffix(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return Path(text).stem.strip()


def _is_self_made_english_cjh_material(material: Mapping[str, Any]) -> bool:
    if str(material.get("lang") or "").strip().lower() != "en":
        return False
    names = [
        _material_name_without_suffix(str(material.get("display_name") or "")),
        _material_name_without_suffix(str(material.get("filename") or "")),
    ]
    return any(name.endswith("-蔡靖华") for name in names if name)


def _local_en_cjh_source_material(product: Mapping[str, Any], material: Mapping[str, Any]) -> dict:
    object_key = str(material.get("object_key") or "").strip()
    cover_object_key = str(material.get("cover_object_key") or "").strip()
    filename = str(material.get("filename") or material.get("display_name") or "").strip()
    media_item_id = _safe_int(material.get("id"))
    media_product_id = _safe_int(product.get("product_id")) or _safe_int(material.get("product_id"))
    product_code = str(product.get("product_code") or "").strip()
    return {
        "source_type": "local_en_cjh",
        "source_label": "自制EN",
        "material_key": f"local:{media_item_id}" if media_item_id else "",
        "media_product_id": media_product_id,
        "media_item_id": media_item_id,
        "product_code": product_code,
        "product_name": product.get("product_name") or "",
        "video_name": filename or str(material.get("display_name") or "自制EN素材").strip(),
        "video_path": object_key,
        "object_key": object_key,
        "cover_object_key": cover_object_key,
        "video_image_path": cover_object_key,
        "video_url": material.get("video_url") or (_local_video_url(object_key) if object_key else ""),
        "cover_url": material.get("cover_url") or (_local_video_url(cover_object_key) if cover_object_key else ""),
        "cumulative_90_spend": 0.0,
        "video_ads_count": _safe_int(material.get("push_count")),
        "video_author": "蔡靖华",
        "video_upload_time": material.get("created_at") or "",
        "video_duration_seconds": _safe_float(material.get("duration_seconds")),
        "yesterday_spend_delta": 0.0,
        "is_local_material": True,
        "is_imported": True,
        "has_local_product_in_library": media_product_id > 0,
        "has_local_material_in_library": media_item_id > 0,
    }


def _build_source_material_candidates(
    product: Mapping[str, Any],
    local_materials: list[dict],
    mingkong_materials: list[dict],
    *,
    limit: int = 6,
) -> list[dict]:
    local_sources = [
        _local_en_cjh_source_material(product, item)
        for item in local_materials
        if _is_self_made_english_cjh_material(item)
    ]
    local_sources.sort(
        key=lambda item: (
            _safe_int(item.get("video_ads_count")),
            str(item.get("video_upload_time") or ""),
            _safe_int(item.get("media_item_id")),
        ),
        reverse=True,
    )
    local_sources = local_sources[:2]
    mk_limit = max(limit - len(local_sources), 0)
    local_product_id = _safe_int(product.get("product_id"))
    mk_sources = []
    for item in mingkong_materials[:mk_limit]:
        source = dict(item, source_type=item.get("source_type") or "mingkong", source_label=item.get("source_label") or "明空")
        media_product_id = _safe_int(source.get("media_product_id")) or local_product_id
        source["media_product_id"] = media_product_id
        source["has_local_product_in_library"] = bool(_safe_bool(source.get("has_local_product_in_library")) or media_product_id > 0)
        source["has_local_material_in_library"] = bool(
            _safe_bool(source.get("has_local_material_in_library")) or _safe_int(source.get("media_item_id")) > 0
        )
        mk_sources.append(source)
    return (mk_sources + local_sources)[:limit]


def _fallback_product_analysis(product: Mapping[str, Any], countries: list[dict], mk_materials: list[dict]) -> dict:
    active = [c for c in countries if _safe_float(c.get("active_7d_ad_spend_usd")) > 0]
    strong = [c for c in countries if _safe_float(c.get("ad_spend_usd")) >= 30 and _safe_float(c.get("ad_roas")) >= 1.5]
    never = [c for c in countries if c.get("delivery_status") == "never"]
    weak = [c for c in countries if c.get("delivery_status") == "stopped"]
    actionable_never = [c for c in never if not _task_blocks_recommendation(c.get("blocking_task"))]
    actionable_weak = [c for c in weak if not _task_blocks_recommendation(c.get("blocking_task"))]
    actionable_active = [c for c in active if not _task_blocks_recommendation(c.get("blocking_task"))]
    blocked_countries = [c for c in countries if _task_blocks_recommendation(c.get("blocking_task"))]
    spend30 = _safe_float(product.get("spend_30d"))
    orders30 = _safe_float(product.get("orders_30d"))
    true_roas = _safe_float(product.get("true_roas_30d"))

    if spend30 >= 300 and orders30 >= 30 and true_roas >= 1.5:
        priority = "P0"
    elif spend30 >= 100 or orders30 >= 15:
        priority = "P1"
    elif spend30 >= 50 or orders30 >= 8:
        priority = "P2"
    else:
        priority = "P3"

    if strong and actionable_never:
        primary_action = "expand_country"
        judgement = "已有国家跑出量和效率，优先把同素材扩到未验证国家。"
    elif actionable_active and mk_materials:
        primary_action = "same_country_new_material"
        judgement = "当前仍有投放消耗，可在已跑国家补新明空素材继续测。"
    elif actionable_weak and mk_materials:
        primary_action = "weak_country_retest"
        judgement = "部分国家历史投放弱，可用新素材做二次确认。"
    elif blocked_countries:
        primary_action = "hold"
        judgement = "候选国家已有待处理、进行中或已完成任务，先查看任务结果和推送反馈，不重复排程。"
    else:
        primary_action = "investigate"
        judgement = "数据量或素材线索不足，先检查广告命名、订单归因和素材绑定。"

    picked_material = mk_materials[0] if mk_materials else {}
    country_actions = []
    picked_countries = actionable_never[:3] or actionable_weak[:2] or actionable_active[:2] or blocked_countries[:2]
    for country in picked_countries:
        blocking_task = country.get("blocking_task")
        cancelled_task = country.get("cancelled_task")
        action = {
            "country_code": country.get("country_code"),
            "lang": country.get("lang"),
            "action": primary_action,
            "priority": priority,
            "reason": judgement,
            "material_key": picked_material.get("material_key", ""),
            "video_path": picked_material.get("video_path", ""),
        }
        if blocking_task:
            action["existing_task"] = blocking_task
            action["duplicate_suppressed"] = True
        elif cancelled_task:
            action["cancelled_task"] = cancelled_task
        country_actions.append(action)
    material_actions = []
    if picked_material:
        source_label = picked_material.get("source_label") or "素材"
        target_langs = [item.get("lang") for item in country_actions if item.get("lang")]
        material_actions.append({
            "action": "import_or_translate",
            "material_key": picked_material.get("material_key", ""),
            "video_path": picked_material.get("video_path", ""),
            "target_langs": target_langs,
            "reason": f"{source_label}素材表现或相关性靠前，适合作为补素材候选。",
        })
    return {
        "product_id": product.get("product_id"),
        "product_code": product.get("product_code"),
        "overall_judgement": judgement,
        "priority": priority,
        "primary_action": primary_action,
        "country_actions": country_actions,
        "material_actions": material_actions,
        "risks": [] if true_roas >= 1 else ["真实ROAS偏低或利润为负，需要小预算验证。"],
        "next_check": "补素材后观察 24-48 小时消耗、订单和国家 ROAS。",
        "mode": "deterministic_fallback",
    }


def _fill_missing_product_analysis_fields(parsed: Mapping[str, Any], fallback: Mapping[str, Any]) -> dict:
    result = dict(parsed or {})
    filled: list[str] = []
    for key in (
        "product_id",
        "product_code",
        "overall_judgement",
        "priority",
        "primary_action",
        "country_actions",
        "material_actions",
        "risks",
        "next_check",
    ):
        value = result.get(key)
        if value not in (None, "", []):
            continue
        fallback_value = fallback.get(key)
        if fallback_value in (None, "", []):
            continue
        result[key] = fallback_value
        filled.append(key)
    if filled:
        result["fallback_filled_fields"] = filled
    return result


def _run_product_analysis(
    product: dict,
    countries: list[dict],
    local_materials: list[dict],
    mk_materials: list[dict],
    *,
    project_id: int,
    user_id: int | None,
    run_ai: bool,
    provider_code: str = PROVIDER_CODE,
    model_id: str = MODEL_ID,
) -> dict:
    fallback = _fallback_product_analysis(product, countries, mk_materials)
    if not run_ai:
        return fallback
    payload = {
        "identity": {
            "product_id": product.get("product_id"),
            "product_code": product.get("product_code"),
            "product_name": product.get("product_name"),
        },
        "performance_windows": _rank_input(product),
        "country_summary": countries,
        "local_materials": local_materials[:20],
        "mingkong_material_candidates": [
            material for material in mk_materials if material.get("source_type", "mingkong") == "mingkong"
        ],
        "source_material_candidates": mk_materials,
        "task_assignments": [
            task
            for country in countries
            for task in (country.get("tasks") or [])
        ],
        "task_dedup_rule": (
            "pending/in_progress/completed 都视为已安排，不要再建议同产品同国家创建翻译任务；"
            "cancelled 可以重排。"
        ),
        "target_country_tiers": list(TARGET_COUNTRIES),
    }
    try:
        result = llm_client.invoke_generate(
            PRODUCT_ANALYSIS_USE_CASE,
            prompt=_product_prompt(payload),
            user_id=user_id,
            project_id=str(project_id),
            response_schema=PRODUCT_ANALYSIS_RESPONSE_SCHEMA,
            temperature=0.2,
            max_output_tokens=4096,
            provider_override=provider_code,
            model_override=model_id,
            billing_extra={"stage": "product_analysis", "product_id": product.get("product_id")},
            timeout_seconds=180,
        )
        parsed = _llm_json(result)
        if not parsed:
            fallback["ai_error"] = "empty model response"
            return fallback
        parsed = _fill_missing_product_analysis_fields(parsed, fallback)
        parsed.setdefault("mode", "ai")
        parsed.setdefault("prompt_debug", {
            "provider": provider_code,
            "model": model_id,
            "use_case": PRODUCT_ANALYSIS_USE_CASE,
            "mode": "ai",
            "usage_log_id": result.get("usage_log_id"),
            "prompt": _product_prompt(payload),
            "response_text": result.get("text"),
        })
        return parsed
    except Exception as exc:
        log.exception("AI material strategist product analysis failed product_id=%s", product.get("product_id"))
        fallback["ai_error"] = str(exc)
        return fallback


def _decorate_ai_result_with_tasks(ai_result: Mapping[str, Any], countries: list[dict], task_assignments: list[dict]) -> dict:
    result = dict(ai_result or {})
    by_country = {
        _normalize_country_code(country.get("country_code"), lang=country.get("lang")): country
        for country in countries
    }
    decorated_actions: list[dict] = []
    blocked_count = 0
    for raw_action in result.get("country_actions") or []:
        action = dict(raw_action or {})
        code = _country_code_for_action(action)
        country = by_country.get(code) or {}
        blocking_task = country.get("blocking_task")
        cancelled_task = country.get("cancelled_task")
        if blocking_task:
            blocked_count += 1
            original_action = action.get("action") or action.get("decision") or ""
            action["original_action"] = original_action
            action["action"] = "hold"
            action["existing_task"] = blocking_task
            action["duplicate_suppressed"] = True
            reason = str(action.get("reason") or "").strip()
            suffix = (
                f"已有任务 #{blocking_task.get('task_id')}（{blocking_task.get('status_label')}），"
                "不重复排程。"
            )
            action["reason"] = f"{reason}；{suffix}" if reason else suffix
        elif cancelled_task:
            action["cancelled_task"] = cancelled_task
            reason = str(action.get("reason") or "").strip()
            suffix = f"曾取消任务 #{cancelled_task.get('task_id')}，可重新安排。"
            if suffix not in reason:
                action["reason"] = f"{reason}；{suffix}" if reason else suffix
        decorated_actions.append(action)

    if decorated_actions:
        result["country_actions"] = decorated_actions
    if blocked_count and blocked_count == len(decorated_actions):
        result["primary_action"] = "hold"
        next_check = str(result.get("next_check") or "").strip()
        hold_check = "先查看已存在任务的产出、推送状态和广告反馈，再决定是否补新素材。"
        result["next_check"] = f"{next_check} {hold_check}".strip() if hold_check not in next_check else next_check
    result["task_assignments"] = task_assignments[:30]
    result["task_summary"] = _task_assignments_summary(task_assignments)
    return result


def _append_task_action(actions: list[dict], task: Mapping[str, Any], *, seen_task_ids: set[int], label_prefix: str = "任务") -> None:
    task_id = _safe_int(task.get("task_id"))
    if not task_id or task_id in seen_task_ids:
        return
    seen_task_ids.add(task_id)
    actions.append({
        "type": "view_task",
        "label": f"{label_prefix} #{task_id} · {task.get('status_label') or ''}".strip(),
        "url": task.get("task_url") or f"/tasks/detail/{task_id}",
        "task_id": task_id,
        "task": dict(task),
        "country_code": task.get("country_code"),
        "target_lang": task.get("lang"),
    })


def _build_action_items(
    product: Mapping[str, Any],
    ai_result: Mapping[str, Any],
    mk_materials: list[dict],
    countries: list[dict],
) -> list[dict]:
    pid = _safe_int(product.get("product_id"))
    code = str(product.get("product_code") or "").strip()
    country_by_code = {
        _normalize_country_code(country.get("country_code"), lang=country.get("lang")): country
        for country in countries
    }
    seen_task_ids: set[int] = set()
    seen_create_countries: set[str] = set()
    actions: list[dict] = [
        {
            "type": "supplement_workbench",
            "label": "素材工作台",
            "url": f"/medias/product/video_workbench/{pid}",
        },
        {
            "type": "translation_tasks",
            "label": "翻译任务",
            "url": f"/medias/products/{pid}/translation-tasks",
        },
        {
            "type": "product_materials",
            "label": "素材库反馈",
            "url": f"/medias/{quote(code, safe='')}" if code else f"/medias/product/video_workbench/{pid}",
        },
    ]
    for material in mk_materials[:3]:
        source_type = str(material.get("source_type") or "mingkong")
        video_path = str(material.get("video_path") or "").strip()
        video_url = material.get("video_url") or (_mk_video_url(video_path) if source_type == "mingkong" and video_path else "")
        if video_url:
            actions.append({
                "type": "view_source_video",
                "label": "看视频" if source_type == "local_en_cjh" else "看明空视频",
                "url": video_url,
                "material_key": material.get("material_key"),
            })
        if source_type == "local_en_cjh":
            continue
        actions.append({
            "type": "import_mk_video",
            "label": "加入素材库",
            "url": "/mk-import/video",
            "method": "POST",
            "material_key": material.get("material_key"),
            "payload": {
                "mk_video_metadata": _mk_import_metadata_from_material(material),
                "product_owner_id": product.get("user_id"),
            },
        })
    for country in ai_result.get("country_actions") or []:
        code_for_action = _country_code_for_action(country)
        country_summary = country_by_code.get(code_for_action) or {}
        blocking_task = country.get("existing_task") or country_summary.get("blocking_task")
        cancelled_task = country.get("cancelled_task") or country_summary.get("cancelled_task")
        if blocking_task:
            _append_task_action(actions, blocking_task, seen_task_ids=seen_task_ids)
            continue
        if cancelled_task:
            _append_task_action(actions, cancelled_task, seen_task_ids=seen_task_ids, label_prefix="已取消任务")
        lang = str(country.get("lang") or "").strip()
        if not lang and code_for_action:
            lang = _lang_for_country_code(code_for_action)
        if not lang:
            continue
        if lang == "en" or code_for_action == "EN":
            continue
        if code_for_action in seen_create_countries:
            continue
        seen_create_countries.add(code_for_action)
        actions.append({
            "type": "create_translation_task",
            "label": f"创建{code_for_action or lang}翻译任务",
            "url": f"/medias/product/video_workbench/{pid}?target_lang={quote(lang)}",
            "target_lang": lang,
            "country_code": code_for_action or country.get("country_code"),
        })
    return actions


def _summarize_project(products: list[dict], ranking: dict, snapshot: dict) -> dict:
    priority_counts: dict[str, int] = defaultdict(int)
    action_counts: dict[str, int] = defaultdict(int)
    for item in products:
        ai = item.get("ai_result") or {}
        priority_counts[str(ai.get("priority") or "P3")] += 1
        action_counts[str(ai.get("primary_action") or "investigate")] += 1
    return {
        "top_product_count": len(products),
        "priority_counts": dict(priority_counts),
        "action_counts": dict(action_counts),
        "data_window": snapshot.get("window") or {},
        "data_quality": snapshot.get("data_quality") or {},
        "ranking_mode": (ranking.get("ranking_result") or {}).get("mode") or "unknown",
    }


def _selected_product_ids_from_ranking(ranking: Mapping[str, Any]) -> list[int]:
    raw_ids = ranking.get("selected_product_ids")
    if isinstance(raw_ids, list):
        return [_safe_int(pid) for pid in raw_ids if _safe_int(pid)]

    payload = ranking.get("ranking_result") if isinstance(ranking.get("ranking_result"), Mapping) else ranking
    ranked_products: list[Any] = []
    if isinstance(payload, Mapping):
        final_output = payload.get("final_output")
        if isinstance(final_output, Mapping):
            ranked_products = final_output.get("ranked_products") or []
        if not ranked_products:
            ranked_products = payload.get("ranked_products") or []

    ordered = sorted(
        [item for item in ranked_products if isinstance(item, Mapping)],
        key=lambda item: _safe_int(item.get("rank")) or 999,
    )
    return [_safe_int(item.get("product_id")) for item in ordered if _safe_int(item.get("product_id"))]


def _ranking_from_stored(project_row: Mapping[str, Any]) -> dict[str, Any] | None:
    stored = _json_loads(project_row.get("ranking_result_json"), {}) or {}
    if not isinstance(stored, Mapping) or not stored:
        return None
    if "selected_product_ids" in stored:
        ranking = dict(stored)
    else:
        prompt_debug = _json_loads(project_row.get("ranking_prompt_json"), {}) or {}
        ranking = {
            "selected_product_ids": _selected_product_ids_from_ranking(stored),
            "ranking_result": dict(stored),
            "prompt_debug": prompt_debug if isinstance(prompt_debug, Mapping) else {},
        }
    selected = _selected_product_ids_from_ranking(ranking)
    if not selected:
        return None
    ranking["selected_product_ids"] = selected[:_PROJECT_TOP_N]
    return ranking


def _load_project_row(project_id: int) -> dict | None:
    return db.query_one(
        """
        SELECT id, project_name, status, user_id, provider_code, model_id,
               data_window_json, data_snapshot_json, ranking_prompt_json,
               ranking_result_json, summary_json, progress_json, share_token,
               share_enabled_at, error_message, started_at, finished_at, created_at, updated_at
        FROM ai_material_strategist_projects
        WHERE id = %s
        """,
        (project_id,),
    )


def _mark_other_running_projects_interrupted(
    project_id: int,
    *,
    reason: str = "replaced_by_project_resume",
    message: str | None = None,
    error_message: str | None = None,
) -> None:
    rows = db.query(
        """
        SELECT id, progress_json
        FROM ai_material_strategist_projects
        WHERE status = 'running' AND id <> %s
        """,
        (project_id,),
    )
    for row in rows:
        other_id = _safe_int(row.get("id"))
        if not other_id:
            continue
        progress = _normalize_progress(
            _json_loads(row.get("progress_json"), {}) or {},
            message="历史运行线程已中断。",
        )
        notice = message or f"历史运行线程已中断，当前恢复执行项目 #{project_id}；本项目已标记中断。"
        progress = _interrupted_progress(
            progress,
            message=notice,
            reason=reason,
        )
        db.execute(
            """
            UPDATE ai_material_strategist_projects
            SET status = 'interrupted',
                error_message = %s,
                progress_json = %s,
                finished_at = NOW(),
                updated_at = NOW()
            WHERE id = %s AND status = 'running'
            """,
            (
                error_message or f"{notice} 请从步骤卡片手动继续。",
                _json_dumps(progress),
                other_id,
            ),
        )


def _prepare_project_for_run(project_id: int) -> dict:
    row = _load_project_row(project_id)
    if not row:
        raise ValueError(f"AI素材军师项目不存在：{project_id}")
    if row.get("status") == "success":
        return row
    progress = _normalize_progress(
        _json_loads(row.get("progress_json"), {}) or {},
        message="从断点恢复执行。",
    )
    progress = _mark_recovery_state(progress, "running", timestamp_key="resumed_at")
    progress["runner_state"] = "running"
    db.execute(
        """
        UPDATE ai_material_strategist_projects
        SET status = 'running',
            error_message = NULL,
            finished_at = NULL,
            progress_json = %s,
            updated_at = NOW()
        WHERE id = %s
        """,
        (_json_dumps(progress), project_id),
    )
    _mark_other_running_projects_interrupted(project_id)
    return _load_project_row(project_id) or row


def _snapshot_from_stored(project_row: Mapping[str, Any]) -> dict[str, Any] | None:
    snapshot = _json_loads(project_row.get("data_snapshot_json"), {}) or {}
    if isinstance(snapshot, Mapping) and isinstance(snapshot.get("products"), list):
        return dict(snapshot)
    return None


def _select_products(candidates: list[dict], ranking: Mapping[str, Any]) -> list[dict]:
    candidate_by_id = {_safe_int(item.get("product_id")): item for item in candidates}
    selected = [
        candidate_by_id[pid]
        for pid in _selected_product_ids_from_ranking(ranking)
        if pid in candidate_by_id
    ]
    if len(selected) < _PROJECT_TOP_N:
        seen = {_safe_int(item.get("product_id")) for item in selected}
        selected.extend(item for item in candidates if _safe_int(item.get("product_id")) not in seen)
        selected = selected[:_PROJECT_TOP_N]
    return selected


def _load_existing_product_results(project_id: int) -> dict[int, dict]:
    rows = _load_project_product_rows(project_id)
    return {
        _safe_int(row.get("product_id")): _serialize_product_result(row)
        for row in rows
        if _safe_int(row.get("product_id"))
    }


def _load_project_product_rows(project_id: int) -> list[dict]:
    return db.query(
        """
        SELECT id, project_id, rank_no, product_id, product_code, product_name, score,
               metrics_json, country_summary_json, local_materials_json,
               mingkong_materials_json, ai_result_json, action_items_json,
               created_at, updated_at
        FROM ai_material_strategist_product_results
        WHERE project_id = %s
        ORDER BY rank_no ASC, id ASC
        """,
        (project_id,),
    )


def _runtime_result_from_stored(row: Mapping[str, Any]) -> dict:
    return {
        "rank_no": _safe_int(row.get("rank_no")),
        "product": row.get("metrics") or {},
        "country_summary": row.get("country_summary") or [],
        "local_materials": row.get("local_materials") or [],
        "mingkong_materials": row.get("mingkong_materials") or [],
        "ai_result": row.get("ai_result") or {},
        "action_items": row.get("action_items") or [],
    }


def _upsert_product_result(project_id: int, item: Mapping[str, Any]) -> None:
    product = item["product"]
    db.execute(
        """
        INSERT INTO ai_material_strategist_product_results
          (project_id, rank_no, product_id, product_code, product_name, score,
           metrics_json, country_summary_json, local_materials_json,
           mingkong_materials_json, ai_result_json, action_items_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          rank_no = VALUES(rank_no),
          product_code = VALUES(product_code),
          product_name = VALUES(product_name),
          score = VALUES(score),
          metrics_json = VALUES(metrics_json),
          country_summary_json = VALUES(country_summary_json),
          local_materials_json = VALUES(local_materials_json),
          mingkong_materials_json = VALUES(mingkong_materials_json),
          ai_result_json = VALUES(ai_result_json),
          action_items_json = VALUES(action_items_json),
          updated_at = NOW()
        """,
        (
            project_id,
            item["rank_no"],
            product.get("product_id"),
            product.get("product_code") or "",
            product.get("product_name") or "",
            _safe_float(product.get("score")),
            _json_dumps(product),
            _json_dumps(item["country_summary"]),
            _json_dumps(item["local_materials"]),
            _json_dumps(item["mingkong_materials"]),
            _json_dumps(item["ai_result"]),
            _json_dumps(item["action_items"]),
        ),
    )


def _delete_unselected_product_results(project_id: int, selected_product_ids: list[int]) -> None:
    selected_product_ids = [_safe_int(pid) for pid in selected_product_ids if _safe_int(pid)]
    if not selected_product_ids:
        return
    placeholders = ",".join(["%s"] * len(selected_product_ids))
    db.execute(
        f"""
        DELETE FROM ai_material_strategist_product_results
        WHERE project_id = %s
          AND product_id NOT IN ({placeholders})
        """,
        (project_id, *selected_product_ids),
    )


def get_running_project() -> dict | None:
    row = db.query_one(
        """
        SELECT id, project_name, status, user_id, provider_code, model_id,
               summary_json, progress_json, share_token, share_enabled_at,
               error_message, started_at, finished_at, created_at, updated_at
        FROM ai_material_strategist_projects
        WHERE status = 'running'
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """
    )
    return _serialize_project_row(row, include_products=False) if row else None


def mark_startup_interrupted_project_for_recovery() -> dict | None:
    """Mark the newest running project as interrupted by a service restart.

    Docs anchor:
    docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md#断点续跑与恢复
    """
    lock_conn = _with_project_lock(timeout_seconds=0)
    if lock_conn is None:
        log.info("AI material strategist startup recovery skipped; runner lock is busy")
        return None
    try:
        row = db.query_one(
            """
            SELECT id, project_name, status, user_id, provider_code, model_id,
                   summary_json, progress_json, share_token, share_enabled_at,
                   error_message, started_at, finished_at, created_at, updated_at
            FROM ai_material_strategist_projects
            WHERE status = 'running'
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """
        )
        if not row:
            return None

        project_id = _safe_int(row.get("id"))
        if not project_id:
            return None

        detected_at = _now_iso()
        previous_updated_at = _iso(row.get("updated_at"))
        progress = _normalize_progress(
            _json_loads(row.get("progress_json"), {}) or {},
            message="检测到服务重启，准备从断点自动恢复。",
        )
        recovery = progress.get("recovery") if isinstance(progress.get("recovery"), Mapping) else {}
        recovery = dict(recovery or {})
        recovery.update({
            "reason": "service_restart",
            "status": "scheduled",
            "detected_at": detected_at,
            "scheduled_at": detected_at,
            "previous_updated_at": previous_updated_at,
            "project_id": project_id,
            "auto_resume": True,
        })
        progress["recovery"] = recovery
        progress["runner_state"] = "resume_scheduled"

        current_step = str(progress.get("current_step") or "snapshot")
        progress = _progress_update(
            progress,
            step_key=current_step if current_step != "queued" else "snapshot",
            step_status="running",
            percent=_safe_float(progress.get("percent")),
            message="检测到服务重启导致运行线程中断，已加入自动恢复队列。",
            project_status="running",
            level="warning",
        )
        progress["runner_state"] = "resume_scheduled"
        progress["recovery"] = recovery
        affected = db.execute(
            """
            UPDATE ai_material_strategist_projects
            SET progress_json = %s,
                error_message = NULL,
                finished_at = NULL,
                updated_at = NOW()
            WHERE id = %s
              AND status = 'running'
            """,
            (_json_dumps(progress), project_id),
        )
        if affected <= 0:
            return None
        try:
            _mark_other_running_projects_interrupted(
                project_id,
                reason="replaced_by_startup_recovery",
                message=f"服务启动只自动恢复最新项目 #{project_id}；本项目已标记中断。",
                error_message=f"服务启动只自动恢复最新项目 #{project_id}；请从项目详情手动继续未完成或从指定步骤继续。",
            )
        except Exception:
            log.warning(
                "AI material strategist startup recovery failed to mark older running projects: project_id=%s",
                project_id,
                exc_info=True,
            )
        updated_row = dict(row)
        updated_row["progress_json"] = _json_dumps(progress)
        updated_row["error_message"] = ""
        updated_row["finished_at"] = None
        return _serialize_project_row(updated_row, include_products=False)
    finally:
        _release_project_lock(lock_conn)


def mark_project_interrupted(
    project_id: int,
    *,
    reason: str = "startup_resume_failed",
    message: str = "任务中断，等待人工从步骤卡片继续。",
) -> dict | None:
    """Stop a stale running project at an explicit interrupted status."""
    project_id = _safe_int(project_id)
    if not project_id:
        return None
    row = _load_project_row(project_id)
    if not row:
        return None
    progress = _normalize_progress(
        _json_loads(row.get("progress_json"), {}) or {},
        message=message,
    )
    progress = _interrupted_progress(progress, message=message, reason=reason)
    db.execute(
        """
        UPDATE ai_material_strategist_projects
        SET status = 'interrupted',
            error_message = %s,
            progress_json = %s,
            finished_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
          AND status = 'running'
        """,
        (message, _json_dumps(progress), project_id),
    )
    return get_project(project_id)


def resume_project_from_step(project_id: int, step_key: str, *, user_id: int | None = None) -> dict:
    """Reset persisted checkpoints so the project can continue from a step.

    Docs anchor:
    docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md#断点续跑与恢复
    """
    project_id = _safe_int(project_id)
    step_key = str(step_key or "").strip()
    if step_key not in _PROGRESS_STEP_KEYS:
        raise ValueError(f"unsupported AI素材军师恢复步骤：{step_key}")

    lock_conn = _with_project_lock(timeout_seconds=0)
    if lock_conn is None:
        raise ProjectAlreadyRunningError(get_project(project_id))
    try:
        row = _load_project_row(project_id)
        if not row:
            raise ValueError(f"AI素材军师项目不存在：{project_id}")

        progress = _normalize_progress(
            _json_loads(row.get("progress_json"), {}) or {},
            message="准备从指定步骤继续。",
        )
        progress = _reset_progress_from_step(
            progress,
            step_key,
            message=f"已手动选择从「{_step_label(step_key)}」起点继续执行。",
        )
        recovery = dict(progress.get("recovery") or {})
        if user_id is not None:
            recovery["user_id"] = user_id
        progress["recovery"] = recovery

        set_parts = [
            "status = 'running'",
            "summary_json = NULL",
            "progress_json = %s",
            "error_message = NULL",
            "finished_at = NULL",
            "updated_at = NOW()",
        ]
        params: list[Any] = [_json_dumps(progress)]
        if step_key in _CLEAR_SNAPSHOT_FROM_STEPS:
            set_parts.extend(["data_window_json = NULL", "data_snapshot_json = NULL"])
        if step_key in _CLEAR_RANKING_FROM_STEPS:
            set_parts.extend(["ranking_prompt_json = NULL", "ranking_result_json = NULL"])

        db.execute(
            f"""
            UPDATE ai_material_strategist_projects
            SET {', '.join(set_parts)}
            WHERE id = %s
            """,
            (*params, project_id),
        )
        if step_key in _CLEAR_PRODUCT_RESULTS_FROM_STEPS:
            db.execute(
                "DELETE FROM ai_material_strategist_product_results WHERE project_id = %s",
                (project_id,),
            )
        _mark_other_running_projects_interrupted(project_id)
        return get_project(project_id) or {"id": project_id, "status": "running"}
    finally:
        _release_project_lock(lock_conn)


def resume_project_checkpoint(project_id: int, *, user_id: int | None = None) -> dict:
    """Resume a project from persisted checkpoints without clearing product rows.

    Docs anchor:
    docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md#2026-06-11-断电续传收口
    """
    project_id = _safe_int(project_id)
    if not project_id:
        raise ValueError("AI素材军师项目不存在：0")

    lock_conn = _with_project_lock(timeout_seconds=0)
    if lock_conn is None:
        raise ProjectAlreadyRunningError(get_project(project_id))
    try:
        row = _load_project_row(project_id)
        if not row:
            raise ValueError(f"AI素材军师项目不存在：{project_id}")
        if str(row.get("status") or "").lower() == "success":
            return get_project(project_id) or {"id": project_id, "status": "success"}

        progress = _normalize_progress(
            _json_loads(row.get("progress_json"), {}) or {},
            message="准备继续未完成项目。",
        )
        now = _now_iso()
        recovery = progress.get("recovery") if isinstance(progress.get("recovery"), Mapping) else {}
        recovery = dict(recovery or {})
        recovery.update({
            "reason": "manual_checkpoint_resume",
            "status": "scheduled",
            "scheduled_at": now,
            "project_id": project_id,
            "auto_resume": False,
        })
        if user_id is not None:
            recovery["user_id"] = user_id

        logs = list(progress.get("logs") or [])
        logs.append({
            "time": now,
            "level": "warning",
            "message": "已选择继续未完成项目，保留已完成产品结果并重新排队。",
        })
        progress.update({
            "status": "running",
            "runner_state": "checkpoint_resume_scheduled",
            "runner_heartbeat_at": None,
            "recovery": recovery,
            "message": "已选择继续未完成项目，保留已完成产品结果并重新排队。",
            "logs": logs[-_PROGRESS_LOG_LIMIT:],
            "updated_at": now,
        })

        db.execute(
            """
            UPDATE ai_material_strategist_projects
            SET status = 'running',
                summary_json = NULL,
                progress_json = %s,
                error_message = NULL,
                finished_at = NULL,
                updated_at = NOW()
            WHERE id = %s
            """,
            (_json_dumps(progress), project_id),
        )
        _mark_other_running_projects_interrupted(
            project_id,
            reason="replaced_by_checkpoint_resume",
            message=f"当前继续未完成项目 #{project_id}；本项目已标记中断。",
            error_message=f"当前继续未完成项目 #{project_id}；请从项目详情手动继续未完成或从指定步骤继续。",
        )
        return get_project(project_id) or {"id": project_id, "status": "running"}
    finally:
        _release_project_lock(lock_conn)


def create_project_record(user_id: int | None, project_name: str | None = None) -> dict:
    name = (project_name or "").strip() or f"AI素材军师 {datetime.now():%Y-%m-%d %H:%M}"
    initial_progress = _initial_progress()
    conn = db.get_conn()
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 3) AS lock_ok", (_PROJECT_LOCK_NAME,))
            lock_row = cur.fetchone() or {}
            locked = _safe_int(lock_row.get("lock_ok")) == 1
            if not locked:
                raise ProjectAlreadyRunningError(get_running_project())
            cur.execute(
                """
                SELECT id, project_name, status, user_id, provider_code, model_id,
                       summary_json, progress_json, share_token, share_enabled_at,
                       error_message, started_at, finished_at, created_at, updated_at
                FROM ai_material_strategist_projects
                WHERE status = 'running'
                ORDER BY started_at DESC, id DESC
                LIMIT 1
                """
            )
            running = cur.fetchone()
            if running:
                raise ProjectAlreadyRunningError(_serialize_project_row(running, include_products=False))
            cur.execute(
                """
                INSERT INTO ai_material_strategist_projects
                  (project_name, status, user_id, provider_code, model_id, progress_json, started_at)
                VALUES (%s, 'running', %s, %s, %s, %s, NOW())
                """,
                (name, user_id, PROVIDER_CODE, MODEL_ID, _json_dumps(initial_progress)),
            )
            project_id = cur.lastrowid
    finally:
        try:
            if locked:
                with conn.cursor() as cur:
                    cur.execute("SELECT RELEASE_LOCK(%s)", (_PROJECT_LOCK_NAME,))
        finally:
            conn.close()
    return get_project(project_id) or {"id": project_id, "project_name": name, "status": "running"}


def run_project(project_id: int, *, user_id: int | None = None, run_ai: bool = True) -> dict:
    lock_conn = _with_project_lock(timeout_seconds=5)
    if lock_conn is None:
        log.warning("AI material strategist runner lock busy project_id=%s", project_id)
        return get_project(project_id) or {"id": project_id, "status": "running"}
    try:
        return _run_project_locked(project_id, user_id=user_id, run_ai=run_ai)
    finally:
        _release_project_lock(lock_conn)


def _run_project_locked(project_id: int, *, user_id: int | None = None, run_ai: bool = True) -> dict:
    project_row = _prepare_project_for_run(project_id)
    if project_row.get("status") == "success":
        return get_project(project_id) or {"id": project_id, "status": "success"}
    provider_code = _project_provider_code(project_row)
    model_id = _project_model_id(project_row)

    progress = _normalize_progress(
        _json_loads(project_row.get("progress_json"), {}) or {},
        message="从断点恢复执行。",
    )

    def checkpoint(
        step_key: str,
        step_status: str,
        percent: float,
        message: str,
        *,
        product_progress: Mapping[str, Any] | None = None,
        project_status: str = "running",
        level: str = "info",
    ) -> None:
        nonlocal progress
        if project_status == "running" and step_status != "failed":
            percent = max(_safe_float(progress.get("percent")), _safe_float(percent))
        progress = _progress_update(
            progress,
            step_key=step_key,
            step_status=step_status,
            percent=percent,
            message=message,
            product_progress=product_progress,
            project_status=project_status,
            level=level,
        )
        _save_progress(project_id, progress)

    try:
        checkpoint(
            str(progress.get("current_step") or "snapshot") if progress.get("current_step") != "queued" else "snapshot",
            "running",
            _safe_float(progress.get("percent")),
            "从断点恢复执行项目，正在检查已完成阶段。",
        )

        snapshot = _snapshot_from_stored(project_row)
        if snapshot is not None:
            product_count = len(snapshot.get("products") or [])
            checkpoint(
                "snapshot",
                "done",
                max(_safe_float(progress.get("percent")), 14),
                f"复用已保存数据快照，包含 {product_count} 个产品。",
            )
        else:
            checkpoint("snapshot", "running", 4, "读取数据窗口、产品、广告、订单和数据新鲜度。")
            snapshot = build_data_snapshot()
            _save_project_snapshot(project_id, snapshot)
            product_count = len(snapshot.get("products") or [])
            checkpoint(
                "snapshot",
                "done",
                14,
                f"数据快照完成，读取到 {product_count} 个产品。",
            )
        product_count = len(snapshot.get("products") or [])
        checkpoint("candidate_score", "running", 18, "按量级、ROAS、订单和广告数做规则预筛。")
        candidates = score_product_rows(snapshot["products"], limit=_MAX_AI_CANDIDATES)
        checkpoint(
            "candidate_score",
            "done",
            28,
            f"规则预筛完成，{len(candidates)} 个候选进入 AI 复评。",
        )

        ranking = _ranking_from_stored(project_row)
        if ranking is not None:
            checkpoint(
                "ai_ranking",
                "done",
                max(_safe_float(progress.get("percent")), 42),
                f"复用已保存 Top {_PROJECT_TOP_N} 排名结果，不重复调用排名模型。",
            )
        else:
            checkpoint("ai_ranking", "running", 32, f"调用 {provider_code} {model_id} 分批复评 Top {_PROJECT_TOP_N}。")
            ranking = _run_ai_ranking(
                candidates,
                project_id=project_id,
                user_id=user_id,
                run_ai=run_ai,
                provider_code=provider_code,
                model_id=model_id,
            )
            _save_project_ranking(project_id, ranking)
        selected = _select_products(candidates, ranking)
        checkpoint("ai_ranking", "done", 42, f"Top 产品选择完成，进入逐产品分析 {len(selected)} 个。")

        product_ids = [_safe_int(item.get("product_id")) for item in selected]
        checkpoint("material_context", "running", 46, "读取国家反馈、本地素材、明空素材和任务中心排程。")
        countries_by_product = _load_country_summaries(product_ids)
        local_by_product = _load_local_materials(product_ids)
        tasks_by_product = _load_task_assignments(product_ids)
        mk_by_code = _load_mingkong_materials([str(item.get("product_code") or "") for item in selected])
        checkpoint("material_context", "done", 54, "素材上下文读取完成，开始逐产品 AI 分析。")

        results: list[dict] = []
        existing_results = _load_existing_product_results(project_id)
        total_products = len(selected)
        last_product_llm_finished_at = 0.0
        for rank_no, product in enumerate(selected, start=1):
            code_key = str(product.get("product_code") or "").strip().lower()
            product_id = _safe_int(product.get("product_id"))
            product_progress = {
                "current_index": rank_no,
                "total": total_products,
                "current_product_id": product_id,
                "current_product_code": product.get("product_code") or "",
                "current_product_name": product.get("product_name") or "",
            }
            start_percent = 54 + ((rank_no - 1) / max(total_products, 1)) * 30
            existing = existing_results.get(product_id)
            if existing and existing.get("ai_result"):
                stored = _runtime_result_from_stored(existing)
                stored["rank_no"] = rank_no
                stored_product = dict(stored.get("product") or product)
                stored_product.setdefault("product_id", product_id)
                stored_product.setdefault("product_code", product.get("product_code") or "")
                stored_product.setdefault("product_name", product.get("product_name") or "")
                stored["product"] = stored_product
                results.append(stored)
                checkpoint(
                    "product_analysis",
                    "running" if rank_no < total_products else "done",
                    54 + (rank_no / max(total_products, 1)) * 30,
                    f"跳过第 {rank_no}/{total_products} 个产品，已存在分析结果。",
                    product_progress=product_progress,
                )
                continue

            checkpoint(
                "product_analysis",
                "running",
                start_percent,
                f"分析第 {rank_no}/{total_products} 个产品：{product.get('product_code') or product.get('product_name') or product_id}",
                product_progress=product_progress,
            )
            task_assignments = tasks_by_product.get(product_id) or []
            countries = _enrich_country_summaries_with_tasks(
                countries_by_product.get(product_id) or [],
                task_assignments,
            )
            local_materials = local_by_product.get(product_id) or []
            mk_materials = _build_source_material_candidates(
                product,
                local_materials,
                mk_by_code.get(code_key) or [],
            )
            _pace_llm_call(last_product_llm_finished_at, run_ai=run_ai)
            ai_result = _run_product_analysis(
                product,
                countries,
                local_materials,
                mk_materials,
                project_id=project_id,
                user_id=user_id,
                run_ai=run_ai,
                provider_code=provider_code,
                model_id=model_id,
            )
            if run_ai:
                last_product_llm_finished_at = time.monotonic()
            ai_result = _decorate_ai_result_with_tasks(ai_result, countries, task_assignments)
            action_items = _build_action_items(product, ai_result, mk_materials, countries)
            results.append({
                "rank_no": rank_no,
                "product": product,
                "country_summary": countries,
                "local_materials": local_materials,
                "mingkong_materials": mk_materials,
                "ai_result": ai_result,
                "action_items": action_items,
            })
            _upsert_product_result(project_id, results[-1])
            done_percent = 54 + (rank_no / max(total_products, 1)) * 30
            checkpoint(
                "product_analysis",
                "running" if rank_no < total_products else "done",
                done_percent,
                f"已完成第 {rank_no}/{total_products} 个产品分析。",
                product_progress=product_progress,
            )

        checkpoint("persist", "running", 88, f"整理已落库结果，清理不在本轮 Top {_PROJECT_TOP_N} 内的旧结果。")
        _delete_unselected_product_results(project_id, product_ids)
        results.sort(key=lambda item: _safe_int(item.get("rank_no")))

        checkpoint("persist", "done", 94, "产品结果保存完成，开始汇总结论。")
        checkpoint("summary", "running", 96, "汇总 P0/P1、主动作和数据质量。")
        summary = _summarize_project(results, ranking, snapshot)
        progress = _progress_update(
            progress,
            step_key="summary",
            step_status="done",
            percent=100,
            message="项目运行完成。",
            project_status="success",
        )
        progress = _mark_recovery_state(progress, "success", timestamp_key="finished_at")
        db.execute(
            """
            UPDATE ai_material_strategist_projects
            SET status = 'success',
                data_window_json = %s,
                data_snapshot_json = %s,
                ranking_prompt_json = %s,
                ranking_result_json = %s,
                summary_json = %s,
                progress_json = %s,
                error_message = NULL,
                finished_at = NOW()
            WHERE id = %s
            """,
            (
                _json_dumps(snapshot.get("window") or {}),
                _json_dumps(snapshot),
                _json_dumps(ranking.get("prompt_debug") or {}),
                _json_dumps(ranking),
                _json_dumps(summary),
                _json_dumps(progress),
                project_id,
            ),
        )
    except Exception as exc:
        log.exception("AI material strategist project failed project_id=%s", project_id)
        failed_step = str(progress.get("current_step") or "snapshot")
        progress = _progress_update(
            progress,
            step_key=failed_step if failed_step != "queued" else "snapshot",
            step_status="failed",
            percent=_safe_float(progress.get("percent")),
            message=f"项目运行失败：{exc}",
            project_status="failed",
            level="error",
        )
        progress = _mark_recovery_state(progress, "failed", timestamp_key="finished_at")
        db.execute(
            """
            UPDATE ai_material_strategist_projects
            SET status = 'failed', error_message = %s, progress_json = %s, finished_at = NOW()
            WHERE id = %s
            """,
            (str(exc), _json_dumps(progress), project_id),
        )
    return get_project(project_id) or {"id": project_id, "status": "failed"}


def create_and_run_project(
    user_id: int | None,
    *,
    project_name: str | None = None,
    run_ai: bool = True,
) -> dict:
    project = create_project_record(user_id, project_name)
    return run_project(_safe_int(project.get("id")), user_id=user_id, run_ai=run_ai)


def list_projects(limit: int = 30) -> list[dict]:
    rows = db.query(
        """
        SELECT id, project_name, status, user_id, provider_code, model_id,
               summary_json, progress_json, share_token, share_enabled_at,
               error_message, started_at, finished_at, created_at, updated_at
        FROM ai_material_strategist_projects
        ORDER BY id DESC
        LIMIT %s
        """,
        (max(1, min(limit, 100)),),
    )
    return [_serialize_project_row(row, include_products=False) for row in rows]


def get_project(project_id: int) -> dict | None:
    row = db.query_one(
        """
        SELECT id, project_name, status, user_id, provider_code, model_id,
               data_window_json, data_snapshot_json, ranking_prompt_json,
               ranking_result_json, summary_json, progress_json, share_token,
               share_enabled_at, error_message,
               started_at, finished_at, created_at, updated_at
        FROM ai_material_strategist_projects
        WHERE id = %s
        """,
        (project_id,),
    )
    if not row:
        return None
    project = _serialize_project_row(row, include_products=True)
    product_rows = _load_project_product_rows(project_id)
    project["products"] = [_serialize_product_result(row) for row in product_rows]
    return project


def delete_project(project_id: int) -> dict[str, Any]:
    project_id = _safe_int(project_id)
    if not project_id:
        return {"deleted": False, "reason": "not_found"}
    row = db.query_one(
        """
        SELECT id, project_name, status, user_id, provider_code, model_id,
               summary_json, progress_json, share_token, share_enabled_at,
               error_message, started_at, finished_at, created_at, updated_at
        FROM ai_material_strategist_projects
        WHERE id = %s
        """,
        (project_id,),
    )
    if not row:
        return {"deleted": False, "reason": "not_found"}
    project = _serialize_project_row(row, include_products=False)
    if str(row.get("status") or "").lower() == "running":
        return {"deleted": False, "reason": "running", "project": project}
    db.execute(
        "DELETE FROM ai_material_strategist_projects WHERE id = %s",
        (project_id,),
    )
    return {"deleted": True, "project_id": project_id, "project": project}


def ensure_project_share(project_id: int) -> dict | None:
    project_id = _safe_int(project_id)
    if not project_id:
        return None
    row = db.query_one(
        """
        SELECT id, share_token, share_enabled_at
        FROM ai_material_strategist_projects
        WHERE id = %s
        """,
        (project_id,),
    )
    if not row:
        return None
    if row.get("share_token"):
        return _serialize_share_row(row)

    for _ in range(5):
        token = secrets.token_urlsafe(_SHARE_TOKEN_BYTES)
        try:
            db.execute(
                """
                UPDATE ai_material_strategist_projects
                SET share_token = %s,
                    share_enabled_at = COALESCE(share_enabled_at, NOW()),
                    updated_at = NOW()
                WHERE id = %s AND (share_token IS NULL OR share_token = '')
                """,
                (token, project_id),
            )
        except Exception as exc:
            if _is_duplicate_key_error(exc):
                continue
            raise
        row = db.query_one(
            """
            SELECT id, share_token, share_enabled_at
            FROM ai_material_strategist_projects
            WHERE id = %s
            """,
            (project_id,),
        )
        if row and row.get("share_token"):
            return _serialize_share_row(row)
    raise RuntimeError("生成 AI素材军师分享链接失败，请重试")


def get_project_by_share_token(share_token: str) -> dict | None:
    token = str(share_token or "").strip()
    if not token:
        return None
    row = db.query_one(
        """
        SELECT id, project_name, status, user_id, provider_code, model_id,
               data_window_json, data_snapshot_json, ranking_prompt_json,
               ranking_result_json, summary_json, progress_json, share_token,
               share_enabled_at, error_message,
               started_at, finished_at, created_at, updated_at
        FROM ai_material_strategist_projects
        WHERE share_token = %s
        """,
        (token,),
    )
    if not row:
        return None
    project = _serialize_project_row(row, include_products=True)
    product_rows = _load_project_product_rows(_safe_int(row.get("id")))
    project["products"] = [_serialize_product_result(item) for item in product_rows]
    return project


def _checkpoint_resume_state(row: Mapping[str, Any], progress: Mapping[str, Any]) -> tuple[bool, str]:
    status = str(row.get("status") or "running").lower()
    if status == "success":
        return False, ""
    if status in {"failed", "interrupted"}:
        return True, "terminal_status"
    if status != "running":
        return False, ""

    runner_state = str(progress.get("runner_state") or "").strip().lower()
    heartbeat_at = progress.get("runner_heartbeat_at")
    progress_updated_at = progress.get("updated_at") or row.get("updated_at")
    if heartbeat_at and _is_stale_time(heartbeat_at):
        return True, "stale_heartbeat"
    if runner_state in _SCHEDULED_RUNNER_STATES and _is_stale_time(progress_updated_at):
        return True, "stale_scheduled"
    return False, ""


def _serialize_project_row(row: Mapping[str, Any], *, include_products: bool) -> dict:
    status = row.get("status") or "running"
    progress = _json_loads(row.get("progress_json"), {}) or {}
    if not progress and status == "running":
        progress = _initial_progress(message="项目正在运行，等待后台写入详细进度。")
    can_resume_checkpoint, resume_checkpoint_reason = _checkpoint_resume_state(row, progress)
    out = {
        "id": _safe_int(row.get("id")),
        "project_name": row.get("project_name") or "",
        "status": status,
        "user_id": row.get("user_id"),
        "provider_code": row.get("provider_code") or PROVIDER_CODE,
        "model_id": row.get("model_id") or MODEL_ID,
        "has_share": bool(row.get("share_token")),
        "share_enabled_at": _iso(row.get("share_enabled_at")),
        "summary": _json_loads(row.get("summary_json"), {}) or {},
        "progress": progress,
        "can_resume_checkpoint": can_resume_checkpoint,
        "resume_checkpoint_reason": resume_checkpoint_reason,
        "error_message": row.get("error_message") or "",
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }
    if include_products:
        out.update({
            "data_window": _json_loads(row.get("data_window_json"), {}) or {},
            "data_snapshot": _json_loads(row.get("data_snapshot_json"), {}) or {},
            "ranking_prompt": _json_loads(row.get("ranking_prompt_json"), {}) or {},
            "ranking_result": _json_loads(row.get("ranking_result_json"), {}) or {},
        })
    return out


def _serialize_share_row(row: Mapping[str, Any]) -> dict:
    return {
        "project_id": _safe_int(row.get("id")),
        "share_token": row.get("share_token") or "",
        "share_enabled_at": _iso(row.get("share_enabled_at")),
    }


def _is_duplicate_key_error(exc: Exception) -> bool:
    args = getattr(exc, "args", ())
    return bool(args and args[0] == 1062)


def _mk_import_metadata_from_material(material: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(material, Mapping):
        return {}
    row = dict(material)
    if "mk_video_metadata_json" not in row and isinstance(row.get("mk_video_metadata"), Mapping):
        row["mk_video_metadata_json"] = row.get("mk_video_metadata")
    return _mk_import_metadata(row)


def _upgrade_import_action_payload(action: dict, material: Mapping[str, Any] | None) -> dict:
    payload = dict(action.get("payload") or {})
    existing_meta = payload.get("mk_video_metadata") if isinstance(payload.get("mk_video_metadata"), Mapping) else {}
    merged_meta = _mk_import_metadata_from_material(material)
    for key, value in dict(existing_meta or {}).items():
        if value not in (None, ""):
            merged_meta[key] = value
    if merged_meta:
        payload["mk_video_metadata"] = merged_meta
        action["payload"] = payload
    return action


def _serialize_product_result(row: Mapping[str, Any]) -> dict:
    product_id = _safe_int(row.get("product_id"))
    mingkong_materials = _json_loads(row.get("mingkong_materials_json"), []) or []
    if isinstance(mingkong_materials, list):
        mingkong_materials = _refresh_source_material_library_statuses(
            mingkong_materials,
            product_id=product_id,
            enrich_mingkong=True,
        )
    else:
        mingkong_materials = []
    material_by_key = {
        str(material.get("material_key") or ""): material
        for material in mingkong_materials
        if isinstance(material, Mapping)
    }
    action_items = _json_loads(row.get("action_items_json"), []) or []
    if isinstance(action_items, list):
        upgraded_actions = []
        for action in action_items:
            if isinstance(action, dict):
                act_type = action.get("type")
                if act_type == "supplement_workbench":
                    action["label"] = "素材工作台"
                    url = action.get("url")
                    if isinstance(url, str) and "/medias/product/addvideo/" in url:
                        action["url"] = url.replace("/medias/product/addvideo/", "/medias/product/video_workbench/")
                elif act_type == "create_translation_task":
                    url = action.get("url")
                    if isinstance(url, str) and "/medias/product/addvideo/" in url:
                        action["url"] = url.replace("/medias/product/addvideo/", "/medias/product/video_workbench/")
                elif act_type == "import_mk_video":
                    material_key = str(action.get("material_key") or "")
                    action = _upgrade_import_action_payload(action, material_by_key.get(material_key))
            upgraded_actions.append(action)
        action_items = upgraded_actions

    return {
        "id": _safe_int(row.get("id")),
        "project_id": _safe_int(row.get("project_id")),
        "rank_no": _safe_int(row.get("rank_no")),
        "product_id": product_id,
        "product_code": row.get("product_code") or "",
        "product_name": row.get("product_name") or "",
        "score": _safe_float(row.get("score")),
        "metrics": _json_loads(row.get("metrics_json"), {}) or {},
        "country_summary": _json_loads(row.get("country_summary_json"), []) or [],
        "local_materials": _json_loads(row.get("local_materials_json"), []) or [],
        "mingkong_materials": mingkong_materials,
        "ai_result": _json_loads(row.get("ai_result_json"), {}) or {},
        "action_items": action_items,
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }



def build_preview() -> dict:
    snapshot = build_data_snapshot()
    candidates = score_product_rows(snapshot["products"], limit=_PROJECT_TOP_N)
    return {
        "window": snapshot.get("window") or {},
        "data_quality": snapshot.get("data_quality") or {},
        "product_count": len(snapshot.get("products") or []),
        "eligible_count": len(score_product_rows(snapshot["products"], limit=100000)),
        "top_candidates": [_rank_input(item) for item in candidates],
    }
