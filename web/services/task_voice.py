"""Task voice confirmation workflow."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from web import store
from web.services import pipeline_runner
from web.services.task_access import refresh_task as refresh_task_state
from web.services.task_av_inputs import av_task_target_lang
from web.services.task_source_video import ensure_local_source_video as ensure_source_video
from web.services.translate_detail_protocol import normalize_confirm_voice_payload


@dataclass(frozen=True)
class TaskVoiceConfirmOutcome:
    payload: dict
    status_code: int = 200


def confirm_task_voice(
    task_id: str,
    task: dict,
    body: dict,
    *,
    user_id: int | None,
    update_task: Callable[..., object] = store.update,
    set_step: Callable[..., object] = store.set_step,
    set_current_review_step: Callable[..., object] = store.set_current_review_step,
    refresh_task: Callable[..., dict] = refresh_task_state,
    ensure_local_source_video: Callable[..., object] = ensure_source_video,
    runner=pipeline_runner,
) -> TaskVoiceConfirmOutcome:
    lang = av_task_target_lang(task)
    try:
        normalized = normalize_confirm_voice_payload(
            body=body,
            lang=lang or "",
        )
    except ValueError as exc:
        return TaskVoiceConfirmOutcome({"error": str(exc)}, 400)

    update_task(
        task_id,
        type="translation",
        selected_voice_id=normalized["voice_id"],
        selected_voice_name=normalized["voice_name"],
        voice_id=normalized["voice_id"],
        subtitle_font=normalized["subtitle_font"],
        subtitle_size=normalized["subtitle_size"],
        subtitle_position_y=normalized["subtitle_position_y"],
        subtitle_position=normalized["subtitle_position"],
        pipeline_version="av",
        target_lang=lang or task.get("target_lang"),
    )
    set_step(task_id, "voice_match", "done")
    set_current_review_step(task_id, "")

    updated_task = refresh_task(task_id, task)
    try:
        ensure_local_source_video(task_id, updated_task)
    except FileNotFoundError as exc:
        return TaskVoiceConfirmOutcome({"error": str(exc)}, 409)

    runner.resume(task_id, "alignment", user_id=user_id)
    return TaskVoiceConfirmOutcome(
        {
            "ok": True,
            "voice_id": normalized["voice_id"],
            "voice_name": normalized["voice_name"],
        }
    )
