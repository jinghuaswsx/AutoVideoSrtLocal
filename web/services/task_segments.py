"""Task translation segment confirmation workflow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from appcore.runtime import _build_av_localized_translation
from web import store
from web.services import pipeline_runner
from web.services.task_access import refresh_task as refresh_task_state
from web.services.task_av_rewrite import build_translate_compare_artifact


@dataclass(frozen=True)
class TaskSegmentsOutcome:
    payload: dict
    status_code: int = 200


def _build_av_sentences(updated_task: dict, segments: list[dict]) -> list[dict]:
    variant_state = dict((updated_task.get("variants") or {}).get("av") or {})
    existing_sentences = [
        dict(item)
        for item in (variant_state.get("sentences") or [])
        if isinstance(item, dict)
    ]
    existing_by_asr = {
        int(sentence.get("asr_index", sentence.get("index", idx))): sentence
        for idx, sentence in enumerate(existing_sentences)
    }
    av_sentences = []
    for fallback_index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        asr_index = int(segment.get("asr_index", segment.get("index", fallback_index)))
        base = dict(existing_by_asr.get(asr_index, {}))
        translated = str(segment.get("translated") or segment.get("target_text") or segment.get("text") or "")
        base.update(
            {
                "asr_index": asr_index,
                "text": translated,
                "est_chars": len(translated),
                "start_time": float(segment.get("start_time", base.get("start_time", 0.0)) or 0.0),
                "end_time": float(segment.get("end_time", base.get("end_time", 0.0)) or 0.0),
                "source_text": str(segment.get("text") or base.get("source_text") or ""),
            }
        )
        if "target_duration" not in base:
            base["target_duration"] = max(0.0, base["end_time"] - base["start_time"])
        av_sentences.append(base)
    return av_sentences


def confirm_task_segments(
    task_id: str,
    task: dict,
    body: Mapping[str, object],
    *,
    user_id: int | None,
    confirm_segments: Callable[..., object] = store.confirm_segments,
    refresh_task: Callable[..., dict] = refresh_task_state,
    build_av_localized_translation: Callable[..., dict] = _build_av_localized_translation,
    update_variant: Callable[..., object] = store.update_variant,
    update_task: Callable[..., object] = store.update,
    build_translate_artifact: Callable[..., dict] = build_translate_compare_artifact,
    set_artifact: Callable[..., object] = store.set_artifact,
    set_current_review_step: Callable[..., object] = store.set_current_review_step,
    set_step: Callable[..., object] = store.set_step,
    set_step_message: Callable[..., object] = store.set_step_message,
    runner=pipeline_runner,
) -> TaskSegmentsOutcome:
    segments = body.get("segments")
    if not isinstance(segments, list):
        return TaskSegmentsOutcome({"error": "segments required"}, 400)

    confirm_segments(task_id, segments)
    updated_task = refresh_task(task_id, task)
    if str(updated_task.get("pipeline_version") or "").strip() == "av":
        try:
            av_sentences = _build_av_sentences(updated_task, segments)
        except (TypeError, ValueError) as exc:
            return TaskSegmentsOutcome({"error": f"invalid segments: {exc}"}, 400)
        localized_translation = build_av_localized_translation(av_sentences)
        update_variant(
            task_id,
            "av",
            sentences=av_sentences,
            localized_translation=localized_translation,
        )
        update_task(task_id, localized_translation=localized_translation, segments=av_sentences)
        updated_task = refresh_task(task_id, updated_task)

    set_artifact(task_id, "translate", build_translate_artifact(updated_task))
    set_current_review_step(task_id, "")
    set_step(task_id, "translate", "done")
    set_step_message(task_id, "translate", "翻译确认完成")
    runner.resume(task_id, "tts", user_id=user_id)
    return TaskSegmentsOutcome({"status": "ok"})
