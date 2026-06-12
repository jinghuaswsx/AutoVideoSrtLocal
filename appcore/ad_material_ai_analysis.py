"""投放素材AI分析项目服务。

Docs anchor:
docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md#2026-06-10-功能拆分纠偏
"""
from __future__ import annotations

import json
import logging
import math
import re
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from appcore import db, llm_client

log = logging.getLogger(__name__)

RANK_USE_CASE = "medias.ad_material_ai_analysis_rank_products"
PRODUCT_ANALYSIS_USE_CASE = "medias.ad_material_ai_analysis_product_analysis"
PROVIDER_CODE = "google_wj"
MODEL_ID = "gemini-3.5-flash"
PROMPT_VERSION = "ad_material_review_v2026_06_10"

_RJC_SUFFIX_RE = re.compile(r"[-_]?rjc$", re.IGNORECASE)
_MAX_AI_CANDIDATES = 60
_PROJECT_TOP_N = 20

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
_PROJECT_LOCK_NAME = "ad_material_ai_analysis_single_running_project"
_SHARE_TOKEN_BYTES = 24
_PROGRESS_LOG_LIMIT = 12
PROGRESS_STEPS: tuple[dict[str, str], ...] = (
    {"key": "snapshot", "label": "读取数据窗口", "description": "读取产品、广告、订单、明空素材新鲜度。"},
    {"key": "candidate_score", "label": "规则预筛打分", "description": "按消耗、订单、ROAS、广告数筛选候选品。"},
    {"key": "ai_ranking", "label": "Top 20 AI 复评", "description": "分批调用 GoogleWJ Gemini 复评候选产品。"},
    {"key": "material_context", "label": "补齐素材上下文", "description": "读取国家反馈、本地素材、明空素材和任务中心排程。"},
    {"key": "product_analysis", "label": "逐产品分析", "description": "逐个产品分析国家、素材、任务去重和补素材建议。"},
    {"key": "persist", "label": "保存结果", "description": "写入 Top 产品、AI 建议和操作入口。"},
    {"key": "summary", "label": "汇总结论", "description": "生成项目级统计和完成状态。"},
)


class ProjectAlreadyRunningError(RuntimeError):
    def __init__(self, project: Mapping[str, Any] | None = None):
        super().__init__("已有投放素材AI分析项目正在运行")
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
            if step_status in {"done", "failed"}:
                step["finished_at"] = now
                if not step.get("started_at"):
                    step["started_at"] = now
        elif step_index >= 0:
            if index < step_index and step.get("status") in {"pending", "running"}:
                step["status"] = "done"
                if not step.get("started_at"):
                    step["started_at"] = now
                step["finished_at"] = step.get("finished_at") or now
            if step_status == "failed" and index > step_index and step.get("status") == "pending":
                step["status"] = "skipped"

    if product_progress is not None:
        current_product = dict(progress.get("product_progress") or {})
        current_product.update(dict(product_progress))
        progress["product_progress"] = current_product

    logs = list(progress.get("logs") or [])
    logs.append({"time": now, "level": level, "message": message})
    progress.update({
        "status": project_status,
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
        "UPDATE ad_material_ai_analysis_projects SET progress_json=%s, updated_at=NOW() WHERE id=%s",
        (_json_dumps(progress), project_id),
    )


def _normalize_progress(progress: Mapping[str, Any] | None, *, message: str) -> dict[str, Any]:
    base = _initial_progress(message=message)
    if not isinstance(progress, Mapping) or not progress:
        return base

    for key in (
        "status", "percent", "current_step", "current_step_label",
        "message", "updated_at",
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
        log.exception("Ad material AI analysis lock acquire failed")
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
        UPDATE ad_material_ai_analysis_projects
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
        UPDATE ad_material_ai_analysis_projects
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


def _snake_batches(items: list[dict], size: int = 20) -> list[list[dict]]:
    """按蛇形顺序把已排序候选交错分配到批次。

    顺序切分会让全局第 11-20 名与最强的前 10 名同批互斥出局，而弱批的
    第 41-60 名却能内部晋级；蛇形分配保证每批强弱混合，批内竞争公平。
    """
    items = list(items)
    if not items:
        return []
    batch_count = math.ceil(len(items) / max(size, 1))
    if batch_count <= 1:
        return [items]
    batches: list[list[dict]] = [[] for _ in range(batch_count)]
    for index, item in enumerate(items):
        round_no, pos = divmod(index, batch_count)
        target = pos if round_no % 2 == 0 else batch_count - 1 - pos
        batches[target].append(item)
    return batches


def _query_one_safe(sql: str, args: tuple[Any, ...] = ()) -> dict | None:
    try:
        return db.query_one(sql, args)
    except Exception:
        log.exception("Ad material AI analysis data quality query failed")
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
        "delivery_status", "effective_breakeven_roas",
    )
    payload = {key: row.get(key) for key in keys}
    breakeven = _safe_float(row.get("effective_breakeven_roas"))
    true_roas = row.get("true_roas_30d")
    if breakeven > 0 and true_roas is not None:
        payload["roas_vs_breakeven"] = round(_safe_float(true_roas) / breakeven, 4)
    else:
        payload["roas_vs_breakeven"] = None
    return payload


MATERIAL_REVIEW_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "final_decision": {"type": "string", "enum": ["通过", "条件通过", "不通过"]},
        "quality_score": {"type": "integer"},
        "score_breakdown": {
            "type": "object",
            "properties": {
                "product_history": {
                    "type": "object",
                    "properties": {
                        "score": {"type": ["integer", "null"]},
                        "max_score": {"type": "integer"},
                        "included": {"type": "boolean"},
                        "reason": {"type": "string"},
                        "sub_scores": {
                            "type": "object",
                            "properties": {
                                "recent_profitability": {"type": ["integer", "null"]},
                                "historical_winner_signal": {"type": ["integer", "null"]},
                            },
                            "required": ["recent_profitability", "historical_winner_signal"],
                        },
                    },
                    "required": ["score", "max_score", "included", "reason", "sub_scores"],
                },
                "creator_data": {
                    "type": "object",
                    "properties": {
                        "score": {"type": ["integer", "null"]},
                        "max_score": {"type": "integer"},
                        "included": {"type": "boolean"},
                        "reason": {"type": "string"},
                        "sub_scores": {
                            "type": "object",
                            "properties": {
                                "gpm_to_aov_ratio": {"type": ["integer", "null"]},
                                "sales_burst_signal": {"type": ["integer", "null"]},
                                "other_creator_signal": {"type": ["integer", "null"]},
                            },
                            "required": [
                                "gpm_to_aov_ratio",
                                "sales_burst_signal",
                                "other_creator_signal",
                            ],
                        },
                    },
                    "required": ["score", "max_score", "included", "reason", "sub_scores"],
                },
                "trend": {
                    "type": "object",
                    "properties": {
                        "score": {"type": ["integer", "null"]},
                        "max_score": {"type": "integer"},
                        "included": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["score", "max_score", "included", "reason"],
                },
                "video_content": {
                    "type": "object",
                    "properties": {
                        "score": {"type": ["integer", "null"]},
                        "max_score": {"type": "integer"},
                        "included": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["score", "max_score", "included", "reason"],
                },
            },
            "required": ["product_history", "creator_data", "trend", "video_content"],
        },
        "analysis_reason": {
            "type": "object",
            "properties": {
                "product_history_analysis": {"type": "string"},
                "creator_data_analysis": {"type": "string"},
                "trend_analysis": {"type": "string"},
                "video_content_analysis": {"type": "string"},
                "final_judgment_reason": {"type": "string"},
            },
            "required": [
                "product_history_analysis",
                "creator_data_analysis",
                "trend_analysis",
                "video_content_analysis",
                "final_judgment_reason",
            ],
        },
        "material_plan": {
            "type": "object",
            "properties": {
                "risk_alerts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": [
                                    "sensitive_word",
                                    "platform_word",
                                    "risky_expression",
                                    "visual_risk",
                                    "compliance_risk",
                                    "cultural_fit_risk",
                                ],
                            },
                            "original": {"type": "string"},
                            "risk_reason": {"type": "string"},
                            "suggested_fix": {"type": "string"},
                        },
                        "required": ["type", "original", "risk_reason", "suggested_fix"],
                    },
                },
                "editing_plan": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "target": {"type": "string"},
                            "issue": {"type": "string"},
                            "action": {
                                "type": "string",
                                "enum": [
                                    "delete",
                                    "move_forward",
                                    "mute",
                                    "replace_text",
                                    "crop",
                                    "speed_up",
                                    "add_caption",
                                    "keep",
                                ],
                            },
                            "reason": {"type": "string"},
                        },
                        "required": ["target", "issue", "action", "reason"],
                    },
                },
                "hook_suggestions": {"type": "array"},
                "highlight_segments_to_move_forward": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "segment": {"type": "string"},
                            "why_it_matters": {"type": "string"},
                            "suggested_new_position": {"type": "string"},
                        },
                        "required": ["segment", "why_it_matters", "suggested_new_position"],
                    },
                },
                "copy_extraction": {
                    "type": "object",
                    "properties": {
                        "original_language": {"type": "string", "enum": ["中文", "英文", "混合", "unknown"]},
                        "original_copy": {"type": "string"},
                        "english_translation": {"type": "string"},
                        "copy_source": {
                            "type": "string",
                            "enum": ["subtitle", "voiceover", "on_screen_text", "caption", "mixed", "unknown"],
                        },
                    },
                    "required": [
                        "original_language",
                        "original_copy",
                        "english_translation",
                        "copy_source",
                    ],
                },
            },
            "required": [
                "risk_alerts",
                "editing_plan",
                "hook_suggestions",
                "highlight_segments_to_move_forward",
                "copy_extraction",
            ],
        },
    },
    "required": [
        "final_decision",
        "quality_score",
        "score_breakdown",
        "analysis_reason",
        "material_plan",
    ],
}


def _product_review_facts(product_id: int) -> dict[str, Any]:
    return db.query_one(
        """
        SELECT
          p.id, p.name, p.product_code, p.selling_points,
          p.purchase_price, p.packet_cost_estimated, p.packet_cost_actual,
          p.standalone_price, p.standalone_shipping_fee,
          c.order_revenue_usd, c.shipping_revenue_usd, c.total_revenue_usd,
          c.ad_spend_usd, c.active_7d_ad_spend_usd, c.overall_roas,
          c.delivery_start_time, c.delivery_end_time, c.active_days
        FROM media_products p
        LEFT JOIN media_product_ad_summary_cache c ON c.product_id = p.id
        WHERE p.id = %s
        """,
        (product_id,),
    ) or {}


def _media_match_terms(material: Mapping[str, Any]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for key in ("filename", "display_name", "object_key"):
        raw = str(material.get(key) or "").strip().replace("\\", "/")
        if not raw:
            continue
        name = raw.split("?", 1)[0].rsplit("/", 1)[-1].strip().lower()
        candidates = [name]
        if "." in name:
            candidates.append(name.rsplit(".", 1)[0])
        for candidate in candidates:
            if len(candidate) < 4 or candidate in seen:
                continue
            seen.add(candidate)
            terms.append(candidate)
    return terms


def _row_matches_material(row: Mapping[str, Any], terms: list[str]) -> bool:
    haystack = (
        f"{row.get('ad_name') or ''} "
        f"{row.get('normalized_ad_code') or ''}"
    ).strip().lower()
    return bool(haystack and any(term in haystack for term in terms))


def _iso_week_bounds(day: date) -> tuple[date, date, str]:
    start = day - timedelta(days=day.weekday())
    end = start + timedelta(days=6)
    iso_year, iso_week, _ = day.isocalendar()
    return start, end, f"{iso_year}-W{iso_week:02d}"


def _load_product_ad_rows_for_materials(product_id: int) -> list[dict]:
    rows = db.query(
        """
        SELECT id, ad_name, normalized_ad_code,
               COALESCE(meta_business_date, report_date) AS activity_date,
               spend_usd, purchase_value_usd, result_count
        FROM meta_ad_daily_ad_metrics
        WHERE product_id = %s
          AND COALESCE(spend_usd, 0) > 0
        ORDER BY COALESCE(meta_business_date, report_date), id
        """,
        (product_id,),
    )
    for row in rows:
        row["metric_source"] = "daily"
    realtime_rows = db.query(
        """
        SELECT
          m.id,
          m.ad_name,
          m.normalized_ad_code,
          m.business_date AS activity_date,
          m.spend_usd,
          m.purchase_value_usd,
          m.result_count
        FROM (
          SELECT m.*
          FROM (
            SELECT latest_day.business_date, latest_day.ad_account_id, MAX(rt.snapshot_at) AS max_snapshot_at
            FROM meta_ad_realtime_daily_ad_metrics rt
            INNER JOIN (
              SELECT ad_account_id, MAX(business_date) AS business_date
              FROM meta_ad_realtime_daily_ad_metrics
              WHERE data_completeness = 'realtime_partial'
              GROUP BY ad_account_id
            ) latest_day
              ON rt.business_date = latest_day.business_date
             AND (rt.ad_account_id <=> latest_day.ad_account_id)
            WHERE rt.data_completeness = 'realtime_partial'
            GROUP BY latest_day.business_date, latest_day.ad_account_id
          ) latest
          STRAIGHT_JOIN meta_ad_realtime_daily_ad_metrics m
            ON m.business_date = latest.business_date
           AND (m.ad_account_id <=> latest.ad_account_id)
           AND m.snapshot_at = latest.max_snapshot_at
          WHERE m.data_completeness = 'realtime_partial'
            AND COALESCE(m.spend_usd, 0) > 0
        ) m
        JOIN media_products p
          ON p.id = %s
         AND p.deleted_at IS NULL
         AND p.product_code IS NOT NULL
         AND p.product_code <> ''
         AND (
           LOWER(COALESCE(m.normalized_campaign_code, '')) LIKE CONCAT(LOWER(p.product_code), '%%')
           OR LOWER(COALESCE(m.campaign_name, '')) LIKE CONCAT(LOWER(p.product_code), '%%')
           OR LOWER(COALESCE(m.normalized_ad_code, '')) LIKE CONCAT(LOWER(p.product_code), '%%')
           OR LOWER(COALESCE(m.ad_name, '')) LIKE CONCAT(LOWER(p.product_code), '%%')
         )
        ORDER BY m.business_date, m.id
        """,
        (product_id,),
    )
    for row in realtime_rows:
        row["metric_source"] = "realtime"
    rows.extend(realtime_rows)
    return rows


def _build_media_weekly_history(product_id: int, local_materials: list[dict]) -> list[dict]:
    materials: list[dict[str, Any]] = []
    material_terms: dict[int, list[str]] = {}
    for material in local_materials:
        media_id = _safe_int(material.get("id"))
        if not media_id:
            continue
        material_terms[media_id] = _media_match_terms(material)
        materials.append({
            "media_id": media_id,
            "lang": material.get("lang") or "",
            "filename": material.get("filename") or "",
            "display_name": material.get("display_name") or material.get("filename") or "",
            "created_at": material.get("created_at"),
            "mk_video_path": material.get("mk_video_path") or "",
            "total_spend": 0.0,
            "total_sales": 0.0,
            "insights": [],
        })
    if not materials:
        return []

    by_media = {_safe_int(item.get("media_id")): item for item in materials}
    week_map: dict[tuple[int, str], dict[str, Any]] = {}
    matched_metric_keys: set[tuple[int, str]] = set()
    for row in _load_product_ad_rows_for_materials(product_id):
        activity_date = _to_date(row.get("activity_date"))
        if activity_date is None:
            continue
        metric_key = f"{row.get('metric_source') or 'daily'}:{row.get('id') or ''}:{row.get('activity_date') or ''}"
        for media_id, terms in material_terms.items():
            if not terms or not _row_matches_material(row, terms):
                continue
            dedupe_key = (media_id, metric_key)
            if dedupe_key in matched_metric_keys:
                continue
            matched_metric_keys.add(dedupe_key)
            week_start, week_end, week_id = _iso_week_bounds(activity_date)
            bucket = week_map.setdefault(
                (media_id, week_id),
                {
                    "spend": 0.0,
                    "sales": 0.0,
                    "purchases": 0,
                    "date_start": week_start.isoformat(),
                    "date_end": week_end.isoformat(),
                    "week_id": week_id,
                },
            )
            bucket["spend"] += _safe_float(row.get("spend_usd"))
            bucket["sales"] += _safe_float(row.get("purchase_value_usd"))
            bucket["purchases"] += _safe_int(row.get("result_count"))

    for (media_id, _week_id), bucket in sorted(week_map.items(), key=lambda item: item[0][1]):
        media = by_media.get(media_id)
        if not media:
            continue
        spend = _safe_float(bucket.get("spend"))
        sales = _safe_float(bucket.get("sales"))
        insight = {
            "spend": round(spend, 4),
            "sales": round(sales, 4),
            "roas": _roas(sales, spend),
            "purchases": _safe_int(bucket.get("purchases")),
            "cpc": None,
            "cpm": None,
            "purchase_click_rate": None,
            "date_start": bucket["date_start"],
            "date_end": bucket["date_end"],
            "week_id": bucket["week_id"],
        }
        media["insights"].append(insight)
        media["total_spend"] = round(_safe_float(media.get("total_spend")) + spend, 4)
        media["total_sales"] = round(_safe_float(media.get("total_sales")) + sales, 4)
    return materials


def _effective_media_count(medias: list[dict], base_roas: Any) -> int:
    threshold = _safe_float(base_roas)
    count = 0
    for media in medias:
        for insight in media.get("insights") or []:
            spend = _safe_float(insight.get("spend"))
            roas = insight.get("roas")
            if spend <= 0:
                continue
            if threshold > 0:
                if _safe_float(roas) >= threshold:
                    count += 1
                    break
            elif roas is not None and _safe_float(roas) > 0:
                count += 1
                break
    return count


def _active_media_count(medias: list[dict], current_date: date) -> int:
    recent_start = current_date - timedelta(days=6)
    count = 0
    for media in medias:
        for insight in media.get("insights") or []:
            week_end = _to_date(insight.get("date_end"))
            if week_end and week_end >= recent_start and _safe_float(insight.get("spend")) > 0:
                count += 1
                break
    return count


def _build_product_brief(product: Mapping[str, Any], local_materials: list[dict]) -> dict[str, Any]:
    product_id = _safe_int(product.get("product_id"))
    facts = _product_review_facts(product_id)
    review_date = date.today()
    medias = _build_media_weekly_history(product_id, local_materials)
    total_medias = len(medias)
    base_roas = product.get("effective_breakeven_roas")
    if base_roas is None:
        try:
            from appcore import product_roas

            calc = product_roas.calculate_break_even_roas(
                purchase_price=facts.get("purchase_price"),
                estimated_packet_cost=facts.get("packet_cost_estimated"),
                actual_packet_cost=facts.get("packet_cost_actual"),
                standalone_price=facts.get("standalone_price"),
                standalone_shipping_fee=facts.get("standalone_shipping_fee"),
                rmb_per_usd=product_roas.get_configured_rmb_per_usd(),
            )
            base_roas = calc.get("effective_roas")
        except Exception:
            log.debug("failed to calculate product base_roas product_id=%s", product_id, exc_info=True)
            base_roas = None
    effective_count = _effective_media_count(medias, base_roas)
    active_count = _active_media_count(medias, review_date)
    cold_count = max(0, total_medias - effective_count)
    product_desc = str(facts.get("selling_points") or "").strip()
    recent_7d_sales = (
        _safe_float(product.get("revenue_7d"))
        if product.get("revenue_7d") is not None
        else _safe_float(product.get("purchase_value_7d"))
    )
    matrix = {
        "slug": product.get("product_code") or facts.get("product_code") or "",
        "total_medias": total_medias,
        "product_name": product.get("product_name") or facts.get("name") or "",
        "product_desc": product_desc,
        "base_roas": base_roas,
        "today": review_date.isoformat(),
        "active_days": _safe_int(facts.get("active_days")),
        "total_spend": _safe_float(facts.get("ad_spend_usd") or product.get("cached_ad_spend_usd")),
        "total_sales": _safe_float(facts.get("total_revenue_usd") or product.get("revenue_30d")),
        "overall_roas": facts.get("overall_roas") if facts.get("overall_roas") is not None else product.get("true_roas_30d"),
        "recent_7d_roas": _roas(recent_7d_sales, product.get("spend_7d")),
        "recent_7d_sales": recent_7d_sales,
        "recent_7d_spend": _safe_float(product.get("spend_7d")),
        "cold_media_count": cold_count,
        "active_media_count": active_count,
        "effective_media_count": effective_count,
        "hit_rate": round(effective_count / total_medias, 4) if total_medias else None,
        "medias": medias,
    }
    return {"code": 0, "data": {"matrix": matrix}, "message": ""}


def _build_candidate_video(mk_materials: list[dict]) -> dict[str, Any]:
    if not mk_materials:
        return {}
    material = dict(mk_materials[0])
    video_name = str(material.get("video_name") or "").strip()
    return {
        "source": "mingkong_material_daily_snapshots",
        "video_id": material.get("material_key") or "",
        "video_name": video_name,
        "desc": video_name,
        "video_path": material.get("video_path") or "",
        "video_url": material.get("video_url") or "",
        "cover_path": material.get("video_image_path") or "",
        "author_name": material.get("video_author") or "",
        "publish_time": material.get("video_upload_time") or "",
        "duration_seconds": material.get("video_duration_seconds"),
        "cumulative_90_spend": material.get("cumulative_90_spend"),
        "video_ads_count": material.get("video_ads_count"),
        "yesterday_spend_delta": material.get("yesterday_spend_delta"),
        "note": "该候选视频来自明空素材快照，不等同于达人销量归因数据。",
    }


def _build_creator_brief(candidate_video: Mapping[str, Any]) -> dict[str, Any]:
    author = str(candidate_video.get("author_name") or "").strip()
    if not author:
        return {}
    return {
        "identity": {"name": author},
        "commerce_metrics": {},
        "data_availability": {
            "included": False,
            "missing_fields": [
                "commerce_metrics.gpm_ratio",
                "commerce_metrics.latest_units_sold",
                "category_match_evidence",
            ],
            "reason": "当前仅有候选素材作者名，没有可证明达人卖货能力或候选视频贡献的数据。",
        },
    }


def _latest_stage1_visual_brief(local_materials: list[dict]) -> dict[str, Any]:
    item_ids = [_safe_int(item.get("id")) for item in local_materials if _safe_int(item.get("id"))]
    if not item_ids:
        return {}
    placeholders = ",".join(["%s"] * len(item_ids))
    row = db.query_one(
        f"""
        SELECT source_id, run_id, status, raw_response, overall_score,
               dimensions, verdict, verdict_reason, issues, highlights, completed_at
        FROM video_ai_reviews
        WHERE source_type = 'media_item'
          AND source_id IN ({placeholders})
          AND status = 'done'
        ORDER BY completed_at DESC, run_id DESC
        LIMIT 1
        """,
        tuple(str(item_id) for item_id in item_ids),
    )
    if not row:
        return {}
    raw = _json_loads(row.get("raw_response"), {}) or {}
    if isinstance(raw, Mapping) and any(
        key in raw
        for key in ("content_quality", "risk_alerts", "copy_extraction", "editing_plan")
    ):
        return dict(raw)
    return {
        "content_quality": {
            "overall_score": row.get("overall_score"),
            "verdict": row.get("verdict") or "",
            "reason": row.get("verdict_reason") or "",
            "dimensions": _json_loads(row.get("dimensions"), {}) or {},
        },
        "risk_alerts": _json_loads(row.get("issues"), []) or [],
        "editing_plan": [],
        "hook_suggestions": [],
        "highlight_segments_to_move_forward": _json_loads(row.get("highlights"), []) or [],
        "copy_extraction": {
            "original_language": "unknown",
            "original_copy": "未识别到原始文案",
            "english_translation": "No original copy detected.",
            "copy_source": "unknown",
        },
        "source_review": {
            "source_type": "media_item",
            "source_id": row.get("source_id"),
            "run_id": row.get("run_id"),
            "completed_at": _iso(row.get("completed_at")),
        },
    }


def _build_material_review_input(
    product: Mapping[str, Any],
    local_materials: list[dict],
    mk_materials: list[dict],
) -> dict[str, Any]:
    candidate_video = _build_candidate_video(mk_materials)
    stage1_visual_brief = _latest_stage1_visual_brief(local_materials)
    creator_brief = _build_creator_brief(candidate_video)
    payload = {
        "current_date": date.today().isoformat(),
        "product_brief": _build_product_brief(product, local_materials),
        "creator_brief": creator_brief,
        "candidate_video": candidate_video,
        "stage1_visual_brief": stage1_visual_brief,
    }
    missing = []
    if not creator_brief or not ((creator_brief.get("commerce_metrics") or {}).get("gpm_ratio")):
        missing.append("creator_brief.commerce_metrics.gpm_ratio")
    if not candidate_video:
        missing.append("candidate_video")
    if not stage1_visual_brief:
        missing.append("stage1_visual_brief")
    missing.append("future_45d_trend")
    payload["_adapter_notes"] = {
        "missing_modules": missing,
        "rule": "缺失数据不得补 0 分；提示词要求对应模块 included=false 并按参与满分折算 quality_score。",
    }
    return payload


def _material_review_prompt(payload: dict) -> str:
    rules = """
你是 Facebook 信息流广告补充素材的业务评审员。

判断优先级固定：
1. 商品历史投放数据，50 分
2. 达人数据，30 分
3. 未来 45 天季节 / 节日 / 外部市场趋势，15 分
4. 视频内容，5 分

最终判断不由分数硬卡线决定，也没有任何单项一票否决。分数只用于排序和解释强弱。
输出必须是严格 JSON，不要 Markdown，不要代码块，不要自然语言说明。

输入包含 current_date、product_brief、creator_brief、candidate_video、stage1_visual_brief。
current_date 是评审当天；如果 product_brief.data.matrix.today 不一致，优先使用 current_date。
product_brief.data.matrix 是商品历史核心依据；creator_brief 是第二依据；candidate_video 只做辅助和文本风险兜底；stage1_visual_brief 只占视频内容 5 分。

缺失数据适配规则：
只根据输入 JSON 判断，不要补全不存在的数据。
如果 creator_brief、candidate_video、stage1_visual_brief 或未来45天趋势依据为空或缺关键字段，对应 score 必须为 null、included=false、reason 写“该数据缺失，未参与评分”或等价说明。
不要把缺失解释成表现差，也不要填 0 分。
缺失模块如果有 sub_scores，小分也必须为 null。
quality_score 只按参与评分模块得分/参与评分模块满分折算为 0-100 整数。

final_decision 只能是：通过、条件通过、不通过。
允许通过：商品历史非常强，最近 7 天仍高于保本或历史充分验证且近期断档可合理理解为旧素材老化/素材断供；达人 GPM/客单价 >= 1；达人类目和商品匹配；视频问题是可剪辑小问题；趋势缺失或不明确但不影响商品和达人基本盘。
条件通过：有核心亮点但存在明显不确定性，包括商品历史曾经不错但近期转弱、商品历史强但达人或视频一般、达人强但商品历史弱、达人缺失、趋势依据不足、视频需要剪辑处理、信息不足但仍有补素材价值。
不通过：综合看没有足够商业理由继续做。不要因为单个风险词、单个视频瑕疵、单个缺失字段直接判死。

商品历史 50 分，拆分为最近赚钱能力 30 分、历史跑爆能力 20 分。商品历史分析必须是最详细的一段。
最近赚钱能力优先看 recent_7d_roas、recent_7d_spend、recent_7d_sales、base_roas；base_roas > 0 时 recent_7d_roas >= base_roas 是最强近期信号。
如需结合周明细，看 medias[].insights[] 中 date_end 接近 current_date 的有花费周；周记录是 ISO 自然周累计，不是日消耗。
若最近 7 天字段缺失，禁止写“最近 7 天没有消耗”或“近期转弱”；应写“输入未提供最近 7 天顶层聚合字段”，并改从最近有花费周判断。
若当前周覆盖 current_date，该周可能未结束，不能和完整历史周机械对比。
如果最近几周或几个月没有周度 insights，不能直接判断商品失效；可能是旧素材老化、素材断供、账户停投或投放节奏变化。
历史跑爆能力看 total_spend、total_sales、overall_roas、active_days、base_roas、total_medias、cold_media_count、active_media_count、effective_media_count、hit_rate、medias[].insights[]。
历史强商品即使近期没有周度记录，仍是强商品信号；不要因为历史周数据来自几个月前就自动扣重分。

达人数据 30 分，拆分为 GPM/客单价 15 分、销量爆发情况 10 分、其他达人辅助信号 5 分。
creator_brief.commerce_metrics.gpm_ratio >= 1 视为优秀。
latest_units_sold 默认理解为达人当前周期总销量，只能说明达人整体卖货规模，不等于候选视频销量，除非输入明确说明该销量由候选视频贡献。
候选视频是否爆发要看发布时间附近销量上升、is_top_video=true 或 rank_by_play 靠前、recent_30d_change/recent_30d_trend；这些热度变化不等于直接销量。
has_burst=false、pct_units_sold 下降、is_top_video=false 或 rank_by_play 靠后时，sales_burst_signal 通常落在 0-5 分。
分析理由里必须区分“达人整体销量”和“候选视频贡献”；不确定时写“输入未证明这些销量由候选视频贡献”。

未来趋势 15 分只看未来约 45 天的季节需求、节日需求、外部市场趋势、明确输入中给出的商品趋势依据。
禁止把达人最近播放量上升、达人最近销量上升、creator_brief.trend.direction、商品最近 7 天 ROAS 变化、商品历史周度投放数据、候选视频近期增长或“看起来最近热”当作趋势评分依据。
如果输入没有明确未来 45 天季节、节日或外部趋势依据，trend 必须 score=null、included=false、reason 写“缺少未来45天季节、节日或外部趋势依据，未参与评分”。

视频内容 5 分只看是否疑似 AI 视频、画质是否太差、是否不适合欧美投放、是否有明显可剪辑风险。视频内容只占 5 分，不能主导最终判断。
如果 stage1_visual_brief 是新版视频取证结构，用 content_quality 给视频分，用 risk_alerts 生成风险提示，用 editing_plan、hook_suggestions、highlight_segments_to_move_forward 生成剪辑方案，用 copy_extraction 生成原始文案和英文翻译。

风险和文案兜底扫描：
不能完全依赖 Stage 1 的 risk_alerts，必须二次扫描 candidate_video.desc、stage1_visual_brief.copy_extraction.original_copy、stage1_visual_brief.risk_alerts。
出现 tiktok、tiktokshop、tiktokmademebuyit、link in bio、bio link、storefront、shop、buyit、free shipping、freeshipping、明确平台导流表达、明显夸大承诺、中文 UI / 非英语界面、车牌 / 水印 / 品牌露出时，material_plan.risk_alerts 必须包含。
如果风险来自 candidate_video.desc，risk_reason 可写“视频描述包含平台/促销标签，不适合作为 Facebook 信息流广告原文案直接保留”。

原始文案提取：
只提取原始文案并翻译英文。禁止生成新的广告主文案、标题、CTA。
copy_extraction.original_copy 必须合并 Stage 1 识别到的口播 / 字幕 / 画面文字和 candidate_video.desc；多来源时用 [voiceover]、[video_desc] 等标签分段，copy_source 填 mixed。
如果没有识别到原始文案，输出 original_language=unknown、original_copy=未识别到原始文案、english_translation=No original copy detected.、copy_source=unknown。

剪辑方案：
editing_plan 必须可执行，不要只输出 keep。
必须覆盖平台词怎么处理、中文 UI 怎么处理、车牌/水印/品牌露出怎么处理、哪个爆点片段需要前置、哪段口播需要消音或删除。
如果风险来自 candidate_video.desc 且没有画面时间点，target 填 unknown，也要写清楚删除或替换描述文案中的平台词 / 促销词。
如果没有风险或剪辑建议，对应数组输出 []。

不要输出 test_plan。
final_judgment_reason 只解释为什么给这个判断，不要写上线、投放、测试、预算、首日指标、ROAS 观察计划、“建议上线投放”、“建议测试”。可以写“因此判断为通过/条件通过/不通过”。

严格按下面字段输出，不要增删字段：
{
  "final_decision": "通过 | 条件通过 | 不通过",
  "quality_score": 0,
  "score_breakdown": {
    "product_history": {
      "score": 0,
      "max_score": 50,
      "included": true,
      "reason": "",
      "sub_scores": {
        "recent_profitability": 0,
        "historical_winner_signal": 0
      }
    },
    "creator_data": {
      "score": 0,
      "max_score": 30,
      "included": true,
      "reason": "",
      "sub_scores": {
        "gpm_to_aov_ratio": 0,
        "sales_burst_signal": 0,
        "other_creator_signal": 0
      }
    },
    "trend": {
      "score": 0,
      "max_score": 15,
      "included": true,
      "reason": ""
    },
    "video_content": {
      "score": 0,
      "max_score": 5,
      "included": true,
      "reason": ""
    }
  },
  "analysis_reason": {
    "product_history_analysis": "",
    "creator_data_analysis": "",
    "trend_analysis": "",
    "video_content_analysis": "",
    "final_judgment_reason": ""
  },
  "material_plan": {
    "risk_alerts": [
      {
        "type": "sensitive_word | platform_word | risky_expression | visual_risk | compliance_risk | cultural_fit_risk",
        "original": "",
        "risk_reason": "",
        "suggested_fix": ""
      }
    ],
    "editing_plan": [
      {
        "target": "0:00-0:00 | unknown",
        "issue": "",
        "action": "delete | move_forward | mute | replace_text | crop | speed_up | add_caption | keep",
        "reason": ""
      }
    ],
    "hook_suggestions": [],
    "highlight_segments_to_move_forward": [
      {
        "segment": "0:00-0:00 | unknown",
        "why_it_matters": "",
        "suggested_new_position": "0:00-0:03"
      }
    ],
    "copy_extraction": {
      "original_language": "中文 | 英文 | 混合 | unknown",
      "original_copy": "未识别到原始文案",
      "english_translation": "No original copy detected.",
      "copy_source": "subtitle | voiceover | on_screen_text | caption | mixed | unknown"
    }
  }
}
""".strip()
    return f"{rules}\n\n输入 JSON：\n{_json_dumps(payload)}"


def _fallback_material_review(review_input: Mapping[str, Any], product: Mapping[str, Any]) -> dict[str, Any]:
    matrix = (((review_input.get("product_brief") or {}).get("data") or {}).get("matrix") or {})
    base_roas = _safe_float(matrix.get("base_roas"))
    recent_roas = matrix.get("recent_7d_roas")
    overall_roas = matrix.get("overall_roas")
    total_spend = _safe_float(matrix.get("total_spend"))
    total_sales = _safe_float(matrix.get("total_sales"))
    recent_spend = _safe_float(matrix.get("recent_7d_spend"))
    recent_sales = _safe_float(matrix.get("recent_7d_sales"))
    recent_score = 18
    if recent_spend > 0 and recent_sales > 0 and (base_roas <= 0 or _safe_float(recent_roas) >= base_roas):
        recent_score = 26
    elif recent_spend > 0:
        recent_score = 16
    historical_score = 8
    if total_spend >= 500 and total_sales > 0 and (base_roas <= 0 or _safe_float(overall_roas) >= base_roas):
        historical_score = 17
    elif total_spend >= 100:
        historical_score = 12
    product_score = recent_score + historical_score
    quality = int(round(product_score / 50 * 100))
    if product_score >= 40:
        decision = "通过"
    elif product_score >= 25:
        decision = "条件通过"
    else:
        decision = "不通过"
    product_reason = (
        f"商品历史按现有系统数据评估：累计消耗 {total_spend:.2f}，累计销售 {total_sales:.2f}，"
        f"整体ROAS {overall_roas if overall_roas is not None else '缺失'}，"
        f"近7天消耗 {recent_spend:.2f}，近7天销售 {recent_sales:.2f}，"
        f"近7天ROAS {recent_roas if recent_roas is not None else '缺失'}，"
        f"保本ROAS {matrix.get('base_roas') if matrix.get('base_roas') is not None else '缺失'}。"
    )
    return {
        "final_decision": decision,
        "quality_score": quality,
        "score_breakdown": {
            "product_history": {
                "score": product_score,
                "max_score": 50,
                "included": True,
                "reason": product_reason,
                "sub_scores": {
                    "recent_profitability": recent_score,
                    "historical_winner_signal": historical_score,
                },
            },
            "creator_data": {
                "score": None,
                "max_score": 30,
                "included": False,
                "reason": "该数据缺失，未参与评分",
                "sub_scores": {
                    "gpm_to_aov_ratio": None,
                    "sales_burst_signal": None,
                    "other_creator_signal": None,
                },
            },
            "trend": {
                "score": None,
                "max_score": 15,
                "included": False,
                "reason": "缺少未来45天季节、节日或外部趋势依据，未参与评分",
            },
            "video_content": {
                "score": None,
                "max_score": 5,
                "included": False,
                "reason": "该数据缺失，未参与评分",
            },
        },
        "analysis_reason": {
            "product_history_analysis": product_reason,
            "creator_data_analysis": "输入未提供可证明的达人 GPM/客单价、销量爆发或类目匹配数据。",
            "trend_analysis": "输入未提供未来45天季节、节日或外部市场趋势依据。",
            "video_content_analysis": "输入未提供可用的视频取证结果，视频内容未参与评分。",
            "final_judgment_reason": f"当前仅商品历史数据可参与评分，因此判断为{decision}。",
        },
        "material_plan": {
            "risk_alerts": [],
            "editing_plan": [],
            "hook_suggestions": [],
            "highlight_segments_to_move_forward": [],
            "copy_extraction": {
                "original_language": "unknown",
                "original_copy": "未识别到原始文案",
                "english_translation": "No original copy detected.",
                "copy_source": "unknown",
            },
        },
        "mode": "deterministic_fallback",
    }


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
        "并且有可补素材空间的产品。\n"
        "效率判断必须参照 roas_vs_breakeven（true_roas_30d / 保本ROAS）：>=1 代表已过保本线，"
        "不同产品保本线不同，禁止只看绝对 ROAS 高低比较产品；该字段为 null 时改看 true_roas_30d 并在理由中注明缺少保本线。\n"
        "输出严格 JSON，字段符合 response_schema。\n"
        f"输入数据：\n{_json_dumps(payload)}"
    )


def _product_prompt(payload: dict) -> str:
    return (
        "你是跨境电商投放素材AI分析评审员。只分析当前一个产品，不编造输入中没有的数据。\n"
        "请先判断产品阶段，再给补素材操作建议。建议必须落到国家、语言、素材或明空 video_path；"
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


def _run_ai_ranking(candidates: list[dict], *, project_id: int, user_id: int | None, run_ai: bool) -> dict[str, Any]:
    if not run_ai:
        return _fallback_ranking(candidates)
    try:
        batch_results: list[dict[str, Any]] = []
        merged_candidates: list[dict[str, Any]] = []
        for batch_index, batch in enumerate(_snake_batches(candidates, 20), start=1):
            payload = {
                "batch_index": batch_index,
                "rule": "本批最多输出 Top10，剔除高ROAS低量产品。",
                "products": [_rank_input(row) for row in batch],
            }
            result = llm_client.invoke_generate(
                RANK_USE_CASE,
                prompt=_ranking_prompt(payload),
                user_id=user_id,
                project_id=str(project_id),
                response_schema=RANKING_RESPONSE_SCHEMA,
                temperature=0.15,
                max_output_tokens=4096,
                provider_override=PROVIDER_CODE,
                model_override=MODEL_ID,
                billing_extra={"stage": "batch_rank", "batch_index": batch_index},
                timeout_seconds=180,
            )
            parsed = _llm_json(result)
            batch_results.append({
                "input": payload,
                "output": parsed,
                "usage_log_id": result.get("usage_log_id"),
                "prompt": _ranking_prompt(payload),
                "response_text": result.get("text"),
                "provider": PROVIDER_CODE,
                "model": MODEL_ID
            })
            ids = {_safe_int(item.get("product_id")) for item in parsed.get("ranked_products") or []}
            by_id = {_safe_int(item.get("product_id")): item for item in batch}
            merged_candidates.extend(by_id[pid] for pid in ids if pid in by_id)

        if not merged_candidates:
            return _fallback_ranking(candidates, "AI batch ranking returned empty")
        merged_candidates = score_product_rows(merged_candidates, limit=len(merged_candidates))
        final_payload = {
            "rule": "从所有批次候选里输出最终 Top20，仍然坚持有量 + 效率。",
            "products": [_rank_input(row) for row in merged_candidates],
        }
        final = llm_client.invoke_generate(
            RANK_USE_CASE,
            prompt=_ranking_prompt(final_payload),
            user_id=user_id,
            project_id=str(project_id),
            response_schema=RANKING_RESPONSE_SCHEMA,
            temperature=0.1,
            max_output_tokens=4096,
            provider_override=PROVIDER_CODE,
            model_override=MODEL_ID,
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
                "provider": PROVIDER_CODE,
                "model": MODEL_ID
            },
            "prompt_debug": {
                "provider": PROVIDER_CODE,
                "model": MODEL_ID,
                "use_case": RANK_USE_CASE,
                "batch_count": len(batch_results),
            },
        }
    except Exception as exc:
        log.exception("Ad material AI analysis ranking failed")
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
          i.duration_seconds, i.created_at,
          b.mk_video_path,
          COUNT(DISTINCT CASE WHEN l.status = 'success' THEN l.id END) AS push_count
        FROM media_items i
        LEFT JOIN media_item_mk_bindings b ON b.media_item_id = i.id
        LEFT JOIN media_push_logs l ON l.item_id = i.id
        WHERE i.deleted_at IS NULL
          AND i.product_id IN ({placeholders})
        GROUP BY i.id, i.product_id, i.lang, i.filename, i.display_name,
                 i.object_key, i.task_id, i.duration_seconds, i.created_at, b.mk_video_path
        ORDER BY i.product_id, i.created_at DESC, i.id DESC
        """,
        tuple(product_ids),
    )
    out: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        object_key = str(row.get("object_key") or "").strip()
        item = {
            "id": _safe_int(row.get("id")),
            "product_id": _safe_int(row.get("product_id")),
            "lang": row.get("lang") or "en",
            "filename": row.get("filename") or "",
            "display_name": row.get("display_name") or row.get("filename") or "",
            "object_key": object_key,
            "task_id": row.get("task_id") or "",
            "duration_seconds": _safe_float(row.get("duration_seconds")),
            "created_at": _iso(row.get("created_at")),
            "mk_video_path": row.get("mk_video_path") or "",
            "push_count": _safe_int(row.get("push_count")),
            "video_url": _local_video_url(object_key) if object_key else "",
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
    for row in rows:
        local_code = reverse.get(str(row.get("product_code") or "").strip().lower())
        if not local_code or len(grouped[local_code]) >= per_product_limit:
            continue
        video_path = str(row.get("video_path") or "").strip()
        material = {
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
            "video_author": row.get("video_author") or "",
            "video_upload_time": _iso(row.get("video_upload_time")),
            "video_duration_seconds": _safe_float(row.get("video_duration_seconds")),
            "yesterday_spend_delta": _safe_float(row.get("yesterday_spend_delta")),
            "top100_display_position": row.get("top100_display_position"),
            "snapshot_at": _iso(row.get("snapshot_at")),
            "mk_video_metadata": _mk_import_metadata(row),
        }
        grouped[local_code].append(material)
    return dict(grouped)


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
        target_langs = [item.get("lang") for item in country_actions if item.get("lang")]
        material_actions.append({
            "action": "import_or_translate",
            "material_key": picked_material.get("material_key", ""),
            "video_path": picked_material.get("video_path", ""),
            "target_langs": target_langs,
            "reason": "明空素材90天消耗/广告数靠前，适合作为补素材候选。",
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


def _run_product_analysis(
    product: dict,
    countries: list[dict],
    local_materials: list[dict],
    mk_materials: list[dict],
    *,
    project_id: int,
    user_id: int | None,
    run_ai: bool,
) -> dict:
    fallback = _fallback_product_analysis(product, countries, mk_materials)
    review_input = _build_material_review_input(product, local_materials, mk_materials)
    prompt_debug = {
        "provider": PROVIDER_CODE,
        "model": MODEL_ID,
        "use_case": PRODUCT_ANALYSIS_USE_CASE,
        "prompt_version": PROMPT_VERSION,
        "missing_modules": (review_input.get("_adapter_notes") or {}).get("missing_modules") or [],
    }
    if not run_ai:
        fallback["material_review_input"] = review_input
        fallback["material_review_result"] = _fallback_material_review(review_input, product)
        fallback["material_review_prompt_debug"] = {**prompt_debug, "mode": "deterministic_fallback"}
        return fallback
    try:
        result = llm_client.invoke_generate(
            PRODUCT_ANALYSIS_USE_CASE,
            prompt=_material_review_prompt(review_input),
            user_id=user_id,
            project_id=str(project_id),
            response_schema=MATERIAL_REVIEW_RESPONSE_SCHEMA,
            temperature=0.2,
            max_output_tokens=8192,
            provider_override=PROVIDER_CODE,
            model_override=MODEL_ID,
            billing_extra={
                "stage": "material_review",
                "product_id": product.get("product_id"),
                "prompt_version": PROMPT_VERSION,
            },
            timeout_seconds=180,
        )
        parsed = _llm_json(result)
        if not parsed:
            fallback["material_review_input"] = review_input
            fallback["material_review_result"] = _fallback_material_review(review_input, product)
            fallback["material_review_prompt_debug"] = {**prompt_debug, "mode": "empty_model_response"}
            fallback["ai_error"] = "empty model response"
            return fallback
        fallback["mode"] = "ai"
        fallback["material_review_input"] = review_input
        fallback["material_review_result"] = parsed
        fallback["material_review_prompt_debug"] = {
            **prompt_debug,
            "mode": "ai",
            "usage_log_id": result.get("usage_log_id"),
            "prompt": _material_review_prompt(review_input),
            "response_text": result.get("text"),
        }
        quality_score = _safe_int(parsed.get("quality_score"))
        if quality_score >= 80:
            fallback["priority"] = "P0"
        elif quality_score >= 65:
            fallback["priority"] = "P1"
        elif quality_score >= 45:
            fallback["priority"] = "P2"
        else:
            fallback["priority"] = "P3"
        final_reason = ((parsed.get("analysis_reason") or {}).get("final_judgment_reason") or "").strip()
        if final_reason:
            fallback["overall_judgement"] = final_reason
        if parsed.get("final_decision") == "不通过":
            fallback["primary_action"] = "hold"
        return fallback
    except Exception as exc:
        log.exception("Ad material AI analysis product analysis failed product_id=%s", product.get("product_id"))
        fallback["material_review_input"] = review_input
        fallback["material_review_result"] = _fallback_material_review(review_input, product)
        fallback["material_review_prompt_debug"] = {**prompt_debug, "mode": "error"}
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
        video_path = str(material.get("video_path") or "").strip()
        if video_path:
            actions.append({
                "type": "view_mk_video",
                "label": "看明空视频",
                "url": material.get("video_url") or _mk_video_url(video_path),
                "material_key": material.get("material_key"),
            })
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
        FROM ad_material_ai_analysis_projects
        WHERE id = %s
        """,
        (project_id,),
    )


def _mark_other_running_projects_interrupted(project_id: int) -> None:
    rows = db.query(
        """
        SELECT id, progress_json
        FROM ad_material_ai_analysis_projects
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
        current_step = str(progress.get("current_step") or "snapshot")
        progress = _progress_update(
            progress,
            step_key=current_step if current_step != "queued" else "snapshot",
            step_status="failed",
            percent=_safe_float(progress.get("percent")),
            message=f"历史运行线程已中断，当前恢复执行项目 #{project_id}；本项目已标记失败。",
            project_status="failed",
            level="error",
        )
        db.execute(
            """
            UPDATE ad_material_ai_analysis_projects
            SET status = 'failed',
                error_message = %s,
                progress_json = %s,
                finished_at = NOW(),
                updated_at = NOW()
            WHERE id = %s AND status = 'running'
            """,
            (
                f"历史运行线程已中断，当前恢复执行项目 #{project_id}。",
                _json_dumps(progress),
                other_id,
            ),
        )


def _prepare_project_for_run(project_id: int) -> dict:
    row = _load_project_row(project_id)
    if not row:
        raise ValueError(f"投放素材AI分析项目不存在：{project_id}")
    if row.get("status") == "success":
        return row
    progress = _normalize_progress(
        _json_loads(row.get("progress_json"), {}) or {},
        message="从断点恢复执行。",
    )
    db.execute(
        """
        UPDATE ad_material_ai_analysis_projects
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
               mingkong_materials_json, ai_result_json, country_reviews_json,
               market_expansion_json, action_items_json,
               created_at, updated_at
        FROM ad_material_ai_analysis_product_results
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
        INSERT INTO ad_material_ai_analysis_product_results
          (project_id, rank_no, product_id, product_code, product_name, score,
           metrics_json, country_summary_json, local_materials_json,
           mingkong_materials_json, ai_result_json, country_reviews_json,
           market_expansion_json, action_items_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
          country_reviews_json = VALUES(country_reviews_json),
          market_expansion_json = VALUES(market_expansion_json),
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
            _json_dumps(item.get("country_reviews") or {}),
            _json_dumps(item.get("market_expansion") or []),
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
        DELETE FROM ad_material_ai_analysis_product_results
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
        FROM ad_material_ai_analysis_projects
        WHERE status = 'running'
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """
    )
    return _serialize_project_row(row, include_products=False) if row else None


def create_project_record(user_id: int | None, project_name: str | None = None) -> dict:
    name = (project_name or "").strip() or f"投放素材AI分析 {datetime.now():%Y-%m-%d %H:%M}"
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
                FROM ad_material_ai_analysis_projects
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
                INSERT INTO ad_material_ai_analysis_projects
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


def _resolve_billing_user_id(explicit_user_id: int | None = None) -> int | None:
    if explicit_user_id:
        return int(explicit_user_id)
    try:
        row = db.query_one(
            "SELECT id FROM users "
            "WHERE is_active=1 AND role IN ('superadmin','admin') "
            "ORDER BY CASE WHEN username='admin' THEN 0 WHEN role='superadmin' THEN 1 ELSE 2 END, id ASC "
            "LIMIT 1"
        )
        if row:
            return int(row["id"])
    except Exception:
        log.warning("Failed to resolve billing user ID", exc_info=True)
    return None


def run_project(project_id: int, *, user_id: int | None = None, run_ai: bool = True) -> dict:
    lock_conn = _with_project_lock(timeout_seconds=5)
    if lock_conn is None:
        log.warning("Ad material AI analysis runner lock busy project_id=%s", project_id)
        return get_project(project_id) or {"id": project_id, "status": "running"}
    try:
        return _run_project_locked(project_id, user_id=user_id, run_ai=run_ai)
    finally:
        _release_project_lock(lock_conn)


def _run_project_locked(project_id: int, *, user_id: int | None = None, run_ai: bool = True) -> dict:
    user_id = _resolve_billing_user_id(user_id)
    project_row = _prepare_project_for_run(project_id)
    if project_row.get("status") == "success":
        return get_project(project_id) or {"id": project_id, "status": "success"}

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
                "复用已保存 Top 20 排名结果，不重复调用排名模型。",
            )
        else:
            checkpoint("ai_ranking", "running", 32, "调用 GoogleWJ Gemini 分批复评 Top 20。")
            ranking = _run_ai_ranking(candidates, project_id=project_id, user_id=user_id, run_ai=run_ai)
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
            mk_materials = mk_by_code.get(code_key) or []
            ai_result = _run_product_analysis(
                product,
                countries,
                local_materials,
                mk_materials,
                project_id=project_id,
                user_id=user_id,
                run_ai=run_ai,
            )
            ai_result = _decorate_ai_result_with_tasks(ai_result, countries, task_assignments)
            action_items = _build_action_items(product, ai_result, mk_materials, countries)

            # --- 逐国独立评估 ---
            countries_by_code = {
                _normalize_country_code(c.get("country_code"), lang=c.get("lang")): c
                for c in countries
            }
            country_reviews: dict[str, dict] = {}
            for eval_country in TARGET_EVAL_COUNTRIES:
                cc = eval_country["country_code"]
                country_data = countries_by_code.get(cc, {})
                country_lang = eval_country.get("lang", "")
                country_materials_for_lang = [
                    m for m in local_materials
                    if str(m.get("lang") or "").lower() == country_lang
                ]
                country_tasks_for_cc = [
                    t for t in task_assignments
                    if _normalize_country_code(t.get("country_code"), lang=t.get("lang")) == cc
                ]
                review = _run_country_review(
                    product, eval_country, country_data,
                    country_materials_for_lang, mk_materials, country_tasks_for_cc,
                    project_id=project_id, user_id=user_id, run_ai=run_ai,
                )
                country_reviews[cc] = review
            market_expansion = _build_market_expansion_recommendations(
                product, country_reviews, countries,
            )

            results.append({
                "rank_no": rank_no,
                "product": product,
                "country_summary": countries,
                "local_materials": local_materials,
                "mingkong_materials": mk_materials,
                "ai_result": ai_result,
                "country_reviews": country_reviews,
                "market_expansion": market_expansion,
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

        checkpoint("persist", "running", 88, "整理已落库结果，清理不在本轮 Top 20 内的旧结果。")
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
        db.execute(
            """
            UPDATE ad_material_ai_analysis_projects
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
        log.exception("Ad material AI analysis project failed project_id=%s", project_id)
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
        db.execute(
            """
            UPDATE ad_material_ai_analysis_projects
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
        FROM ad_material_ai_analysis_projects
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
        FROM ad_material_ai_analysis_projects
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
        FROM ad_material_ai_analysis_projects
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
        "DELETE FROM ad_material_ai_analysis_product_results WHERE project_id = %s",
        (project_id,),
    )
    db.execute(
        "DELETE FROM ad_material_ai_analysis_projects WHERE id = %s",
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
        FROM ad_material_ai_analysis_projects
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
                UPDATE ad_material_ai_analysis_projects
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
            FROM ad_material_ai_analysis_projects
            WHERE id = %s
            """,
            (project_id,),
        )
        if row and row.get("share_token"):
            return _serialize_share_row(row)
    raise RuntimeError("生成投放素材AI分析分享链接失败，请重试")


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
        FROM ad_material_ai_analysis_projects
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


def _serialize_project_row(row: Mapping[str, Any], *, include_products: bool) -> dict:
    status = row.get("status") or "running"
    progress = _json_loads(row.get("progress_json"), {}) or {}
    if not progress and status == "running":
        progress = _initial_progress(message="项目正在运行，等待后台写入详细进度。")
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
    mingkong_materials = _json_loads(row.get("mingkong_materials_json"), []) or []
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
        "product_id": _safe_int(row.get("product_id")),
        "product_code": row.get("product_code") or "",
        "product_name": row.get("product_name") or "",
        "score": _safe_float(row.get("score")),
        "metrics": _json_loads(row.get("metrics_json"), {}) or {},
        "country_summary": _json_loads(row.get("country_summary_json"), []) or [],
        "local_materials": _json_loads(row.get("local_materials_json"), []) or [],
        "mingkong_materials": mingkong_materials,
        "ai_result": _json_loads(row.get("ai_result_json"), {}) or {},
        "country_reviews": _json_loads(row.get("country_reviews_json"), {}) or {},
        "market_expansion": _json_loads(row.get("market_expansion_json"), []) or [],
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


# ---------------------------------------------------------------------------
# 多国独立评估（Country-level review）
# ---------------------------------------------------------------------------

COUNTRY_REVIEW_USE_CASE = "medias.ad_material_ai_analysis_country_review"

# 5个目标评估国家（不包含 EN 源语言）
TARGET_EVAL_COUNTRIES: tuple[dict[str, str], ...] = (
    {"country_code": "DE", "country_name": "德国", "lang": "de", "lang_name": "德语"},
    {"country_code": "FR", "country_name": "法国", "lang": "fr", "lang_name": "法语"},
    {"country_code": "IT", "country_name": "意大利", "lang": "it", "lang_name": "意大利语"},
    {"country_code": "ES", "country_name": "西班牙", "lang": "es", "lang_name": "西班牙语"},
    {"country_code": "JP", "country_name": "日本", "lang": "ja", "lang_name": "日语"},
)

COUNTRY_REVIEW_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "country_code": {"type": "string"},
        "final_decision": {"type": "string", "enum": ["通过", "条件通过", "不通过"]},
        "quality_score": {"type": "integer"},
        "score_breakdown": {
            "type": "object",
            "properties": {
                "global_product_history": {
                    "type": "object",
                    "properties": {
                        "score": {"type": ["integer", "null"]},
                        "max_score": {"type": "integer"},
                        "included": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["score", "max_score", "included", "reason"],
                },
                "country_performance": {
                    "type": "object",
                    "properties": {
                        "score": {"type": ["integer", "null"]},
                        "max_score": {"type": "integer"},
                        "included": {"type": "boolean"},
                        "reason": {"type": "string"},
                        "sub_scores": {
                            "type": "object",
                            "properties": {
                                "country_spend_and_roas": {"type": ["integer", "null"]},
                                "country_material_coverage": {"type": ["integer", "null"]},
                            },
                            "required": ["country_spend_and_roas", "country_material_coverage"],
                        },
                    },
                    "required": ["score", "max_score", "included", "reason", "sub_scores"],
                },
                "material_supplement_opportunity": {
                    "type": "object",
                    "properties": {
                        "score": {"type": ["integer", "null"]},
                        "max_score": {"type": "integer"},
                        "included": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["score", "max_score", "included", "reason"],
                },
            },
            "required": ["global_product_history", "country_performance", "material_supplement_opportunity"],
        },
        "analysis_reason": {
            "type": "object",
            "properties": {
                "global_history_analysis": {"type": "string"},
                "country_performance_analysis": {"type": "string"},
                "material_opportunity_analysis": {"type": "string"},
                "final_judgment_reason": {"type": "string"},
            },
            "required": [
                "global_history_analysis",
                "country_performance_analysis",
                "material_opportunity_analysis",
                "final_judgment_reason",
            ],
        },
        "recommended_action": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["expand", "supplement", "retest", "hold", "skip"],
                },
                "reason": {"type": "string"},
            },
            "required": ["action", "reason"],
        },
    },
    "required": [
        "country_code",
        "final_decision",
        "quality_score",
        "score_breakdown",
        "analysis_reason",
        "recommended_action",
    ],
}


def _build_country_review_input(
    product: Mapping[str, Any],
    country_info: Mapping[str, Any],
    country_summary: Mapping[str, Any],
    country_materials: list[dict],
    mk_materials: list[dict],
    country_tasks: list[dict],
) -> dict[str, Any]:
    """为单个产品×单个国家构建 LLM 评估输入。"""
    country_code = country_info.get("country_code", "")
    lang = country_info.get("lang", "")

    # Global product history
    product_global = {
        "product_id": product.get("product_id"),
        "product_code": product.get("product_code") or "",
        "product_name": product.get("product_name") or "",
        "effective_breakeven_roas": product.get("effective_breakeven_roas"),
        "cached_overall_roas": product.get("cached_overall_roas"),
        "cached_ad_spend_usd": product.get("cached_ad_spend_usd"),
        "cached_active_7d_ad_spend_usd": product.get("cached_active_7d_ad_spend_usd"),
        "delivery_status": product.get("delivery_status") or "never",
        "spend_30d": product.get("spend_30d"),
        "spend_7d": product.get("spend_7d"),
        "spend_yesterday": product.get("spend_yesterday"),
        "orders_30d": product.get("orders_30d"),
        "orders_7d": product.get("orders_7d"),
        "revenue_30d": product.get("revenue_30d"),
        "profit_30d": product.get("profit_30d"),
        "true_roas_30d": product.get("true_roas_30d"),
        "meta_roas_30d": product.get("meta_roas_30d"),
        "local_material_count": product.get("local_material_count"),
        "local_material_langs": product.get("local_material_langs"),
    }

    # Country-specific performance
    country_performance = {
        "country_code": country_code,
        "country_name": country_info.get("country_name", ""),
        "lang": lang,
        "lang_name": country_info.get("lang_name", ""),
        "ad_spend_usd": _safe_float(country_summary.get("ad_spend_usd")),
        "purchase_value_usd": _safe_float(country_summary.get("purchase_value_usd")),
        "ad_roas": country_summary.get("ad_roas"),
        "active_7d_ad_spend_usd": _safe_float(country_summary.get("active_7d_ad_spend_usd")),
        "item_count": _safe_int(country_summary.get("item_count")),
        "pushed_video_count": _safe_int(country_summary.get("pushed_video_count")),
        "delivery_status": country_summary.get("delivery_status") or "never",
    }

    # Materials in this language
    material_info = {
        "local_materials_count": len(country_materials),
        "local_materials": [
            {
                "id": m.get("id"),
                "filename": m.get("filename") or m.get("display_name") or "",
                "push_count": _safe_int(m.get("push_count")),
                "created_at": m.get("created_at"),
            }
            for m in country_materials[:5]
        ],
        "mingkong_available_count": len(mk_materials),
        "mingkong_top_materials": [
            {
                "material_key": m.get("material_key") or "",
                "video_name": m.get("video_name") or "",
                "cumulative_90_spend": _safe_float(m.get("cumulative_90_spend")),
                "video_ads_count": _safe_int(m.get("video_ads_count")),
            }
            for m in mk_materials[:3]
        ],
    }

    # Tasks for this country
    task_info = {
        "total_tasks": len(country_tasks),
        "tasks": [
            {
                "task_id": t.get("task_id"),
                "status_group": t.get("status_group"),
                "status_label": t.get("status_label"),
                "created_at": t.get("created_at"),
            }
            for t in country_tasks[:5]
        ],
        "has_blocking_task": any(_task_blocks_recommendation(t) for t in country_tasks),
    }

    return {
        "current_date": date.today().isoformat(),
        "target_country": {
            "country_code": country_code,
            "country_name": country_info.get("country_name", ""),
            "lang": lang,
            "lang_name": country_info.get("lang_name", ""),
        },
        "product_global": product_global,
        "country_performance": country_performance,
        "available_materials": material_info,
        "existing_tasks": task_info,
    }


def _country_review_prompt(payload: dict, country_info: Mapping[str, Any]) -> str:
    """生成单个国家的投放素材评估提示词。"""
    country_name = country_info.get("country_name", "")
    lang_name = country_info.get("lang_name", "")
    country_code = country_info.get("country_code", "")

    rules = f"""你是 Facebook 信息流广告投放的业务评审员，负责评估一个产品在 {country_name}（{lang_name}）市场的素材补充价值。

评估维度和权重：
1. 商品全局历史（40 分）— 产品整体投放验证程度
2. 该国投放表现（40 分）— 产品在{country_name}的具体表现
3. 素材补充空间（20 分）— {country_name}市场可执行的素材补充机会

输出必须是严格 JSON，不要 Markdown，不要代码块，不要自然语言说明。

---

## 输入数据说明

- `current_date`：评审当天日期
- `target_country`：本次评估的目标国家信息
- `product_global`：产品全局投放数据（所有国家汇总）
- `country_performance`：产品在{country_name}的投放表现
- `available_materials`：可用素材（本地已有 + 明空可用）
- `existing_tasks`：{country_name}已有的任务排程

---

## 一、最终判断规则

`final_decision` 只能是：通过、条件通过、不通过。

### 给「通过」的情况
- 商品全局历史强（累计消耗高、overall_roas 高于保本、active_days 长）
- 该国已有投放且 ROAS 健康（ad_roas >= effective_breakeven_roas 或近 7 天仍有消耗）
- 有素材补充空间（该国素材数少或明空有可用新素材）
- 没有阻塞任务

### 给「条件通过」的情况
- 商品全局历史强，但该国尚未投放或投放很少
- 该国有投放但 ROAS 偏低，需要新素材验证
- 有素材补充空间但不确定性较大
- 已有任务在进行中，建议等任务完成后再决定

### 给「不通过」的情况
- 商品全局历史弱（消耗少、ROAS 低于保本、验证不充分）
- 该国投放历史差且没有合理改善方向
- 已有多个阻塞任务且无新素材可补
- 该国市场与产品类别不匹配

---

## 二、评分规则

### 1. 商品全局历史（40 分）

重点看 `product_global` 中的：
- `cached_ad_spend_usd`：全局累计消耗
- `cached_overall_roas`：全局整体 ROAS
- `effective_breakeven_roas`：保本 ROAS
- `cached_active_7d_ad_spend_usd`：近 7 天全局消耗
- `spend_30d`、`orders_30d`、`revenue_30d`、`profit_30d`：30 天窗口数据
- `true_roas_30d`：30 天真实 ROAS

判断规则：
- 累计消耗高、overall_roas 高于保本、近 7 天仍有消耗 → 35-40 分
- 累计消耗中等、ROAS 健康但近期变弱 → 25-34 分
- 累计消耗少、ROAS 一般 → 15-24 分
- 几乎无投放验证 → 0-14 分

### 2. 该国投放表现（40 分）

拆分为：
- 该国消耗和 ROAS（25 分）
- 该国素材覆盖（15 分）

看 `country_performance` 中的：
- `ad_spend_usd`：该国累计广告消耗
- `ad_roas`：该国 ROAS
- `active_7d_ad_spend_usd`：该国近 7 天消耗
- `item_count`：该国已有素材数
- `pushed_video_count`：该国已推送视频数
- `delivery_status`：该国投放状态（active/stopped/never）

判断规则：
- `delivery_status=active` 且 `ad_roas >= effective_breakeven_roas` → 高分
- `delivery_status=active` 但 `ad_roas` 低于保本 → 中等分
- `delivery_status=stopped`（曾投放但已停） → 需看历史消耗大小
- `delivery_status=never`（从未在该国投放） → 该国消耗和 ROAS 给 0 分，但素材覆盖可加分

如果 `delivery_status=never`：
- `country_spend_and_roas` 给 0 分，`included` 设为 false，reason 写"该国尚未投放"
- `country_material_coverage` 正常评分

### 3. 素材补充空间（20 分）

看 `available_materials` 和 `existing_tasks`：
- 该国本地素材数少（item_count <= 2）且明空有可用素材 → 15-20 分
- 该国本地素材数中等且明空有可用素材 → 10-14 分
- 该国本地素材数充足 → 5-9 分
- 已有阻塞任务在进行中 → 降低评分
- 没有任何可用素材来源 → 0-4 分

---

## 三、recommended_action 规则

- `expand`：该国从未投放，但商品全局强，建议扩展到该国
- `supplement`：该国已在投放，素材可补充
- `retest`：该国曾投放但已停，值得用新素材重试
- `hold`：有阻塞任务或需等待数据
- `skip`：商品或该国条件不足，不建议当前投入

---

## 四、缺失数据处理

如果 `country_performance` 中 `delivery_status=never`（从未在该国投放）：
- `country_performance.score` 正常评分（素材覆盖部分仍可评），`country_spend_and_roas` 设为 0
- 不要把从未投放等同于表现差，它只是没有数据
- 从未投放 + 商品全局强 = 扩展机会

---

## 五、输出 JSON Schema

严格按下面字段输出，不要增删字段：

{{
  "country_code": "{country_code}",
  "final_decision": "通过 | 条件通过 | 不通过",
  "quality_score": 0,
  "score_breakdown": {{
    "global_product_history": {{
      "score": 0,
      "max_score": 40,
      "included": true,
      "reason": ""
    }},
    "country_performance": {{
      "score": 0,
      "max_score": 40,
      "included": true,
      "reason": "",
      "sub_scores": {{
        "country_spend_and_roas": 0,
        "country_material_coverage": 0
      }}
    }},
    "material_supplement_opportunity": {{
      "score": 0,
      "max_score": 20,
      "included": true,
      "reason": ""
    }}
  }},
  "analysis_reason": {{
    "global_history_analysis": "",
    "country_performance_analysis": "",
    "material_opportunity_analysis": "",
    "final_judgment_reason": ""
  }},
  "recommended_action": {{
    "action": "expand | supplement | retest | hold | skip",
    "reason": ""
  }}
}}

字段约束：
- quality_score 为 0-100 整数
- analysis_reason 中各分析字段必须用中文
- final_judgment_reason 只解释判断原因，不要写上线、投放、测试建议""".strip()
    return f"{rules}\n\n输入 JSON：\n{_json_dumps(payload)}"


def _fallback_country_review(
    product: Mapping[str, Any],
    country_info: Mapping[str, Any],
    country_summary: Mapping[str, Any],
    country_tasks: list[dict],
) -> dict[str, Any]:
    """无 LLM 时的确定性兜底逐国评估。"""
    country_code = country_info.get("country_code", "")
    base_roas = _safe_float(product.get("effective_breakeven_roas"))
    spend30 = _safe_float(product.get("spend_30d"))
    overall_roas = _safe_float(product.get("cached_overall_roas"))

    # Global product history score (out of 40)
    if spend30 >= 300 and (base_roas <= 0 or overall_roas >= base_roas):
        global_score = 35
    elif spend30 >= 100:
        global_score = 25
    elif spend30 >= 50:
        global_score = 18
    else:
        global_score = 8

    # Country performance score (out of 40)
    country_spend = _safe_float(country_summary.get("ad_spend_usd"))
    country_roas = country_summary.get("ad_roas")
    country_7d = _safe_float(country_summary.get("active_7d_ad_spend_usd"))
    delivery_status = country_summary.get("delivery_status") or "never"
    item_count = _safe_int(country_summary.get("item_count"))

    if delivery_status == "active" and country_roas is not None and (base_roas <= 0 or _safe_float(country_roas) >= base_roas):
        spend_roas_score = 22
    elif delivery_status == "active":
        spend_roas_score = 14
    elif delivery_status == "stopped" and country_spend >= 50:
        spend_roas_score = 8
    else:
        spend_roas_score = 0

    if item_count >= 5:
        material_coverage_score = 12
    elif item_count >= 2:
        material_coverage_score = 8
    elif item_count >= 1:
        material_coverage_score = 5
    else:
        material_coverage_score = 0

    country_score = spend_roas_score + material_coverage_score

    # Material supplement opportunity (out of 20)
    has_blocking = any(_task_blocks_recommendation(t) for t in country_tasks)
    if has_blocking:
        supplement_score = 5
    elif item_count <= 1:
        supplement_score = 18
    elif item_count <= 3:
        supplement_score = 14
    else:
        supplement_score = 8

    total = global_score + country_score + supplement_score
    quality = int(round(total / 100 * 100))

    if total >= 70:
        decision = "通过"
    elif total >= 45:
        decision = "条件通过"
    else:
        decision = "不通过"

    if delivery_status == "never" and global_score >= 25:
        action = "expand"
    elif delivery_status == "active":
        action = "supplement"
    elif delivery_status == "stopped":
        action = "retest"
    elif has_blocking:
        action = "hold"
    else:
        action = "skip"

    return {
        "country_code": country_code,
        "final_decision": decision,
        "quality_score": quality,
        "score_breakdown": {
            "global_product_history": {
                "score": global_score,
                "max_score": 40,
                "included": True,
                "reason": f"商品全局30天消耗 {spend30:.2f}，overall ROAS {overall_roas}，保本ROAS {base_roas}。",
            },
            "country_performance": {
                "score": country_score,
                "max_score": 40,
                "included": delivery_status != "never",
                "reason": f"{country_info.get('country_name', '')}投放状态 {delivery_status}，累计消耗 {country_spend:.2f}，ROAS {country_roas}，素材数 {item_count}。",
                "sub_scores": {
                    "country_spend_and_roas": spend_roas_score,
                    "country_material_coverage": material_coverage_score,
                },
            },
            "material_supplement_opportunity": {
                "score": supplement_score,
                "max_score": 20,
                "included": True,
                "reason": f"该国素材数 {item_count}，{'有阻塞任务' if has_blocking else '无阻塞任务'}。",
            },
        },
        "analysis_reason": {
            "global_history_analysis": f"商品全局30天消耗 {spend30:.2f} USD，overall ROAS {overall_roas}。",
            "country_performance_analysis": f"{country_info.get('country_name', '')}投放状态为 {delivery_status}，累计消耗 {country_spend:.2f} USD。",
            "material_opportunity_analysis": f"该国本地素材 {item_count} 个。",
            "final_judgment_reason": f"综合评估后判断为{decision}。",
        },
        "recommended_action": {
            "action": action,
            "reason": "基于确定性评估的建议操作。",
        },
        "mode": "deterministic_fallback",
    }


def _run_country_review(
    product: Mapping[str, Any],
    country_info: Mapping[str, Any],
    country_summary: Mapping[str, Any],
    country_materials: list[dict],
    mk_materials: list[dict],
    country_tasks: list[dict],
    *,
    project_id: int,
    user_id: int | None,
    run_ai: bool,
) -> dict[str, Any]:
    """对单个产品×单个国家执行 LLM 评估。"""
    country_code = country_info.get("country_code", "")
    fallback = _fallback_country_review(product, country_info, country_summary, country_tasks)

    if not run_ai:
        return fallback

    review_input = _build_country_review_input(
        product, country_info, country_summary, country_materials, mk_materials, country_tasks,
    )

    try:
        result = llm_client.invoke_generate(
            COUNTRY_REVIEW_USE_CASE,
            prompt=_country_review_prompt(review_input, country_info),
            user_id=user_id,
            project_id=str(project_id),
            response_schema=COUNTRY_REVIEW_RESPONSE_SCHEMA,
            temperature=0.2,
            max_output_tokens=4096,
            provider_override=PROVIDER_CODE,
            model_override=MODEL_ID,
            billing_extra={
                "stage": "country_review",
                "product_id": product.get("product_id"),
                "country_code": country_code,
            },
            timeout_seconds=120,
        )
        parsed = _llm_json(result)
        if not parsed:
            fallback["ai_error"] = "empty model response"
            return fallback
        parsed["mode"] = "ai"
        parsed.setdefault("country_code", country_code)
        return parsed
    except Exception as exc:
        log.exception(
            "Country review failed product_id=%s country=%s",
            product.get("product_id"), country_code,
        )
        fallback["ai_error"] = str(exc)
        return fallback


def _build_market_expansion_recommendations(
    product: Mapping[str, Any],
    country_reviews: dict[str, dict],
    countries: list[dict],
) -> list[dict]:
    """基于各国评估结果，生成市场扩展建议（规则驱动，不需 LLM）。"""
    base_roas = _safe_float(product.get("effective_breakeven_roas"))

    # Classify countries by strength
    strong_countries: list[dict] = []
    weak_countries: list[dict] = []
    never_countries: list[dict] = []

    countries_by_code: dict[str, dict] = {
        _normalize_country_code(c.get("country_code"), lang=c.get("lang")): c
        for c in countries
    }

    for code, review in country_reviews.items():
        country_data = countries_by_code.get(code, {})
        delivery_status = country_data.get("delivery_status") or "never"
        score = _safe_int(review.get("quality_score"))
        decision = review.get("final_decision", "")

        if delivery_status == "never":
            never_countries.append({"code": code, "review": review, "data": country_data})
        elif decision == "通过" or score >= 70:
            strong_countries.append({"code": code, "review": review, "data": country_data})
        else:
            weak_countries.append({"code": code, "review": review, "data": country_data})

    recommendations: list[dict] = []

    # European cluster: DE, FR → IT, ES
    eu_strong = [c for c in strong_countries if c["code"] in {"DE", "FR", "IT", "ES"}]
    eu_never = [c for c in never_countries if c["code"] in {"DE", "FR", "IT", "ES"}]
    eu_weak = [c for c in weak_countries if c["code"] in {"DE", "FR", "IT", "ES"}]

    if eu_strong and (eu_never or eu_weak):
        strong_names = "、".join(
            (_TARGET_BY_COUNTRY.get(c["code"]) or {}).get("country_name", c["code"])
            for c in eu_strong
        )
        target_list = eu_never + eu_weak
        target_names = "、".join(
            (_TARGET_BY_COUNTRY.get(c["code"]) or {}).get("country_name", c["code"])
            for c in target_list
        )
        recommendations.append({
            "type": "eu_cluster_expansion",
            "source_countries": [c["code"] for c in eu_strong],
            "target_countries": [c["code"] for c in target_list],
            "priority": "P1" if len(eu_strong) >= 2 else "P2",
            "reason": f"{strong_names}表现强劲，建议将同素材扩展投放到{target_names}。欧洲市场用户偏好相近，跨国扩展成功率较高。",
        })

    # JP as independent market
    jp_review = country_reviews.get("JP")
    if jp_review:
        jp_data = countries_by_code.get("JP", {})
        jp_delivery = jp_data.get("delivery_status") or "never"
        global_spend = _safe_float(product.get("spend_30d"))

        if jp_delivery == "never" and global_spend >= 200 and _safe_float(product.get("true_roas_30d")) >= (base_roas or 1.0):
            recommendations.append({
                "type": "jp_market_entry",
                "source_countries": [c["code"] for c in strong_countries] or ["EN"],
                "target_countries": ["JP"],
                "priority": "P2",
                "reason": "产品全局表现强，日本市场尚未投放。日本市场独立性强，建议小规模测试。",
            })
        elif jp_delivery == "stopped" and global_spend >= 150:
            recommendations.append({
                "type": "jp_market_retest",
                "source_countries": [c["code"] for c in strong_countries] or ["EN"],
                "target_countries": ["JP"],
                "priority": "P3",
                "reason": "产品全局有量，日本市场曾投放已停。可用新素材重试日本市场。",
            })

    # All strong → recommend consolidation
    if len(strong_countries) >= 3 and not never_countries:
        recommendations.append({
            "type": "consolidation",
            "source_countries": [c["code"] for c in strong_countries],
            "target_countries": [],
            "priority": "P1",
            "reason": "多国投放均表现强劲，建议加大素材补充频率，保持各国投放势头。",
        })

    return recommendations

