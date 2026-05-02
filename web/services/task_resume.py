"""Task resume workflow helpers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from appcore.db import query_one as db_query_one
from appcore.task_recovery import recover_task_if_needed
from web import store
from web.services import pipeline_runner
from web.services.task_access import load_task as load_task_from_store
from web.services.task_access import refresh_task as refresh_task_from_store
from web.services.task_source_video import ensure_local_source_video


@dataclass(frozen=True)
class TaskResumeOutcome:
    payload: dict[str, Any]
    status_code: int
    not_found: bool = False


def resume_task_from_step(
    task_id: str,
    *,
    user_id: int,
    start_step: str,
    resumable_steps: Sequence[str],
    query_one=db_query_one,
    recover_task: Callable[[str], object] | None = None,
    load_task: Callable[[str], dict | None] | None = None,
    refresh_task: Callable[[str, dict], dict] | None = None,
    ensure_source: Callable[[str, dict], object] | None = None,
    resume_runner: Callable[..., object] | None = None,
    set_step: Callable[[str, str, str], object] = store.set_step,
    set_step_message: Callable[[str, str, str], object] = store.set_step_message,
    update_task: Callable[..., object] = store.update,
) -> TaskResumeOutcome:
    row = query_one(
        "SELECT id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, user_id),
    )
    if not row:
        return TaskResumeOutcome({}, 404, not_found=True)

    recover_task = recover_task or recover_task_if_needed
    recover_task(task_id)
    load_task = load_task or load_task_from_store
    task = load_task(task_id)
    if not task:
        return TaskResumeOutcome({}, 404, not_found=True)

    if start_step not in resumable_steps:
        return TaskResumeOutcome({"error": f"start_step must be one of {list(resumable_steps)}"}, 400)

    started = False
    for step in resumable_steps:
        if step == start_step:
            started = True
        if started:
            set_step(task_id, step, "pending")
            set_step_message(task_id, step, "等待中...")

    resume_payload = {"status": "running", "error": "", "current_review_step": ""}
    if (task.get("pipeline_version") or "") == "av":
        resume_payload["type"] = "translation"
    update_task(task_id, **resume_payload)

    refresh_task = refresh_task or refresh_task_from_store
    task = refresh_task(task_id, task)
    try:
        ensure_source = ensure_source or ensure_local_source_video
        ensure_source(task_id, task)
    except FileNotFoundError as exc:
        return TaskResumeOutcome({"error": str(exc)}, 409)

    resume_runner = resume_runner or pipeline_runner.resume
    resume_runner(task_id, start_step, user_id=user_id)
    return TaskResumeOutcome({"status": "started", "start_step": start_step}, 200)
