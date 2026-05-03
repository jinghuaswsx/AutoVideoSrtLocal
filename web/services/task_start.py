"""Task pipeline start workflow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from web import store
from web.services import pipeline_runner
from web.services.task_access import refresh_task as refresh_task_state
from web.services.task_av_inputs import merge_av_step_maps
from web.services.task_source_video import (
    ensure_local_source_video as ensure_source_video,
    task_requires_source_sync as requires_source_sync,
)
from web.services.task_start_inputs import parse_bool


@dataclass(frozen=True)
class TaskStartOutcome:
    payload: dict
    status_code: int = 200


def start_task_pipeline(
    task_id: str,
    task: dict,
    body: Mapping[str, object],
    *,
    av_inputs: Mapping[str, object],
    source_updates: Mapping[str, object],
    user_id: int | None,
    update_task: Callable[..., object] = store.update,
    refresh_task: Callable[..., dict] = refresh_task_state,
    task_requires_source_sync: Callable[[dict], bool] = requires_source_sync,
    ensure_local_source_video: Callable[..., object] = ensure_source_video,
    runner=pipeline_runner,
) -> TaskStartOutcome:
    av_steps, av_step_messages = merge_av_step_maps(
        task.get("steps"),
        task.get("step_messages"),
    )
    update_task(
        task_id,
        type="translation",
        voice_gender=body.get("voice_gender", "male"),
        voice_id=None if body.get("voice_id") in (None, "", "auto") else body.get("voice_id"),
        subtitle_position=body.get("subtitle_position", "bottom"),
        subtitle_font=body.get("subtitle_font", "Impact"),
        subtitle_size=body.get("subtitle_size", 14),
        subtitle_position_y=float(body.get("subtitle_position_y", 0.68)),
        interactive_review=parse_bool(body.get("interactive_review", False)),
        pipeline_version="av",
        av_translate_inputs=dict(av_inputs),
        target_lang=av_inputs["target_language"],
        **source_updates,
        steps=av_steps,
        step_messages=av_step_messages,
    )
    task = refresh_task(task_id, task)

    if task_requires_source_sync(task):
        try:
            ensure_local_source_video(task_id, task)
        except FileNotFoundError as exc:
            return TaskStartOutcome({"error": str(exc)}, 409)
        updated_task = refresh_task(task_id, task)
        return TaskStartOutcome({"status": "source_ready", "task": updated_task})

    runner.start(task_id, user_id=user_id)
    updated_task = refresh_task(task_id, task)
    return TaskStartOutcome({"status": "started", "task": updated_task})
