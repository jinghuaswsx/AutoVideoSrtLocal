from __future__ import annotations

import copy
import json
import logging
import threading

from appcore.db import execute as db_execute, query as db_query, query_one as db_query_one
import appcore.task_state as task_state

log = logging.getLogger(__name__)

RECOVERY_ERROR_MESSAGE = "任务因服务重启或后台执行中断，已自动标记为失败，请重新发起。"
RECOVERY_INTERRUPTED_MESSAGE = "任务因服务重启或后台执行中断，已标记为中断；请在页面手动重新启动。"
IMAGE_TRANSLATE_INTERRUPTED_MESSAGE = "服务重启导致任务中断，点「重新生成」继续处理未完成的图片。"

# 图片翻译 APIMART 任务在上游已提交即可能计费；启动恢复时只要有 task_id，
# 就自动拉起 worker 先检查上游结果，避免本地重启后直接重复提交。

PIPELINE_PROJECT_TYPES = {"translation", "de_translate", "fr_translate", "ja_translate", "copywriting"}
INTERRUPTED_PIPELINE_PROJECT_TYPES = {"multi_translate", "omni_translate", "translate_lab", "av_translate"}
LINK_CHECK_RUNNING_STATUSES = {"locking_locale", "downloading", "analyzing", "summarizing"}
RECOVERABLE_PROJECT_TYPES = (
    {"video_creation", "video_review", "link_check", "image_translate", "subtitle_removal"}
    | PIPELINE_PROJECT_TYPES
    | INTERRUPTED_PIPELINE_PROJECT_TYPES
)
LINK_CHECK_STARTUP_RECOVERY_STATUSES = ("locking_locale", "downloading", "analyzing", "summarizing")
IMAGE_TRANSLATE_STARTUP_RECOVERY_STATUSES = ("queued", "running")
SUBTITLE_REMOVAL_STARTUP_RECOVERY_STATUSES = ("queued", "running", "submitted")

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


def _mark_inflight_steps_as_interrupted(state: dict) -> bool:
    steps = state.setdefault("steps", {})
    step_messages = state.setdefault("step_messages", {})
    changed = False
    for step, status in list(steps.items()):
        if status in {"queued", "running"}:
            steps[step] = "interrupted"
            step_messages[step] = RECOVERY_INTERRUPTED_MESSAGE
            changed = True
    return changed


def _has_waiting_steps(state: dict) -> bool:
    steps = state.get("steps", {}) or {}
    return any(status == "waiting" for status in steps.values())


def recover_project_state(project_type: str, task_id: str, state: dict | None, active: bool | None = None) -> tuple[bool, dict, str | None]:
    recovered = copy.deepcopy(state or {})
    if str(recovered.get("pipeline_version") or "").strip() == "av":
        project_type = "av_translate"
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
        summary = recovered.setdefault("summary", {})
        if summary.get("overall_decision") == "running":
            summary["overall_decision"] = "unfinished"
        return True, recovered, "failed"
    elif project_type == "image_translate" and recovered.get("status") in IMAGE_TRANSLATE_STARTUP_RECOVERY_STATUSES:
        # image_translate recovers by item status:
        #   A. all items terminal -> heal the task-level status;
        #   B. any saved async provider task -> keep running and poll upstream;
        #   C. otherwise mark interrupted for manual retry.
        items = recovered.get("items") or []
        total = len(items)
        done = sum(1 for it in items if (it.get("status") or "") == "done")
        failed = sum(1 for it in items if (it.get("status") or "") == "failed")
        running = sum(1 for it in items if (it.get("status") or "") == "running")
        pending = total - done - failed - running

        # A. 全部到终态 — 修复「完成但状态卡住」的不一致
        if total > 0 and running == 0 and pending == 0 and (done + failed) == total:
            recovered["items"] = items
            recovered["progress"] = {
                "total": total, "done": done, "failed": failed, "running": 0,
            }
            steps = recovered.setdefault("steps", {})
            if failed == 0:
                steps["process"] = "done"
                recovered["status"] = "done"
                recovered["error"] = ""
                return True, recovered, "done"
            # 有失败但没有挂起任务，给用户留「重新生成失败图」的入口
            steps["process"] = "interrupted"
            recovered["status"] = "interrupted"
            recovered["error"] = IMAGE_TRANSLATE_INTERRUPTED_MESSAGE
            return True, recovered, "interrupted"

        # B. Keep running when an upstream async task was already submitted.
        # The worker will poll that task before considering any new submission.
        has_resumable = False
        for it in items:
            # 兼容旧字段名 apimart_task_id；新代码统一写 provider_task_id
            snapshot = (
                (it.get("provider_task_id") or "").strip()
                or (it.get("apimart_task_id") or "").strip()
            )
            if not snapshot:
                continue
            submitted_at = float(
                it.get("provider_task_submitted_at")
                or it.get("apimart_submitted_at")
                or 0.0
            )
            if submitted_at <= 0:
                continue
            has_resumable = True
            break

        if has_resumable:
            recovered["items"] = items
            recovered["progress"] = {
                "total": total, "done": done, "failed": failed, "running": running,
            }
            steps = recovered.setdefault("steps", {})
            steps["process"] = "running"
            recovered["status"] = "running"
            recovered["error"] = ""
            return True, recovered, "running"

        # C. 默认路径：标中断等用户手动处理
        for it in items:
            if (it.get("status") or "") == "running":
                it["status"] = "pending"
                it["error"] = ""
                it["attempts"] = 0
        recovered["items"] = items
        recovered["progress"] = {
            "total": total,
            "done": done,
            "failed": failed,
            "running": 0,
        }
        steps = recovered.setdefault("steps", {})
        if steps.get("process") == "running":
            steps["process"] = "interrupted"
        recovered["status"] = "interrupted"
        recovered["error"] = IMAGE_TRANSLATE_INTERRUPTED_MESSAGE
        return True, recovered, "interrupted"
    elif project_type == "subtitle_removal" and recovered.get("status") in SUBTITLE_REMOVAL_STARTUP_RECOVERY_STATUSES:
        changed = _mark_inflight_steps_as_interrupted(recovered)
        if not changed:
            changed = True
        recovered["status"] = "interrupted"
        recovered["error"] = RECOVERY_INTERRUPTED_MESSAGE
        return True, recovered, "interrupted"
    elif project_type in INTERRUPTED_PIPELINE_PROJECT_TYPES:
        if _has_waiting_steps(recovered):
            return False, recovered, None
        changed = _mark_inflight_steps_as_interrupted(recovered)
        if changed and recovered.get("current_review_step"):
            recovered["current_review_step"] = ""
        if recovered.get("status") == "running":
            changed = True
        if not changed:
            return False, recovered, None
        recovered["status"] = "interrupted"
        recovered["error"] = RECOVERY_INTERRUPTED_MESSAGE
        return True, recovered, "interrupted"
    elif project_type in PIPELINE_PROJECT_TYPES:
        if _has_waiting_steps(recovered):
            return False, recovered, None
        changed = _mark_running_steps_as_error(recovered)
        if changed and recovered.get("current_review_step"):
            recovered["current_review_step"] = ""

    if not changed:
        return False, recovered, None

    if project_type == "link_check":
        return True, recovered, "failed"

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
    # link_check / image_translate 各自有独立的启动恢复状态集，其余类型统一按 status='running' 扫。
    generic_types = tuple(sorted(RECOVERABLE_PROJECT_TYPES - {"link_check", "image_translate"}))
    placeholders = ", ".join(["%s"] * len(generic_types))
    link_check_statuses = ", ".join(f"'{status}'" for status in LINK_CHECK_STARTUP_RECOVERY_STATUSES)
    image_translate_statuses = ", ".join(f"'{status}'" for status in IMAGE_TRANSLATE_STARTUP_RECOVERY_STATUSES)
    subtitle_removal_statuses = ", ".join(f"'{status}'" for status in SUBTITLE_REMOVAL_STARTUP_RECOVERY_STATUSES)
    try:
        rows = db_query(
            f"SELECT id, type, status, state_json FROM projects "
            f"WHERE deleted_at IS NULL AND ("
            f"(type = 'link_check' AND status IN ({link_check_statuses})) "
            f"OR (type = 'image_translate' AND status IN ({image_translate_statuses})) "
            f"OR (type = 'subtitle_removal' AND status IN ({subtitle_removal_statuses})) "
            f"OR (status = 'running' AND type IN ({placeholders}))"
            f")",
            generic_types,
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
            _auto_resume_after_recovery(task_id, project_type, recovered, status)
    return recovered_count


def _auto_resume_after_recovery(task_id: str, project_type: str, recovered: dict, status: str) -> None:
    """某些项目类型在恢复后需要立即拉起 worker 继续跑（如 image_translate 的 APIMART
    异步任务，要继续轮询上游结果）。单独抽出便于测试/失败隔离。"""
    if project_type != "image_translate" or status != "running":
        return
    user_id = recovered.get("_user_id")
    try:
        user_id = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        user_id = None
    try:
        from web.routes.image_translate import start_image_translate_runner
        started = start_image_translate_runner(task_id, user_id)
        log.warning(
            "[task_recovery] auto-resumed image_translate task %s (started=%s)",
            task_id, started,
        )
    except Exception:
        log.warning(
            "[task_recovery] failed to auto-resume image_translate task %s",
            task_id, exc_info=True,
        )
