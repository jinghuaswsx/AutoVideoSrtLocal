"""bulk_translate 父任务状态机 + 调度器 + 人工恢复。

核心铁律(开发全程必守):
  1. 绝不自动恢复任何 bulk_translate 任务 — 进程启动不扫描、不对账、不触发执行
  2. 子任务失败 → 父任务立即停(error),绝不跳过继续跑
  3. 所有恢复/重跑必须由用户按按钮触发(resume / retry_failed / retry_item)

模块划分:
  Task 16 (本文件): create / get / start 状态转换
  Task 17: run_scheduler 主循环 + 失败即停
  Task 18: 子任务派发器 _dispatch_sub_task + _translation_exists_for_item
  Task 19: SocketIO 事件推送(EVT_BT_PROGRESS / EVT_BT_DONE)
  Task 20: 人工恢复三路径 resume_task / retry_failed_items / retry_item

设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md 第 4 章
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from appcore.bulk_translate_estimator import estimate as do_estimate
from appcore.bulk_translate_plan import generate_plan
from appcore.db import execute, query_one

log = logging.getLogger(__name__)


# ============================================================
# 创建父任务(planning 状态)
# ============================================================
def create_bulk_translate_task(
    user_id: int,
    product_id: int,
    target_langs: list[str],
    content_types: list[str],
    force_retranslate: bool,
    video_params: dict,
    initiator: dict,
) -> str:
    """创建父任务,生成 plan + 费用预估 + 审计初始记录。

    初始 status='planning',尚未启动调度器——等用户二次确认后调 start_task。

    返回 task_id(UUID)。
    """
    plan = generate_plan(user_id, product_id, target_langs,
                          content_types, force_retranslate)
    cost = do_estimate(user_id, product_id, target_langs,
                         content_types, force_retranslate)

    state = {
        "product_id": product_id,
        "source_lang": "en",
        "target_langs": target_langs,
        "content_types": content_types,
        "force_retranslate": force_retranslate,
        "video_params_snapshot": video_params or {},
        "initiator": initiator,
        "plan": plan,
        "progress": compute_progress(plan),
        "current_idx": 0,
        "cancel_requested": False,
        "audit_events": [_audit(user_id, "create", {
            "target_langs": target_langs,
            "content_types": content_types,
            "force": force_retranslate,
            "estimated_cost_cny": cost["estimated_cost_cny"],
        })],
        "cost_tracking": {
            "estimate": {
                "copy_tokens": cost["copy_tokens"],
                "image_count": cost["image_count"],
                "video_minutes": cost["video_minutes"],
                "estimated_cost_cny": cost["estimated_cost_cny"],
            },
            "actual": {
                "copy_tokens_used": 0,
                "image_processed": 0,
                "video_minutes_processed": 0,
                "actual_cost_cny": 0.0,
            },
        },
    }

    task_id = str(uuid.uuid4())
    execute(
        """
        INSERT INTO projects (id, user_id, type, status, state_json)
        VALUES (%s, %s, 'bulk_translate', 'planning', %s)
        """,
        (task_id, user_id,
         json.dumps(state, ensure_ascii=False, default=str)),
    )
    log.info(
        "bulk_translate task created task_id=%s product=%s langs=%s",
        task_id, product_id, target_langs,
    )
    return task_id


# ============================================================
# 读取父任务(全部状态一次性读出)
# ============================================================
def get_task(task_id: str) -> dict | None:
    """返回 { id, user_id, status, state, created_at, updated_at } 或 None。

    调度器/API 层都用这个统一入口。
    """
    row = query_one(
        "SELECT id, user_id, status, state_json, created_at, updated_at "
        "FROM projects WHERE id = %s AND type = 'bulk_translate'",
        (task_id,),
    )
    if not row:
        return None
    raw = row["state_json"]
    state = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "status": row["status"],
        "state": state,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ============================================================
# 启动父任务(planning → running)
# ============================================================
def start_task(task_id: str, user_id: int) -> None:
    """把 planning 状态改为 running,追加 audit_events。

    注意: 本函数只做状态转换,不启动调度器。
    调度器由路由层在请求返回后 eventlet.spawn(run_scheduler, task_id)。
    """
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    if task["status"] != "planning":
        raise ValueError(
            f"Cannot start task in status={task['status']}, must be 'planning'"
        )
    state = task["state"]
    _append_audit(state, user_id, "start")
    _save_state(task_id, state, status="running")


# ============================================================
# 内部工具
# ============================================================
def compute_progress(plan: list[dict]) -> dict:
    """基于 plan 项 status 聚合进度。注意状态值到统计 key 的映射:
    pending / running / done / skipped / failed(plan 项的 'error' 在进度里算 'failed')。
    """
    progress = {"total": len(plan), "done": 0, "running": 0,
                "failed": 0, "skipped": 0, "pending": 0}
    for item in plan:
        st = item["status"]
        if st == "pending":
            progress["pending"] += 1
        elif st == "running":
            progress["running"] += 1
        elif st == "done":
            progress["done"] += 1
        elif st == "skipped":
            progress["skipped"] += 1
        elif st == "error":
            progress["failed"] += 1
    return progress


def _save_state(task_id: str, state: dict, status: str | None = None) -> None:
    """把 state 回写 projects.state_json,可选同时改 status。"""
    state["progress"] = compute_progress(state["plan"])
    payload = json.dumps(state, ensure_ascii=False, default=str)
    if status is not None:
        execute(
            "UPDATE projects SET state_json=%s, status=%s WHERE id=%s",
            (payload, status, task_id),
        )
    else:
        execute(
            "UPDATE projects SET state_json=%s WHERE id=%s",
            (payload, task_id),
        )


def _audit(user_id: int, action: str, detail: dict | None = None) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "action": action,
        "detail": detail or {},
    }


def _append_audit(state: dict, user_id: int, action: str,
                   detail: dict | None = None) -> None:
    state.setdefault("audit_events", []).append(_audit(user_id, action, detail))
