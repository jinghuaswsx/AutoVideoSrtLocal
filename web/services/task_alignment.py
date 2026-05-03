"""Task alignment confirmation workflow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pipeline.alignment import build_script_segments
from web import store
from web.preview_artifacts import build_alignment_artifact
from web.services import pipeline_runner


@dataclass(frozen=True)
class TaskAlignmentOutcome:
    payload: dict
    status_code: int = 200


def confirm_task_alignment(
    task_id: str,
    task: dict,
    body: Mapping[str, object],
    *,
    user_id: int | None,
    build_segments: Callable[..., list] = build_script_segments,
    build_artifact: Callable[..., dict] = build_alignment_artifact,
    confirm_alignment: Callable[..., object] = store.confirm_alignment,
    set_artifact: Callable[..., object] = store.set_artifact,
    set_current_review_step: Callable[..., object] = store.set_current_review_step,
    set_step: Callable[..., object] = store.set_step,
    set_step_message: Callable[..., object] = store.set_step_message,
    update_task: Callable[..., object] = store.update,
    runner=pipeline_runner,
) -> TaskAlignmentOutcome:
    break_after = body.get("break_after")
    if not isinstance(break_after, list):
        return TaskAlignmentOutcome({"error": "break_after required"}, 400)

    try:
        script_segments = build_segments(task.get("utterances", []), break_after)
    except ValueError as exc:
        return TaskAlignmentOutcome({"error": str(exc)}, 400)

    confirm_alignment(task_id, break_after, script_segments)
    set_artifact(
        task_id,
        "alignment",
        build_artifact(task.get("scene_cuts", []), script_segments, break_after),
    )
    set_current_review_step(task_id, "")
    set_step(task_id, "alignment", "done")
    set_step_message(task_id, "alignment", "分段确认完成")

    if task.get("interactive_review"):
        set_current_review_step(task_id, "translate")
        set_step(task_id, "translate", "waiting")
        set_step_message(task_id, "translate", "请选择翻译模型和提示词")
        update_task(task_id, _translate_pre_select=True)
    else:
        runner.resume(task_id, "translate", user_id=user_id)

    return TaskAlignmentOutcome({"status": "ok", "script_segments": script_segments})
