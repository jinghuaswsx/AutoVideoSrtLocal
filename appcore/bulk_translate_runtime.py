"""bulk_translate 父任务编排。

新版职责：
- 生成父任务计划并持久化
- 串行创建子任务（支持延迟派发）
- 轮询子任务状态并回填结果
- 在视频任务进入选音色时停在 waiting_manual
- 所有恢复/重跑都必须手工触发
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from appcore import local_media_storage, medias
from appcore.bulk_translate_backfill import (
    sync_detail_images_result,
    sync_video_cover_result,
    sync_video_result,
)
from appcore.bulk_translate_estimator import (
    COST_PER_1K_TOKENS_CNY,
    COST_PER_IMAGE_CNY,
    COST_PER_VIDEO_MINUTE_CNY,
    estimate as do_estimate,
)
from appcore.bulk_translate_plan import generate_plan
from appcore.db import execute, query, query_one
from appcore.events import EVT_BT_DONE, EVT_BT_PROGRESS, Event, EventBus
from config import OUTPUT_DIR, UPLOAD_DIR

log = logging.getLogger(__name__)

_RUNNING_PARENT_STATUSES = {"running", "waiting_manual"}
_FAILURE_CHILD_STATUSES = {"error", "failed", "cancelled", "interrupted"}
_ACTIVE_ITEM_STATUSES = {"dispatching", "running", "syncing_result", "awaiting_voice"}
_RETRYABLE_ITEM_STATUSES = {"failed", "error", "interrupted"}
_REFRESHABLE_ITEM_STATUSES = _ACTIVE_ITEM_STATUSES | _RETRYABLE_ITEM_STATUSES
_RUNNING_ITEM_STATUSES = {"dispatching", "running", "syncing_result"}
_MULTI_TRANSLATE_SUPPORTED_LANGS = {"de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi"}


def _download_media_source_to(object_key: str, destination: str) -> str:
    """Materialize a medias raw-source object into a local pipeline file."""
    key = (object_key or "").strip()
    if not key:
        raise ValueError("raw source video object key missing")
    try:
        return local_media_storage.download_to(key, destination)
    except Exception as exc:
        raise RuntimeError(f"raw source video not available locally: {key}") from exc


def create_bulk_translate_task(
    user_id: int,
    product_id: int,
    target_langs: list[str],
    content_types: list[str],
    force_retranslate: bool,
    video_params: dict,
    initiator: dict,
    raw_source_ids: list[int] | None = None,
) -> str:
    plan = generate_plan(
        user_id,
        product_id,
        target_langs,
        content_types,
        force_retranslate,
        raw_source_ids=raw_source_ids,
    )
    state = {
        "product_id": product_id,
        "source_lang": "en",
        "target_langs": list(target_langs),
        "content_types": list(content_types),
        "force_retranslate": bool(force_retranslate),
        "raw_source_ids": list(raw_source_ids or []),
        "video_params_snapshot": dict(video_params or {}),
        "initiator": dict(initiator or {}),
        "plan": [_normalize_item(item) for item in plan],
        "progress": compute_progress(plan),
        "current_idx": None,
        "cancel_requested": False,
        "scheduler_anchor_ts": None,
        "audit_events": [
            _audit(
                user_id,
                "create",
                {
                    "target_langs": list(target_langs),
                    "content_types": list(content_types),
                    "force": bool(force_retranslate),
                },
            )
        ],
        "cost_tracking": {
            "actual": {
                "copy_tokens_used": 0,
                "image_processed": 0,
                "video_minutes_processed": 0.0,
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
        (task_id, user_id, json.dumps(state, ensure_ascii=False, default=str)),
    )
    return task_id


def get_task(task_id: str) -> dict | None:
    row = query_one(
        "SELECT id, user_id, status, state_json, created_at "
        "FROM projects WHERE id = %s AND type = 'bulk_translate'",
        (task_id,),
    )
    if not row:
        return None
    raw_state = row.get("state_json")
    state = raw_state if isinstance(raw_state, dict) else json.loads(raw_state or "{}")
    return {
        "id": row.get("id"),
        "user_id": row.get("user_id"),
        "status": row.get("status"),
        "state": state,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at") or row.get("created_at"),
    }


def start_task(task_id: str, user_id: int) -> None:
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    if task["status"] != "planning":
        raise ValueError(f"Cannot start task in status={task['status']}, must be 'planning'")
    state = task["state"]
    state["scheduler_anchor_ts"] = None
    _append_audit(state, user_id, "start")
    _save_state(task_id, state, status="running")


def compute_progress(plan: list[dict]) -> dict:
    progress = {
        "total": len(plan),
        "pending": 0,
        "dispatching": 0,
        "running": 0,
        "syncing_result": 0,
        "awaiting_voice": 0,
        "failed": 0,
        "interrupted": 0,
        "done": 0,
        "skipped": 0,
    }
    for raw_item in plan:
        status = _normalized_status(raw_item.get("status"))
        if status not in progress:
            status = "pending"
        progress[status] += 1
    return progress


def run_scheduler(
    task_id: str,
    bus: EventBus | None = None,
    *,
    now_provider=time.time,
    sleep_fn=time.sleep,
    max_loops: int | None = None,
) -> None:
    loops = 0
    while max_loops is None or loops < max_loops:
        loops += 1
        task = get_task(task_id)
        if not task:
            return

        status = task["status"]
        state = task["state"]
        plan = [_normalize_item(item) for item in state.get("plan") or []]
        state["plan"] = plan

        if state.get("cancel_requested"):
            _save_state(task_id, state, status="cancelled")
            _emit(bus, EVT_BT_PROGRESS, task_id, state, "cancelled")
            return

        if status not in _RUNNING_PARENT_STATUSES:
            return

        if state.get("scheduler_anchor_ts") is None:
            state["scheduler_anchor_ts"] = now_provider()
            _save_state(task_id, state, status="running")

        for active_item in _find_active_items(plan):
            _poll_active_item(
                task_id,
                active_item,
                state,
                bus=bus,
            )

        due_item = _next_due_pending_item(state, now_provider())
        if due_item:
            child_task_id, child_task_type, _child_status = _create_child_task(
                task_id,
                due_item,
                state,
            )
            due_item["child_task_id"] = child_task_id
            due_item["sub_task_id"] = child_task_id
            due_item["child_task_type"] = child_task_type
            due_item["status"] = "dispatching"
            due_item["started_at"] = due_item.get("started_at") or _now_iso()
            state["current_idx"] = due_item["idx"]
            _save_state(task_id, state, status="running")
            _emit(bus, EVT_BT_PROGRESS, task_id, state, "running")
            continue

        if any(_normalized_status(item.get("status")) == "pending" for item in plan):
            sleep_fn(1)
            continue

        if any(_normalized_status(item.get("status")) in _RUNNING_ITEM_STATUSES for item in plan):
            sleep_fn(1)
            continue

        if any(_normalized_status(item.get("status")) in {"failed", "error", "interrupted"} for item in plan):
            _save_state(task_id, state, status="failed")
            _emit(bus, EVT_BT_PROGRESS, task_id, state, "failed")
            return

        if any(_normalized_status(item.get("status")) in {"awaiting_voice"} for item in plan):
            _save_state(task_id, state, status="waiting_manual")
            _emit(bus, EVT_BT_PROGRESS, task_id, state, "waiting_manual")
            return

        _save_state(task_id, state, status="done")
        _emit(bus, EVT_BT_DONE, task_id, state, "done")
        return


def pause_task(task_id: str, user_id: int) -> None:
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    state = task["state"]
    _append_audit(state, user_id, "pause")
    _save_state(task_id, state, status="paused")


def cancel_task(task_id: str, user_id: int) -> None:
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    state = task["state"]
    state["cancel_requested"] = True
    _append_audit(state, user_id, "cancel")
    _save_state(task_id, state)


def resume_task(task_id: str, user_id: int) -> None:
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    state = task["state"]
    for item in state.get("plan") or []:
        if _normalized_status(item.get("status")) == "interrupted":
            _reset_item_for_retry(item)
    state["cancel_requested"] = False
    state["scheduler_anchor_ts"] = None
    _append_audit(state, user_id, "resume")
    _save_state(task_id, state, status="running")


def retry_failed_items(task_id: str, user_id: int) -> None:
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    state = task["state"]
    reset_count = 0
    for item in state.get("plan") or []:
        if _normalized_status(item.get("status")) in {"failed", "interrupted"}:
            if _retry_existing_image_child_if_possible(task_id, item, state, user_id):
                reset_count += 1
                continue
            _reset_item_for_retry(item)
            reset_count += 1
    state["cancel_requested"] = False
    state["scheduler_anchor_ts"] = None
    _append_audit(state, user_id, "retry_failed", {"reset_count": reset_count})
    _save_state(task_id, state, status=_derive_parent_status(state.get("plan") or [], "running"))


def retry_item(task_id: str, idx: int, user_id: int) -> None:
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    state = task["state"]
    plan = state.get("plan") or []
    if idx < 0 or idx >= len(plan):
        raise ValueError(f"Invalid idx={idx}, plan has {len(plan)} items")
    item_status = _normalized_status(plan[idx].get("status"))
    reused_image_child = (
        item_status in {"failed", "interrupted"}
        and _retry_existing_image_child_if_possible(task_id, plan[idx], state, user_id)
    )
    if not reused_image_child:
        _reset_item_for_retry(plan[idx])
    state["cancel_requested"] = False
    state["scheduler_anchor_ts"] = None
    _append_audit(state, user_id, "retry_item", {"idx": idx})
    _save_state(task_id, state, status=_derive_parent_status(state.get("plan") or [], "running"))


def force_backfill_item(task_id: str, idx: int, user_id: int) -> None:
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")

    state = task["state"]
    plan = [_normalize_item(item) for item in state.get("plan") or []]
    state["plan"] = plan
    if idx < 0 or idx >= len(plan):
        raise ValueError(f"Invalid idx={idx}, plan has {len(plan)} items")

    item = plan[idx]
    _validate_force_backfill_target(item)
    child_state = _load_child_snapshot(item.get("child_task_type"), item.get("child_task_id"))
    if not child_state:
        raise ValueError("image translate child task not found")

    result = _force_backfill_detail_image_child(item, child_state, user_id)
    _mark_item_force_backfilled(item, result)
    _roll_up_cost_once_for_item(state, item, child_state)
    _append_audit(
        state,
        user_id,
        "force_backfill_item",
        {
            "idx": idx,
            "child_task_id": item.get("child_task_id"),
            "applied_count": int(item.get("forced_backfill_applied_count") or 0),
            "skipped_failed_count": int(item.get("forced_backfill_skipped_failed_count") or 0),
            "apply_status": item.get("forced_backfill_apply_status") or "",
        },
    )
    _save_state(task_id, state, status=_derive_parent_status(plan, task.get("status") or "running"))


def refresh_task_from_children(task_id: str, user_id: int | None = None) -> dict | None:
    """Poll existing child tasks and sync recovered results without creating new children."""
    task = get_task(task_id)
    if not task:
        return None
    if user_id is not None and int(task.get("user_id") or 0) != int(user_id):
        raise ValueError(f"Task {task_id} not found")

    state = task["state"]
    plan = [_normalize_item(item) for item in state.get("plan") or []]
    state["plan"] = plan
    changed = False

    for item in plan:
        if not _should_refresh_from_child(item):
            continue
        child_state = _load_child_snapshot(item.get("child_task_type"), item.get("child_task_id"))
        if not child_state:
            continue
        before = json.dumps(item, ensure_ascii=False, default=str, sort_keys=True)
        _apply_child_snapshot(task_id, item, state, child_state, bus=None)
        after = json.dumps(item, ensure_ascii=False, default=str, sort_keys=True)
        changed = changed or before != after

    if changed:
        final_status = _derive_parent_status(plan, task.get("status") or "")
        _save_state(task_id, state, status=final_status)
        return get_task(task_id)
    return task


def sync_task_with_children_once(
    task_id: str,
    user_id: int | None = None,
) -> dict:
    """Poll child tasks and sync terminal state without starting new work."""
    task = get_task(task_id)
    if not task:
        return {"actions": [], "status": "missing"}
    if user_id is not None and int(task.get("user_id") or 0) != int(user_id):
        raise ValueError("Forbidden")
    if _normalized_status(task.get("status")) in {"done", "cancelled"}:
        return {"actions": [], "status": task.get("status")}

    state = task["state"]
    plan = [_normalize_item(item) for item in state.get("plan") or []]
    state["plan"] = plan
    actions: list[str] = []

    for item in plan:
        if _normalized_status(item.get("status")) == "interrupted":
            continue
        child_task_id = (item.get("child_task_id") or "").strip()
        child_task_type = (item.get("child_task_type") or "").strip()
        if not child_task_id or not child_task_type:
            continue
        child_state = _load_child_snapshot(child_task_type, child_task_id)
        if not child_state:
            continue

        child_project_status = _normalized_status(child_state.get("_project_status"))

        if (
            child_task_type == "image_translate"
            and child_project_status == "done"
            and _image_child_failed_items(child_state)
        ):
            item["status"] = "failed"
            item["error"] = _first_child_error(child_state) or "image_translate failed"
            item["finished_at"] = item.get("finished_at") or _now_iso()
            continue

        if (
            child_project_status in _FAILURE_CHILD_STATUSES
            and _normalized_status(item.get("status")) != "interrupted"
        ):
            item["status"] = "failed"
            item["error"] = _child_failure_error(child_state, child_project_status)
            item["finished_at"] = item.get("finished_at") or _now_iso()
            continue

        if (
            child_project_status == "done"
            and _normalized_status(item.get("status")) != "done"
        ):
            parent_status = _poll_active_item(task_id, item, state, bus=None)
            if _normalized_status(item.get("status")) == "done":
                actions.append("sync_child_result")
            if parent_status in {"failed", "error"}:
                break

    final_status = None
    if plan and all(_normalized_status(item.get("status")) in {"done", "skipped"} for item in plan):
        final_status = "done"
        if "finish_parent" not in actions:
            actions.append("finish_parent")
    elif any(_normalized_status(item.get("status")) == "failed" for item in plan):
        final_status = "failed"
    elif any(_normalized_status(item.get("status")) == "awaiting_voice" for item in plan):
        final_status = "waiting_manual"
    elif actions:
        final_status = "running"

    if final_status is not None:
        _save_state(task_id, state, status=final_status)
    else:
        _save_state(task_id, state)
    return {"actions": actions, "status": actions[-1] if actions else "synced"}


def _poll_active_item(
    parent_task_id: str,
    item: dict,
    parent_state: dict,
    *,
    bus: EventBus | None,
) -> None:
    child_state = _load_child_snapshot(item.get("child_task_type"), item.get("child_task_id"))
    return _apply_child_snapshot(parent_task_id, item, parent_state, child_state, bus=bus)


def _apply_child_snapshot(
    parent_task_id: str,
    item: dict,
    parent_state: dict,
    child_state: dict,
    *,
    bus: EventBus | None,
) -> None:
    child_status = _normalized_status(child_state.get("_project_status"))

    if _child_needs_voice(child_state):
        item["status"] = "awaiting_voice"
        _save_state(parent_task_id, parent_state, status="running")
        _emit(bus, EVT_BT_PROGRESS, parent_task_id, parent_state, "running")
        return

    if child_status == "done":
        completed_child_error = _get_completed_child_error(item, child_state)
        if completed_child_error:
            item["status"] = "failed"
            item["error"] = completed_child_error
            item["finished_at"] = _now_iso()
            _save_state(parent_task_id, parent_state, status="running")
            _emit(bus, EVT_BT_PROGRESS, parent_task_id, parent_state, "running")
            return
        item["status"] = "syncing_result"
        _save_state(parent_task_id, parent_state, status="running")
        try:
            _sync_child_result(parent_task_id, item, parent_state, child_state)
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = str(exc)
            item["finished_at"] = _now_iso()
            _save_state(parent_task_id, parent_state, status="running")
            _emit(bus, EVT_BT_PROGRESS, parent_task_id, parent_state, "running")
            return
        item["result_synced"] = True
        item["status"] = "done"
        item["error"] = None
        item["finished_at"] = _now_iso()
        _roll_up_cost(parent_state, item, child_state)
        _save_state(parent_task_id, parent_state, status="running")
        _emit(bus, EVT_BT_PROGRESS, parent_task_id, parent_state, "running")
        return

    if child_status in _FAILURE_CHILD_STATUSES:
        item["status"] = "failed"
        item["error"] = _child_failure_error(child_state, child_status)
        item["finished_at"] = _now_iso()
        _save_state(parent_task_id, parent_state, status="running")
        _emit(bus, EVT_BT_PROGRESS, parent_task_id, parent_state, "running")
        return

    item["status"] = "running"
    _save_state(parent_task_id, parent_state, status="running")
    _emit(bus, EVT_BT_PROGRESS, parent_task_id, parent_state, "running")
    return


def _get_completed_child_error(item: dict, child_state: dict) -> str:
    child_task_type = (item.get("child_task_type") or "").strip()
    if child_task_type != "image_translate":
        return ""

    failed_items = [
        child_item for child_item in (child_state.get("items") or [])
        if _normalized_status(child_item.get("status")) == "failed"
    ]
    if not failed_items:
        return ""

    first_error = (
        failed_items[0].get("error")
        or child_state.get("error")
        or "image_translate failed"
    )
    failed_count = len(failed_items)
    return f"image_translate child failed ({failed_count} items): {first_error}"


def _retry_existing_image_child_if_possible(
    parent_task_id: str,
    item: dict,
    parent_state: dict,
    user_id: int,
) -> bool:
    if (item.get("child_task_type") or "").strip() != "image_translate":
        return False
    if not (item.get("child_task_id") or item.get("sub_task_id")):
        return False

    try:
        reset_count = _retry_failed_image_child_items(item, user_id)
    except (PermissionError, ValueError):
        return False

    if reset_count <= 0:
        child_state = _load_child_snapshot(item.get("child_task_type"), item.get("child_task_id"))
        if child_state:
            _apply_child_snapshot(parent_task_id, item, parent_state, child_state, bus=None)
        return _normalized_status(item.get("status")) != "failed"

    _mark_image_child_retry_running(item)
    return True


def _retry_failed_image_child_items(item: dict, user_id: int) -> int:
    child_task_id = (item.get("child_task_id") or item.get("sub_task_id") or "").strip()
    if not child_task_id:
        return 0

    from appcore.image_translate_runtime import reset_failed_items_for_retry
    from web.services import image_translate_runner

    reset_count = reset_failed_items_for_retry(child_task_id, user_id=user_id)
    if reset_count > 0:
        image_translate_runner.start(child_task_id, user_id=user_id)
    return reset_count


def _mark_image_child_retry_running(item: dict) -> None:
    child_task_id = item.get("child_task_id") or item.get("sub_task_id")
    item["child_task_id"] = child_task_id
    item["sub_task_id"] = child_task_id
    item["child_task_type"] = "image_translate"
    item["status"] = "running"
    item["error"] = None
    item["result_synced"] = False
    item["started_at"] = item.get("started_at") or _now_iso()
    item["finished_at"] = None


def _validate_force_backfill_target(item: dict) -> None:
    if _normalized_status(item.get("status")) != "failed":
        raise ValueError("only failed items can be force backfilled")
    if (item.get("kind") or "").strip() != "detail_images":
        raise ValueError("force backfill only supports detail image items")
    if (item.get("child_task_type") or "").strip() != "image_translate":
        raise ValueError("force backfill only supports image_translate children")
    if not (item.get("child_task_id") or item.get("sub_task_id")):
        raise ValueError("image translate child task missing")
    if bool(item.get("forced_backfill")):
        raise ValueError("item already force backfilled")


def _force_backfill_detail_image_child(item: dict, child_state: dict, user_id: int) -> dict:
    from appcore.image_translate_runtime import apply_translated_detail_images_from_task

    normalized_child = dict(child_state or {})
    child_status = _normalized_status(normalized_child.get("_project_status"))
    if child_status in {"queued", "planning", "dispatching", "running", "syncing_result"}:
        raise ValueError("image translate child task is still running")

    normalized_child.setdefault("id", item.get("child_task_id") or item.get("sub_task_id"))
    normalized_child.setdefault("type", "image_translate")
    normalized_child.setdefault("_user_id", user_id)
    result = apply_translated_detail_images_from_task(
        normalized_child,
        allow_partial=True,
        user_id=user_id,
    )
    if not result.get("applied_ids"):
        raise ValueError("image translate child task has no successful images to apply")
    return result


def _mark_item_force_backfilled(item: dict, result: dict) -> None:
    applied_ids = [int(item_id) for item_id in result.get("applied_ids") or []]
    skipped_failed_indices = [
        int(failed_idx) for failed_idx in result.get("skipped_failed_indices") or []
        if failed_idx is not None
    ]
    item["status"] = "done"
    item["error"] = None
    item["result_synced"] = True
    item["forced_backfill"] = True
    item["forced_backfill_at"] = _now_iso()
    item["forced_backfill_child_task_id"] = item.get("child_task_id") or item.get("sub_task_id")
    item["forced_backfill_apply_status"] = result.get("apply_status") or ""
    item["forced_backfill_applied_ids"] = applied_ids
    item["forced_backfill_applied_count"] = len(applied_ids)
    item["forced_backfill_skipped_failed_indices"] = skipped_failed_indices
    item["forced_backfill_skipped_failed_count"] = len(skipped_failed_indices)
    item["finished_at"] = _now_iso()


def _roll_up_cost_once_for_item(parent_state: dict, item: dict, child_state: dict) -> None:
    if bool(item.get("cost_rolled_up")):
        return
    _roll_up_cost(parent_state, item, child_state)
    item["cost_rolled_up"] = True


def _should_refresh_from_child(item: dict) -> bool:
    if not (item.get("child_task_id") or item.get("sub_task_id")):
        return False
    status = _normalized_status(item.get("status"))
    if status in _REFRESHABLE_ITEM_STATUSES:
        return True
    return status == "done" and not bool(item.get("result_synced"))


def _derive_parent_status(plan: list[dict], fallback_status: str) -> str:
    statuses = [_normalized_status(item.get("status")) for item in plan]
    if not statuses:
        return fallback_status or "done"
    if any(status in _RETRYABLE_ITEM_STATUSES for status in statuses):
        return "failed"
    if any(status == "awaiting_voice" for status in statuses):
        return "waiting_manual"
    if any(status in _ACTIVE_ITEM_STATUSES for status in statuses):
        return "running"
    if all(status in {"done", "skipped"} for status in statuses):
        return "done"
    if any(status == "pending" for status in statuses):
        return "running"
    return fallback_status or "running"


def _next_due_pending_item(state: dict, now_ts: float) -> dict | None:
    raw_anchor = state.get("scheduler_anchor_ts")
    anchor = float(now_ts if raw_anchor is None else raw_anchor)
    for item in state.get("plan") or []:
        if _normalized_status(item.get("status")) != "pending":
            continue
        if now_ts - anchor >= float(item.get("dispatch_after_seconds") or 0):
            return item
    return None


def _find_active_items(plan: list[dict]) -> list[dict]:
    return [
        item
        for item in plan
        if _normalized_status(item.get("status")) in _ACTIVE_ITEM_STATUSES and item.get("child_task_id")
    ]


def _stable_child_task_id(parent_id: str, item: dict) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"bulk_translate:{parent_id}:{int(item.get('idx') or 0)}").hex


def _ensure_child_identity(parent_id: str, item: dict) -> str:
    child_task_id = (item.get("child_task_id") or item.get("sub_task_id") or "").strip()
    if not child_task_id:
        child_task_id = _stable_child_task_id(parent_id, item)
    item["child_task_id"] = child_task_id
    item["sub_task_id"] = child_task_id
    return child_task_id


def _load_existing_child_project(child_task_id: str) -> dict | None:
    if not child_task_id:
        return None
    row = query_one(
        "SELECT id, type, status FROM projects WHERE id = %s AND deleted_at IS NULL",
        (child_task_id,),
    )
    return row or None


def _child_type_for_kind(kind: str | None) -> str:
    if kind in {"copy", "copywriting"}:
        return "copywriting_translate"
    if kind in {"detail", "detail_images", "cover", "video_covers"}:
        return "image_translate"
    if kind in {"video", "videos"}:
        return "multi_translate"
    return ""


def _create_child_task(parent_id: str, item: dict, parent_state: dict) -> tuple[str, str, str]:
    child_task_id = _ensure_child_identity(parent_id, item)
    existing_child = _load_existing_child_project(child_task_id)
    if existing_child:
        child_task_type = (
            existing_child.get("type")
            or item.get("child_task_type")
            or _child_type_for_kind(item.get("kind"))
        )
        item["child_task_type"] = child_task_type
        return child_task_id, child_task_type, existing_child.get("status") or "running"

    kind = item.get("kind")
    try:
        if kind in {"copy", "copywriting"}:
            return _create_copy_child(parent_id, item, parent_state)
        if kind in {"detail", "detail_images"}:
            return _create_detail_images_child(parent_id, item, parent_state)
        if kind == "video_covers":
            return _create_video_cover_child(parent_id, item, parent_state)
        if kind in {"video", "videos"}:
            return _create_video_child(parent_id, item, parent_state)
        if kind == "cover":
            return _create_video_cover_child(parent_id, item, parent_state)
    except Exception:
        existing_child = _load_existing_child_project(child_task_id)
        if existing_child:
            child_task_type = (
                existing_child.get("type")
                or item.get("child_task_type")
                or _child_type_for_kind(kind)
            )
            item["child_task_type"] = child_task_type
            return child_task_id, child_task_type, existing_child.get("status") or "running"
        raise
    raise ValueError(f"Unknown plan kind: {kind}")


def _load_child_snapshot(task_type: str | None, child_task_id: str | None) -> dict:
    del task_type
    if not child_task_id:
        return {}
    row = query_one(
        "SELECT status, state_json FROM projects WHERE id = %s",
        (child_task_id,),
    )
    if not row:
        return {}
    raw_state = row.get("state_json")
    state = raw_state if isinstance(raw_state, dict) else json.loads(raw_state or "{}")
    state["_project_status"] = row.get("status")
    return state


def _image_child_failed_items(child_state: dict) -> list[dict]:
    return [
        item for item in (child_state.get("items") or [])
        if _normalized_status(item.get("status")) == "failed"
    ]


def _first_child_error(child_state: dict) -> str:
    for item in _image_child_failed_items(child_state):
        error = (item.get("error") or "").strip()
        if error:
            return error
    return (child_state.get("last_error") or child_state.get("error") or "").strip()


def _child_failure_error(child_state: dict, child_status: str) -> str:
    return (
        child_state.get("last_error")
        or child_state.get("error")
        or child_state.get("message")
        or f"child task failed: {child_status}"
    )


def _sync_child_result(
    parent_task_id: str,
    item: dict,
    parent_state: dict,
    child_state: dict,
) -> None:
    kind = item.get("kind")
    product_id = int(parent_state.get("product_id") or 0)
    lang = (item.get("lang") or "").strip()

    if kind in {"copy", "copywriting", "detail", "cover"}:
        return

    if kind == "detail_images":
        sync_detail_images_result(
            parent_task_id=parent_task_id,
            child_task_id=item["child_task_id"],
        )
        return

    if kind == "video_covers":
        items = child_state.get("items") or []
        source_raw_ids = list(item.get("ref", {}).get("source_raw_ids") or [])
        for idx, raw_id in enumerate(source_raw_ids):
            child_item = items[idx] if idx < len(items) else {}
            cover_object_key = (child_item.get("dst_tos_key") or "").strip()
            if not cover_object_key:
                raise RuntimeError(f"video cover output missing for raw source {raw_id}")
            sync_video_cover_result(
                parent_task_id=parent_task_id,
                product_id=product_id,
                lang=lang,
                source_raw_id=int(raw_id),
                cover_object_key=cover_object_key,
            )
        return

    if kind in {"video", "videos"}:
        source_raw_id = int(item.get("ref", {}).get("source_raw_id") or 0)
        video_object_key = _materialize_multi_translate_video(
            product_id=product_id,
            lang=lang,
            source_raw_id=source_raw_id,
            child_task_id=item["child_task_id"],
            child_state=child_state,
        )
        cover_object_key = _materialize_multi_translate_cover(
            product_id=product_id,
            lang=lang,
            source_raw_id=source_raw_id,
            child_task_id=item["child_task_id"],
            child_state=child_state,
        )
        sync_video_result(
            parent_task_id=parent_task_id,
            product_id=product_id,
            lang=lang,
            source_raw_id=source_raw_id,
            video_object_key=video_object_key,
            cover_object_key=cover_object_key,
        )
        return


def _create_copy_child(parent_id: str, item: dict, parent_state: dict) -> tuple[str, str, str]:
    from appcore.copywriting_translate_runtime import CopywritingTranslateRunner

    child_task_id = _ensure_child_identity(parent_id, item)
    user_id = int((parent_state.get("initiator") or {}).get("user_id") or 0)
    state = {
        "product_id": parent_state.get("product_id"),
        "source_lang": "en",
        "target_lang": item.get("lang"),
        "source_copy_id": (item.get("ref") or {}).get("source_copy_id"),
        "parent_task_id": parent_id,
    }
    execute(
        """
        INSERT INTO projects (id, user_id, type, status, state_json)
        VALUES (%s, %s, 'copywriting_translate', 'queued', %s)
        """,
        (child_task_id, user_id, json.dumps(state, ensure_ascii=False, default=str)),
    )
    _spawn_daemon(lambda: CopywritingTranslateRunner(child_task_id).start())
    return child_task_id, "copywriting_translate", "running"


def _create_detail_images_child(parent_id: str, item: dict, parent_state: dict) -> tuple[str, str, str]:
    from appcore import image_translate_settings as its
    from appcore.task_state import create_image_translate
    from web.routes.image_translate import start_image_translate_runner

    product_id = int(parent_state.get("product_id") or 0)
    user_id = int((parent_state.get("initiator") or {}).get("user_id") or 0)
    lang = (item.get("lang") or "").strip()
    target_language_name = medias.get_language_name(lang)
    source_ids = {int(source_id) for source_id in ((item.get("ref") or {}).get("source_detail_ids") or [])}
    source_rows = [
        row for row in medias.list_detail_images(product_id, "en")
        if int(row.get("id") or 0) in source_ids
    ]
    if not source_rows:
        raise ValueError("english detail images are required first")
    source_rows = [row for row in source_rows if not medias.detail_image_is_gif(row)]
    if not source_rows:
        raise ValueError("english detail images contain no translatable static images")

    child_task_id = _ensure_child_identity(parent_id, item)
    task_dir = os.path.join(OUTPUT_DIR, child_task_id)
    os.makedirs(task_dir, exist_ok=True)
    prompt = its.get_prompt("detail", lang).replace("{target_language_name}", target_language_name)
    items = [
        {
            "idx": idx,
            "filename": os.path.basename(row.get("object_key") or "") or f"detail_{idx}.png",
            "src_tos_key": row["object_key"],
            "source_bucket": "media",
            "source_detail_image_id": row["id"],
        }
        for idx, row in enumerate(source_rows)
    ]
    medias_context = {
        "entry": "bulk_translate",
        "parent_task_id": parent_id,
        "product_id": product_id,
        "source_lang": "en",
        "target_lang": lang,
        "source_bucket": "media",
        "source_detail_image_ids": [int(row["id"]) for row in source_rows],
        "auto_apply_detail_images": False,
    }
    create_image_translate(
        child_task_id,
        task_dir,
        user_id=user_id,
        preset="detail",
        target_language=lang,
        target_language_name=target_language_name,
        model_id=_default_image_translate_model_id(user_id),
        prompt=prompt,
        items=items,
        medias_context=medias_context,
        concurrency_mode="parallel",
    )
    start_image_translate_runner(child_task_id, user_id)
    return child_task_id, "image_translate", "running"


def _create_video_cover_child(parent_id: str, item: dict, parent_state: dict) -> tuple[str, str, str]:
    from appcore import image_translate_settings as its
    from appcore.task_state import create_image_translate
    from web.routes.image_translate import start_image_translate_runner

    product_id = int(parent_state.get("product_id") or 0)
    user_id = int((parent_state.get("initiator") or {}).get("user_id") or 0)
    lang = (item.get("lang") or "").strip()
    target_language_name = medias.get_language_name(lang)
    source_raw_ids = [int(raw_id) for raw_id in ((item.get("ref") or {}).get("source_raw_ids") or [])]
    raw_rows = []
    for raw_id in source_raw_ids:
        row = medias.get_raw_source(raw_id)
        if row:
            raw_rows.append(row)
    if not raw_rows:
        raise ValueError("raw source covers are required first")

    child_task_id = _ensure_child_identity(parent_id, item)
    task_dir = os.path.join(OUTPUT_DIR, child_task_id)
    os.makedirs(task_dir, exist_ok=True)
    prompt = its.get_prompt("cover", lang).replace("{target_language_name}", target_language_name)
    items = [
        {
            "idx": idx,
            "filename": os.path.basename(row.get("cover_object_key") or "") or f"raw_cover_{idx}.png",
            "src_tos_key": row["cover_object_key"],
            "source_bucket": "media",
        }
        for idx, row in enumerate(raw_rows)
    ]
    medias_context = {
        "entry": "bulk_translate_video_cover",
        "parent_task_id": parent_id,
        "product_id": product_id,
        "target_lang": lang,
        "source_bucket": "media",
        "source_raw_ids": source_raw_ids,
    }
    create_image_translate(
        child_task_id,
        task_dir,
        user_id=user_id,
        preset="cover",
        target_language=lang,
        target_language_name=target_language_name,
        model_id=_default_image_translate_model_id(user_id),
        prompt=prompt,
        items=items,
        medias_context=medias_context,
        concurrency_mode="parallel",
    )
    start_image_translate_runner(child_task_id, user_id)
    return child_task_id, "image_translate", "running"


def _create_video_child(parent_id: str, item: dict, parent_state: dict) -> tuple[str, str, str]:
    from web import store
    from web.services import multi_pipeline_runner

    product_id = int(parent_state.get("product_id") or 0)
    user_id = int((parent_state.get("initiator") or {}).get("user_id") or 0)
    lang = (item.get("lang") or "").strip()
    if lang not in _MULTI_TRANSLATE_SUPPORTED_LANGS:
        raise ValueError(f"unsupported multi_translate target lang: {lang}")
    child_project_type = "multi_translate"
    runner = multi_pipeline_runner

    source_raw_id = int((item.get("ref") or {}).get("source_raw_id") or 0)
    raw_source = medias.get_raw_source(source_raw_id)
    if not raw_source:
        raise ValueError(f"raw source missing: {source_raw_id}")

    child_task_id = _ensure_child_identity(parent_id, item)
    task_dir = os.path.join(OUTPUT_DIR, child_task_id)
    os.makedirs(task_dir, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    source_media_object_key = (raw_source.get("video_object_key") or "").strip()
    source_name = Path(source_media_object_key).name or f"raw_{source_raw_id}.mp4"
    ext = Path(source_name).suffix or ".mp4"
    video_path = os.path.join(UPLOAD_DIR, f"{child_task_id}{ext}")
    _download_media_source_to(source_media_object_key, video_path)

    store.create(
        child_task_id,
        video_path,
        task_dir,
        original_filename=source_name,
        user_id=user_id,
    )
    execute("UPDATE projects SET type = %s WHERE id = %s", (child_project_type, child_task_id))

    params = dict(parent_state.get("video_params_snapshot") or {})
    store.update(
        child_task_id,
        type=child_project_type,
        display_name=f"{(raw_source.get('display_name') or Path(source_name).stem)}-{lang}",
        target_lang=lang,
        source_tos_key="",
        source_object_info={
            "file_size": raw_source.get("file_size"),
            "original_filename": source_name,
            "source_media_object_key": source_media_object_key,
            "storage_backend": "media_store",
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        },
        delivery_mode="local_primary",
        interactive_review=False,
        subtitle_font=params.get("subtitle_font") or "Impact",
        subtitle_size=int(params.get("subtitle_size") or 14),
        subtitle_position=params.get("subtitle_position") or "bottom",
        subtitle_position_y=float(params.get("subtitle_position_y") or 0.68),
        medias_context={
            "entry": "bulk_translate_video",
            "parent_task_id": parent_id,
            "product_id": product_id,
            "source_raw_id": source_raw_id,
            "source_media_object_key": source_media_object_key,
            "target_lang": lang,
        },
    )
    runner.start(child_task_id, user_id=user_id)
    return child_task_id, child_project_type, "running"


def _materialize_multi_translate_video(
    *,
    product_id: int,
    lang: str,
    source_raw_id: int,
    child_task_id: str,
    child_state: dict,
) -> str:
    raw_source = medias.get_raw_source(source_raw_id) or {}
    base_name = (
        Path(raw_source.get("video_object_key") or "").stem
        or f"raw_{source_raw_id}"
    )
    local_path = _pick_existing_path(
        [
            ((child_state.get("result") or {}).get("hard_video") or ""),
            ((child_state.get("compose_result") or {}).get("hard_video") or ""),
            ((child_state.get("preview_files") or {}).get("hard_video") or ""),
            (((child_state.get("variants") or {}).get("normal") or {}).get("result") or {}).get("hard_video") or "",
            (((child_state.get("variants") or {}).get("normal") or {}).get("preview_files") or {}).get("hard_video") or "",
            (child_state.get("final_video") or ""),
        ]
    )
    if not local_path:
        raise RuntimeError(f"multi_translate output missing for child task {child_task_id}")
    ext = Path(local_path).suffix or ".mp4"
    object_key = f"{int(raw_source.get('user_id') or 0)}/medias/{product_id}/{lang}_{base_name}{ext}"
    with open(local_path, "rb") as fh:
        local_media_storage.write_bytes(object_key, fh.read())
    return object_key


def _materialize_multi_translate_cover(
    *,
    product_id: int,
    lang: str,
    source_raw_id: int,
    child_task_id: str,
    child_state: dict,
) -> str:
    raw_source = medias.get_raw_source(source_raw_id) or {}
    existing_cover = medias.get_raw_source_translation(source_raw_id, lang) or {}
    if (existing_cover.get("cover_object_key") or "").strip():
        return existing_cover["cover_object_key"]

    thumbnail_path = _pick_existing_path(
        [
            (child_state.get("thumbnail_path") or ""),
            ((child_state.get("preview_files") or {}).get("thumbnail") or ""),
            (((child_state.get("variants") or {}).get("normal") or {}).get("preview_files") or {}).get("thumbnail") or "",
        ]
    )
    if not thumbnail_path:
        return raw_source.get("cover_object_key") or ""
    ext = Path(thumbnail_path).suffix or ".jpg"
    base_name = Path(raw_source.get("cover_object_key") or "").stem or f"raw_{source_raw_id}_cover"
    object_key = f"{int(raw_source.get('user_id') or 0)}/medias/{product_id}/{lang}_{base_name}{ext}"
    with open(thumbnail_path, "rb") as fh:
        local_media_storage.write_bytes(object_key, fh.read())
    return object_key


def _pick_existing_path(candidates: list[str]) -> str:
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def _default_image_translate_model_id(_user_id: int | None) -> str:
    from appcore import image_translate_settings as its
    from appcore.gemini_image import coerce_image_model

    channel = "aistudio"
    try:
        channel = its.get_channel()
    except Exception:
        pass
    try:
        return its.get_default_model(channel)
    except Exception:
        return coerce_image_model("", channel=channel)


def _spawn_daemon(fn) -> None:
    threading.Thread(target=fn, daemon=True).start()


def _child_needs_voice(child_state: dict) -> bool:
    current_review_step = (child_state.get("current_review_step") or "").strip()
    steps = child_state.get("steps") or {}
    voice_match_status = (steps.get("voice_match") or "").strip()
    return current_review_step == "voice_match" or voice_match_status == "waiting"


def _roll_up_cost(parent_state: dict, item: dict, child_state: dict) -> None:
    actual = (
        parent_state.setdefault("cost_tracking", {})
        .setdefault("actual", {
            "copy_tokens_used": 0,
            "image_processed": 0,
            "video_minutes_processed": 0.0,
            "actual_cost_cny": 0.0,
        })
    )
    kind = item.get("kind")
    if kind in {"copy", "copywriting"}:
        actual["copy_tokens_used"] += int(child_state.get("tokens_used") or 0)
    elif kind in {"detail", "detail_images", "cover", "video_covers"}:
        actual["image_processed"] += len((item.get("ref") or {}).get("source_detail_ids") or []) or len((item.get("ref") or {}).get("source_raw_ids") or []) or len((item.get("ref") or {}).get("source_cover_ids") or []) or 1
    elif kind in {"video", "videos"}:
        source_raw_id = int((item.get("ref") or {}).get("source_raw_id") or 0)
        raw_source = medias.get_raw_source(source_raw_id) or {}
        actual["video_minutes_processed"] += float(raw_source.get("duration_seconds") or 0.0) / 60.0

    total = (
        (float(actual["copy_tokens_used"]) / 1000.0) * COST_PER_1K_TOKENS_CNY
        + float(actual["image_processed"]) * COST_PER_IMAGE_CNY
        + float(actual["video_minutes_processed"]) * COST_PER_VIDEO_MINUTE_CNY
    )
    actual["actual_cost_cny"] = round(total, 2)


def _normalize_item(raw_item: dict) -> dict:
    item = dict(raw_item or {})
    child_task_id = item.get("child_task_id") or item.get("sub_task_id")
    item["child_task_id"] = child_task_id
    item["sub_task_id"] = child_task_id
    item["child_task_type"] = item.get("child_task_type")
    item["dispatch_after_seconds"] = int(item.get("dispatch_after_seconds") or 0)
    item["result_synced"] = bool(item.get("result_synced", False))
    item["status"] = _normalized_status(item.get("status"))
    item.setdefault("error", None)
    item.setdefault("started_at", None)
    item.setdefault("finished_at", None)
    item["cost_rolled_up"] = bool(item.get("cost_rolled_up", False))
    item["forced_backfill"] = bool(item.get("forced_backfill", False))
    item.setdefault("forced_backfill_applied_count", 0)
    item.setdefault("forced_backfill_skipped_failed_count", 0)
    item.setdefault("forced_backfill_apply_status", "")
    return item


def _reset_item_for_retry(item: dict) -> None:
    item["status"] = "pending"
    item["error"] = None
    item["child_task_id"] = None
    item["sub_task_id"] = None
    item["child_task_type"] = None
    item["result_synced"] = False
    item["started_at"] = None
    item["finished_at"] = None
    item["cost_rolled_up"] = False
    item["forced_backfill"] = False
    item["forced_backfill_at"] = None
    item["forced_backfill_child_task_id"] = None
    item["forced_backfill_apply_status"] = ""
    item["forced_backfill_applied_ids"] = []
    item["forced_backfill_applied_count"] = 0
    item["forced_backfill_skipped_failed_indices"] = []
    item["forced_backfill_skipped_failed_count"] = 0


def _normalized_status(status: str | None) -> str:
    raw = (status or "").strip()
    if raw == "error":
        return "failed"
    return raw or "pending"


def _save_state(task_id: str, state: dict, status: str | None = None) -> None:
    state["progress"] = compute_progress(state.get("plan") or [])
    payload = json.dumps(state, ensure_ascii=False, default=str)
    if status is not None:
        execute(
            "UPDATE projects SET state_json = %s, status = %s WHERE id = %s",
            (payload, status, task_id),
        )
    else:
        execute(
            "UPDATE projects SET state_json = %s WHERE id = %s",
            (payload, task_id),
        )


def _audit(user_id: int, action: str, detail: dict | None = None) -> dict:
    return {
        "ts": _now_iso(),
        "user_id": user_id,
        "action": action,
        "detail": detail or {},
    }


def _append_audit(state: dict, user_id: int, action: str, detail: dict | None = None) -> None:
    state.setdefault("audit_events", []).append(_audit(user_id, action, detail))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(bus: EventBus | None, event_type: str, task_id: str, state: dict, status: str) -> None:
    if bus is None:
        return
    bus.publish(
        Event(
            type=event_type,
            task_id=task_id,
            payload={
                "status": status,
                "progress": state.get("progress"),
                "current_idx": state.get("current_idx"),
                "cost_actual": (state.get("cost_tracking") or {}).get("actual"),
            },
        )
    )
