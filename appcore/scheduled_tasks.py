from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from appcore.db import execute, query

log = logging.getLogger(__name__)

TaskDefinition = dict[str, Any]

TASK_DEFINITIONS: dict[str, TaskDefinition] = {
    "shopifyid": {
        "code": "shopifyid",
        "name": "Shopify ID 获取",
        "description": "每天从店小秘 Shopify 在线商品库抓取 shopifyProductId，并回填 media_products.shopifyid。",
        "schedule": "每天 12:10",
        "source_type": "systemd",
        "source_label": "Linux systemd timer",
        "source_ref": "autovideosrt-shopifyid-sync.timer",
        "runner": "tools/shopifyid_dianxiaomi_sync.py",
        "deployment": "线上已启用",
        "log_table": "scheduled_task_runs",
    },
    "shopifyid_windows_daily": {
        "code": "shopifyid_windows_daily",
        "name": "Shopify ID 获取（Windows 本机）",
        "description": "Windows 计划任务每天触发店小秘 Shopify ID 同步脚本，作为本机运行入口登记。",
        "schedule": "每天 12:10",
        "source_type": "windows",
        "source_label": "Windows 计划任务",
        "source_ref": "AutoVideoSrtLocal-ShopifyIdDianxiaomiSyncDaily",
        "runner": "tools/shopifyid_dianxiaomi_sync_daily.ps1",
        "deployment": "本机运维任务",
        "log_table": "",
        "output_file": "output/shopifyid_dianxiaomi_sync/",
    },
    "roi_hourly_sync": {
        "code": "roi_hourly_sync",
        "name": "店小秘订单与 ROAS 实时同步",
        "description": "每 20 分钟同步店小秘订单、Meta 广告数据，并刷新真实 ROAS 小时事实与日内快照。",
        "schedule": "每 20 分钟",
        "source_type": "systemd",
        "source_label": "Linux systemd timer",
        "source_ref": "autovideosrt-roi-realtime-sync.timer",
        "runner": "tools/roi_hourly_sync.py",
        "deployment": "线上已启用",
        "log_table": "roi_hourly_sync_runs",
    },
    "dianxiaomi_order_import": {
        "code": "dianxiaomi_order_import",
        "name": "店小秘订单导入",
        "description": "ROI 实时同步中的店小秘订单导入子任务，记录订单抓取、明细入库和跳过数量。",
        "schedule": "每 20 分钟（随 ROI 实时同步触发）",
        "source_type": "subtask",
        "source_label": "ROI 同步子任务",
        "source_ref": "autovideosrt-roi-realtime-sync.timer",
        "runner": "tools/dianxiaomi_order_import.py（由 tools/roi_hourly_sync.py 调用）",
        "deployment": "线上已启用",
        "log_table": "dianxiaomi_order_import_batches",
    },
    "meta_realtime_import": {
        "code": "meta_realtime_import",
        "name": "Meta 实时广告导入",
        "description": "ROI 实时同步中的 Meta 实时广告导入子任务，记录导入行数、消耗金额和跳过状态。",
        "schedule": "每 20 分钟（随 ROI 实时同步触发）",
        "source_type": "subtask",
        "source_label": "ROI 同步子任务",
        "source_ref": "autovideosrt-roi-realtime-sync.timer",
        "runner": "tools/roi_hourly_sync.py::_sync_meta_realtime_daily",
        "deployment": "线上已启用",
        "log_table": "meta_ad_realtime_import_runs",
    },
    "meta_daily_final": {
        "code": "meta_daily_final",
        "name": "Meta 收盘日数据",
        "description": "每天北京时间 16:30 抓取刚收盘的 Meta 广告整日数据，17:00 做成功检测和补跑。",
        "schedule": "每天 16:30 同步；17:00 检查补跑",
        "source_type": "systemd",
        "source_label": "Linux systemd timer",
        "source_ref": "autovideosrt-meta-daily-final-sync.timer / autovideosrt-meta-daily-final-check.timer",
        "runner": "tools/meta_daily_final_sync.py",
        "deployment": "线上已启用",
        "log_table": "scheduled_task_runs",
    },
    "product_cover_backfill_tick": {
        "code": "product_cover_backfill_tick",
        "name": "商品组图回填",
        "description": "轮询缺少商品主图的商品，访问商品详情页并用详情轮播第一张图回填主图。",
        "schedule": "每 10 分钟",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "product_cover_backfill_tick",
        "runner": "appcore.product_cover_backfill_scheduler.tick_once",
        "deployment": "Web 服务启动时注册",
        "log_table": "",
    },
    "material_evaluation_tick": {
        "code": "material_evaluation_tick",
        "name": "AI 素材评估",
        "description": "扫描已满足条件但尚未评估的商品素材，批量触发 AI 评估。",
        "schedule": "每 5 分钟",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "material_evaluation_tick",
        "runner": "appcore.material_evaluation_scheduler.tick_once",
        "deployment": "Web 服务启动时注册",
        "log_table": "",
    },
    "push_quality_check_tick": {
        "code": "push_quality_check_tick",
        "name": "推送内容质量检查",
        "description": "扫描推送管理里待推送或重推且已就绪的非英语素材，对小语种文案、封面图、视频前 5 秒做一次大模型质量检查。",
        "schedule": "每 10 分钟",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "push_quality_check_tick",
        "runner": "appcore.push_quality_check_scheduler.tick_once",
        "deployment": "Web 服务启动时注册",
        "log_table": "scheduled_task_runs",
    },
    "tos_backup": {
        "code": "tos_backup",
        "name": "TOS 文件与数据库备份",
        "description": "每天凌晨同步受保护文件到 autovideosrtlocal 桶，并保留 7 天 MySQL dump。",
        "schedule": "每天 02:00",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "tos_backup",
        "runner": "appcore.tos_backup_job.run_scheduled_backup",
        "deployment": "Web 服务启动时注册",
        "log_table": "scheduled_task_runs",
    },
    "subtitle_removal_vod_tick": {
        "code": "subtitle_removal_vod_tick",
        "name": "字幕移除 VOD 接力",
        "description": "当字幕移除 provider 为 VOD 时，持续轮询擦除任务状态并回填结果播放地址。",
        "schedule": "每 60 秒",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "subtitle_removal_vod_tick",
        "runner": "appcore.subtitle_removal_vod_scheduler.tick_once",
        "deployment": "Web 服务启动时注册",
        "log_table": "",
    },
    "cleanup": {
        "code": "cleanup",
        "name": "临时文件清理",
        "description": "定期清理系统运行过程中产生的过期临时文件和中间产物。",
        "schedule": "每小时",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "cleanup",
        "runner": "appcore.cleanup.run_cleanup",
        "deployment": "Web 服务启动时注册",
        "log_table": "",
    },
    "medias_detail_fetch_cleanup": {
        "code": "medias_detail_fetch_cleanup",
        "name": "素材详情抓取任务清理",
        "description": "进程内维护任务，每 60 秒清理过期的素材详情抓取任务状态。",
        "schedule": "每 60 秒",
        "source_type": "in_process",
        "source_label": "进程内维护任务",
        "source_ref": "mdf-cleanup",
        "runner": "appcore.medias_detail_fetch_tasks._cleanup_loop",
        "deployment": "模块导入后后台线程启动",
        "log_table": "",
    },
    "voice_match_cleanup": {
        "code": "voice_match_cleanup",
        "name": "音色匹配任务清理",
        "description": "进程内维护任务，每 60 秒清理过期的音色匹配任务状态和临时文件。",
        "schedule": "每 60 秒",
        "source_type": "in_process",
        "source_label": "进程内维护任务",
        "source_ref": "vmt-cleanup",
        "runner": "appcore.voice_match_tasks._cleanup_loop",
        "deployment": "模块导入后后台线程启动",
        "log_table": "",
    },
    "tts_convergence_stats": {
        "code": "tts_convergence_stats",
        "name": "TTS 收敛统计",
        "description": "服务器 root crontab 每小时生成 TTS 收敛统计日志，用于排查配音收敛情况。",
        "schedule": "每小时整点",
        "source_type": "cron",
        "source_label": "Linux root crontab",
        "source_ref": "0 * * * *",
        "runner": "tools/tts_convergence_stats.py",
        "deployment": "线上 crontab 已启用",
        "log_table": "",
        "output_file": "/var/log/tts_convergence.log",
    },
    "meta_realtime_local_sync": {
        "code": "meta_realtime_local_sync",
        "name": "Meta 本地 ADS Power 实时导出",
        "description": "Windows 计划任务或本地守护进程每 20 分钟从 ADS Power 90 导出 Meta 实时广告数据，并上传到服务器导入。",
        "schedule": "每 20 分钟（00/20/40）",
        "source_type": "windows",
        "source_label": "Windows 计划任务 / 本地 daemon",
        "source_ref": "AutoVideoSrt Meta Realtime Local Sync",
        "runner": "tools/meta_realtime_local_sync.py / tools/meta_realtime_local_daemon.py",
        "deployment": "本地运维任务",
        "log_table": "",
        "output_file": "scratch/meta_realtime_local/logs/",
    },
}

_RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_task_runs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  task_code VARCHAR(64) NOT NULL,
  task_name VARCHAR(120) NOT NULL,
  status ENUM('running', 'success', 'failed') NOT NULL DEFAULT 'running',
  scheduled_for DATETIME NULL,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME NULL,
  duration_seconds INT UNSIGNED NULL,
  summary_json JSON NULL,
  error_message MEDIUMTEXT NULL,
  output_file VARCHAR(512) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_scheduled_task_runs_task_started (task_code, started_at),
  KEY idx_scheduled_task_runs_status_started (status, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def task_definitions() -> list[TaskDefinition]:
    return [dict(item) for item in TASK_DEFINITIONS.values()]


def log_filter_definitions() -> list[TaskDefinition]:
    return [
        {
            "code": "all",
            "name": "全部日志",
            "description": "汇总所有已接入运行表的定时任务日志。",
            "schedule": "全部",
        },
        *task_definitions(),
    ]


def management_tasks() -> list[TaskDefinition]:
    return task_definitions()


def get_task_definition(task_code: str) -> TaskDefinition:
    code = (task_code or "").strip()
    if code in TASK_DEFINITIONS:
        return dict(TASK_DEFINITIONS[code])
    return {
        "code": code or "unknown",
        "name": code or "未知任务",
        "description": "未登记的定时任务。",
        "schedule": "-",
        "source_type": "unknown",
        "source_label": "未登记",
        "source_ref": "-",
        "runner": "-",
        "deployment": "未登记",
        "log_table": "",
    }


def is_known_task(task_code: str) -> bool:
    return (task_code or "").strip() in TASK_DEFINITIONS


def ensure_runs_table() -> None:
    execute(_RUNS_TABLE_SQL)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def start_run(task_code: str, *, scheduled_for: datetime | None = None) -> int:
    ensure_runs_table()
    task = get_task_definition(task_code)
    return int(execute(
        "INSERT INTO scheduled_task_runs "
        "(task_code, task_name, status, scheduled_for, started_at) "
        "VALUES (%s, %s, 'running', %s, NOW())",
        (task["code"], task["name"], scheduled_for),
    ))


def finish_run(
    run_id: int,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    error_message: str | None = None,
    output_file: str | None = None,
) -> None:
    summary_json = (
        json.dumps(summary, ensure_ascii=False, default=_json_default)
        if summary is not None
        else None
    )
    execute(
        "UPDATE scheduled_task_runs SET status=%s, finished_at=NOW(), "
        "duration_seconds=TIMESTAMPDIFF(SECOND, started_at, NOW()), "
        "summary_json=%s, error_message=%s, output_file=%s "
        "WHERE id=%s",
        (status, summary_json, error_message, output_file, int(run_id)),
    )


def record_failure(
    task_code: str,
    *,
    error_message: str,
    summary: dict[str, Any] | None = None,
    output_file: str | None = None,
) -> int:
    run_id = start_run(task_code)
    finish_run(
        run_id,
        status="failed",
        summary=summary,
        error_message=error_message,
        output_file=output_file,
    )
    return run_id


def _decode_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}


def _decode_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _normalize_row(
    row: dict[str, Any] | None,
    *,
    task_code: str | None = None,
    task_name: str | None = None,
    summary_fields: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    if task_code and not item.get("task_code"):
        item["task_code"] = task_code
    if task_name and not item.get("task_name"):
        item["task_name"] = task_name
    summary = _decode_summary(item.pop("summary_json", None))
    for field in summary_fields:
        value = item.get(field)
        if value is not None and value != "":
            summary.setdefault(field, _decode_json_value(value))
    item["summary"] = summary
    return item


def _safe_query_rows(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    try:
        return query(sql, params)
    except Exception:
        log.warning("failed to load scheduled task runs", exc_info=True)
        return []


def _scheduled_task_runs(task_code: str, *, limit: int) -> list[dict[str, Any]]:
    if task_code == "all":
        rows = _safe_query_rows(
            """
            SELECT id, task_code, task_name, status, scheduled_for, started_at, finished_at,
                   duration_seconds, summary_json, error_message, output_file
            FROM scheduled_task_runs
            ORDER BY started_at DESC, id DESC
            LIMIT %s
            """,
            (limit,),
        )
    else:
        rows = _safe_query_rows(
            """
            SELECT id, task_code, task_name, status, scheduled_for, started_at, finished_at,
                   duration_seconds, summary_json, error_message, output_file
            FROM scheduled_task_runs
            WHERE task_code = %s
            ORDER BY started_at DESC, id DESC
            LIMIT %s
            """,
            (task_code, limit),
        )
    return [_normalize_row(row) for row in rows if row]


def _roi_hourly_runs(*, limit: int) -> list[dict[str, Any]]:
    task = TASK_DEFINITIONS["roi_hourly_sync"]
    rows = _safe_query_rows(
        """
        SELECT id, task_code, status, NULL AS scheduled_for,
               sync_started_at AS started_at, sync_finished_at AS finished_at,
               duration_seconds, summary_json, error_message, NULL AS output_file
        FROM roi_hourly_sync_runs
        ORDER BY sync_started_at DESC, id DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [
        _normalize_row(row, task_code=task["code"], task_name=task["name"])
        for row in rows
        if row
    ]


def _dianxiaomi_order_import_runs(*, limit: int) -> list[dict[str, Any]]:
    task = TASK_DEFINITIONS["dianxiaomi_order_import"]
    rows = _safe_query_rows(
        """
        SELECT id, status, NULL AS scheduled_for,
               started_at, finished_at, duration_seconds, summary_json,
               error_message, NULL AS output_file, date_from, date_to,
               total_pages, fetched_orders, fetched_lines, inserted_lines,
               updated_lines, skipped_lines, included_shopify_ids_count
        FROM dianxiaomi_order_import_batches
        ORDER BY started_at DESC, id DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [
        _normalize_row(
            row,
            task_code=task["code"],
            task_name=task["name"],
            summary_fields=(
                "date_from",
                "date_to",
                "total_pages",
                "fetched_orders",
                "fetched_lines",
                "inserted_lines",
                "updated_lines",
                "skipped_lines",
                "included_shopify_ids_count",
            ),
        )
        for row in rows
        if row
    ]


def _meta_realtime_import_runs(*, limit: int) -> list[dict[str, Any]]:
    task = TASK_DEFINITIONS["meta_realtime_import"]
    rows = _safe_query_rows(
        """
        SELECT id, status, NULL AS scheduled_for,
               started_at, finished_at, duration_seconds, summary_json,
               error_message, NULL AS output_file, business_date, snapshot_at,
               ad_account_ids, rows_imported, spend_usd
        FROM meta_ad_realtime_import_runs
        ORDER BY started_at DESC, id DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [
        _normalize_row(
            row,
            task_code=task["code"],
            task_name=task["name"],
            summary_fields=(
                "business_date",
                "snapshot_at",
                "ad_account_ids",
                "rows_imported",
                "spend_usd",
            ),
        )
        for row in rows
        if row
    ]


def _sort_runs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda item: (str(item.get("started_at") or ""), int(item.get("id") or 0)),
        reverse=True,
    )


def list_runs(task_code: str = "all", *, limit: int = 60) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 200))
    code = (task_code or "all").strip() or "all"

    if code == "all":
        rows: list[dict[str, Any]] = []
        rows.extend(_scheduled_task_runs("all", limit=safe_limit))
        rows.extend(_roi_hourly_runs(limit=safe_limit))
        rows.extend(_dianxiaomi_order_import_runs(limit=safe_limit))
        rows.extend(_meta_realtime_import_runs(limit=safe_limit))
        return _sort_runs(rows)[:safe_limit]

    task = TASK_DEFINITIONS.get(code)
    if not task:
        return []
    if task.get("log_table") == "scheduled_task_runs":
        return _scheduled_task_runs(code, limit=safe_limit)
    if task.get("log_table") == "roi_hourly_sync_runs":
        return _roi_hourly_runs(limit=safe_limit)
    if task.get("log_table") == "dianxiaomi_order_import_batches":
        return _dianxiaomi_order_import_runs(limit=safe_limit)
    if task.get("log_table") == "meta_ad_realtime_import_runs":
        return _meta_realtime_import_runs(limit=safe_limit)
    return []


def latest_run(task_code: str = "all") -> dict[str, Any] | None:
    rows = list_runs(task_code, limit=1)
    return rows[0] if rows else None


def latest_failure_alert() -> dict[str, Any] | None:
    """Return the latest failed run only if it is still the latest run for that task."""
    for task in task_definitions():
        if not task.get("log_table"):
            continue
        try:
            row = latest_run(task["code"])
        except Exception:
            log.warning("failed to load scheduled task alert", exc_info=True)
            continue
        if row and row.get("status") == "failed":
            return row
    return None
