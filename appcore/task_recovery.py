from __future__ import annotations

import copy
import json
import logging
import threading

from appcore.db import execute as db_execute, query as db_query, query_one as db_query_one
import appcore.task_state as task_state

log = logging.getLogger(__name__)

RECOVERY_ERROR_MESSAGE = "任务因服务重启或后台执行中断，已自动标记为失败，请重新发起。"

PIPELINE_PROJECT_TYPES = {"translation", "de_translate", "fr_translate", "copywriting"}
LINK_CHECK_RUNNING_STATUSES = {"queued", "locking_locale", "downloading", "analyzing"}
RECOVERABLE_PROJECT_TYPES = {"video_creation", "video_review", "link_check"} | PIPELINE_PROJECT_TYPES
LINK_CHECK_STARTUP_RECOVERY_STATUSES = ("locking_locale", "downloading", "analyzing")

_active_tasks: set[tuple[str, str]] = set()
_active_lock = threading.Lock()


def register_active_task(project_type: str, task_id: str) -> None:
    with _active_lock:
        _active_tasks.add((project_type, task_id))


def unregister_active_task(project_type: str, task_id: str) -> None:
    with _active_lock:
        _active_tasks.discard((project_type, task_id))


def is_task_active(project_type: str, task_id: str) -> bool:
    with _active_lock:
        return (project_type, task_id) in _active_tasks


def _mark_running_steps_as_error(state: dict) -> bool:
    steps = state.setdefault("steps", {})
    step_messages = state.setdefault("step_messages", {})
    changed = False
    for step, status in list(steps.items()):
        if status == "running":
            steps[step] = "error"
            step_messages[step] = RECOVERY_ERROR_MESSAGE
            changed = True
    return changed


def recover_project_state(project_type: str, task_id: str, state: dict | None, active: bool | None = None) -> tuple[bool, dict, str | None]:
    recovered = copy.deepcopy(state or {})
    active = is_task_active(project_type, task_id) if active is None else active
    if active:
        return False, recovered, None

    steps = recovered.setdefault("steps", {})
    changed = False

    if project_type == "video_creation" and steps.get("generate") == "running":
        steps["generate"] = "error"
        changed = True
    elif project_type == "video_review" and steps.get("review") == "running":
        steps["review"] = "error"
        recovered["review_started_at"] = None
        changed = True
    elif project_type == "link_check" and recovered.get("status") in LINK_CHECK_RUNNING_STATUSES:
        changed = _mark_running_steps_as_error(recovered) or changed
        recovered["status"] = "failed"
        recovered["error"] = RECOVERY_ERROR_MESSAGE
        return True, recovered, "failed"
    elif project_type in PIPELINE_PROJECT_TYPES:
        changed = _mark_running_steps_as_error(recovered)
        if recovered.get("current_review_step"):
            recovered["current_review_step"] = ""
            changed = True

    if not changed:
        return False, recovered, None

    recovered["status"] = "error"
    recovered["error"] = RECOVERY_ERROR_MESSAGE
    return True, recovered, "error"


def _persist_project_recovery(task_id: str, recovered: dict, status: str) -> None:
    db_execute(
        "UPDATE projects SET state_json = %s, status = %s WHERE id = %s",
        (json.dumps(recovered, ensure_ascii=False), status, task_id),
    )


def recover_project_if_needed(task_id: str, project_type: str) -> dict | None:
    try:
        row = db_query_one(
            "SELECT state_json FROM projects WHERE id = %s AND type = %s AND deleted_at IS NULL",
            (task_id, project_type),
        )
    except Exception:
        log.warning("[task_recovery] failed to load project %s (%s) for recovery", task_id, project_type, exc_info=True)
        return None
    if not row:
        return None
    state = json.loads(row.get("state_json") or "{}")
    changed, recovered, status = recover_project_state(project_type, task_id, state)
    if changed and status:
        _persist_project_recovery(task_id, recovered, status)
        log.warning("[task_recovery] recovered interrupted %s task %s", project_type, task_id)
    return recovered if changed else state


def recover_task_if_needed(task_id: str) -> dict | None:
    task = task_state.get(task_id)
    if not task:
        return None
    project_type = (task.get("type") or "translation").strip() or "translation"
    changed, recovered, _status = recover_project_state(project_type, task_id, task)
    if changed:
        task_state.update(task_id, **recovered)
        log.warning("[task_recovery] recovered interrupted in-memory %s task %s", project_type, task_id)
        return task_state.get(task_id)
    return task


def recover_all_interrupted_tasks() -> int:
    non_link_check_types = tuple(sorted(RECOVERABLE_PROJECT_TYPES - {"link_check"}))
    placeholders = ", ".join(["%s"] * len(non_link_check_types))
    link_check_statuses = ", ".join(f"'{status}'" for status in LINK_CHECK_STARTUP_RECOVERY_STATUSES)
    try:
        rows = db_query(
            f"SELECT id, type, status, state_json FROM projects "
            f"WHERE deleted_at IS NULL AND ("
            f"(type = 'link_check' AND status IN ({link_check_statuses})) "
            f"OR (status = 'running' AND type IN ({placeholders}))"
            f")",
            non_link_check_types,
        )
    except Exception:
        log.warning("[task_recovery] startup recovery query failed", exc_info=True)
        return 0
    recovered_count = 0
    for row in rows:
        task_id = row.get("id")
        project_type = row.get("type") or ""
        try:
            state = json.loads(row.get("state_json") or "{}")
        except Exception:
            state = {}
        changed, recovered, status = recover_project_state(project_type, task_id, state)
        if changed and status:
            _persist_project_recovery(task_id, recovered, status)
            recovered_count += 1
            log.warning("[task_recovery] recovered interrupted %s task %s during startup", project_type, task_id)
    return recovered_count
