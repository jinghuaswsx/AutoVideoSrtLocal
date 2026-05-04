"""Framework-agnostic pipeline runner.

No Flask, no socketio, no web imports.
Uses EventBus to publish status events consumed by any adapter (web, desktop).
"""
from __future__ import annotations

import json
import logging
import math
import os
import uuid
from datetime import datetime

import config

log = logging.getLogger(__name__)
logger = logging.getLogger(__name__)

import appcore.task_state as task_state
from appcore.api_keys import resolve_jianying_project_root
from appcore import ai_billing
from appcore import tts_generation_stats
from appcore.cancellation import OperationCancelled, throw_if_cancel_requested
from appcore.events import (
    EVT_ALIGNMENT_READY,
    EVT_ASR_RESULT,
    EVT_CAPCUT_READY,
    EVT_ENGLISH_ASR_RESULT,
    EVT_PIPELINE_DONE,
    EVT_PIPELINE_ERROR,
    EVT_STEP_UPDATE,
    EVT_SUBTITLE_READY,
    EVT_TRANSLATE_RESULT,
    EVT_TTS_SCRIPT_READY,
    EVT_VOICE_MATCH_READY,
    Event,
    EventBus,
)
from appcore.preview_artifacts import (
    build_alignment_artifact,
    build_analysis_artifact,
    build_asr_artifact,
    build_compose_artifact,
    build_export_artifact,
    build_extract_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
    build_tts_artifact,
)
from appcore.tts_language_guard import (
    TtsLanguageValidationError,
    extract_tts_script_text,
    validate_tts_script_language_or_raise,
)


from ._helpers import (
    _VALID_TRANSLATE_PREFS,
    _skip_legacy_artifact_upload,
    _save_json,
    _count_visible_chars,
    _join_utterance_text,
    _resolve_original_video_passthrough,
    _is_original_video_passthrough,
    _build_review_segments,
    _translate_billing_provider,
    _translate_billing_model,
    _log_translate_billing,
    _llm_request_payload,
    _llm_response_payload,
    _seconds_to_request_units,
    _resolve_translate_provider,
    _resolve_task_translate_provider,
    _lang_display,
    _is_av_pipeline_task,
    _av_target_lang,
    _tts_final_target_range,
    _in_speedup_window,
    _speedup_ratio,
    _DEFAULT_WPS,
    _compute_next_target,
    _distance_to_duration_range,
    _fit_tts_segments_to_duration,
    _trim_tts_metadata_to_segments,
)


from ._pipeline_runner import PipelineRunner


# Re-export AV helpers + dispatchers from sub-modules so existing
# callers (web routes, runtime_de/fr/ja/multi/omni/v2 subclasses,
# tools, tests) keep working.
from ._av_helpers import (
    _default_av_variant_state,
    _ensure_variant_state,
    _join_source_full_text,
    _load_json_if_exists,
    _restore_av_localize_outputs_from_files,
    _normalize_av_sentences,
    _build_av_localized_translation,
    _build_av_tts_segments,
    _rebuild_tts_full_audio_from_segments,
    _build_av_debug_state,
    _fail_localize,
    _new_silent_runner,
)



def dispatch_localize(task_id: str, runner: "PipelineRunner" | None = None):
    task = task_state.get(task_id) or {}
    task_type = str(task.get("type") or "").strip()
    pipeline_version = str(task.get("pipeline_version") or "").strip()
    if task_type == "av_translate" or pipeline_version == "av":
        return run_av_localize(task_id, runner=runner, variant="av")
    return run_localize(task_id, runner=runner, variant="normal")


def run_localize(task_id: str, runner: "PipelineRunner" | None = None, variant: str = "normal") -> None:
    del variant  # legacy path always reuses existing normal/hook_cta flow
    runner = runner or _new_silent_runner()

    from appcore.source_video import ensure_local_source_video

    ensure_local_source_video(task_id)
    task = task_state.get(task_id) or {}
    task_dir = task.get("task_dir", "")

    runner._step_translate(task_id)
    current = task_state.get(task_id) or {}
    if current.get("steps", {}).get("translate") == "waiting":
        return

    runner._step_tts(task_id, task_dir)
    current = task_state.get(task_id) or {}
    if current.get("steps", {}).get("tts") == "waiting":
        return

    runner._step_subtitle(task_id, task_dir)


def run_av_localize(task_id: str, runner: "PipelineRunner" | None = None, variant: str = "av") -> None:
    runner = runner or _new_silent_runner()
    if config.AV_LOCALIZE_FALLBACK:
        return run_localize(task_id, runner=runner, variant="normal")

    current_step = "translate"

    try:
        from appcore.source_video import ensure_local_source_video
        import importlib
        from pipeline.av_source_normalize import normalize_source_segments
        from pipeline.av_translate import generate_av_localized_translation
        from pipeline.av_subtitle_units import build_subtitle_units_from_sentences
        from pipeline.duration_reconcile import reconcile_duration
        from pipeline.shot_notes import build_fallback_shot_notes, generate_shot_notes
        from pipeline.subtitle import build_srt_from_chunks, save_srt
        from pipeline.tts import generate_full_audio

        ensure_local_source_video(task_id)
        task = task_state.get(task_id) or {}
        if not task:
            raise KeyError(f"task not found: {task_id}")

        video_path = task.get("video_path", "")
        task_dir = task.get("task_dir", "")
        script_segments = list(task.get("script_segments") or [])
        av_inputs = dict(task.get("av_translate_inputs") or {})
        target_language = str(av_inputs.get("target_language") or "").strip()
        target_market = str(av_inputs.get("target_market") or "").strip()
        target_language_name = str(av_inputs.get("target_language_name") or target_language or "目标语").strip()

        missing = [
            field_name
            for field_name, value in (
                ("target_language", target_language),
                ("target_market", target_market),
            )
            if not value
        ]
        if missing:
            _fail_localize(task_id, runner, "translate", f"缺少必填字段: {', '.join(missing)}")
            return

        loc_mod = importlib.import_module(runner.localization_module)
        voice = runner._resolve_voice(task, loc_mod)
        tts_voice_id = voice.get("elevenlabs_voice_id") or voice.get("id")
        speech_rate_voice_id = voice.get("id") or tts_voice_id
        if not tts_voice_id:
            _fail_localize(task_id, runner, "tts", "未找到可用音色，无法继续生成配音")
            return

        source_full_text = _join_source_full_text(script_segments)
        if _restore_av_localize_outputs_from_files(
            task_id,
            runner=runner,
            task=task,
            task_dir=task_dir,
            variant=variant,
            target_language=target_language,
            target_language_name=target_language_name,
            source_full_text=source_full_text,
        ):
            return

        raw_script_segments = script_segments
        runner._set_step(task_id, "translate", "running", "正在纯净化原文 ASR...")
        source_language = (
            task.get("source_language")
            or task.get("detected_source_language")
            or "auto"
        )
        source_normalization = normalize_source_segments(
            script_segments=raw_script_segments,
            source_language=source_language,
            av_inputs=av_inputs,
            user_id=runner.user_id,
            project_id=task_id,
        )
        normalized_script_segments = list(source_normalization.get("segments") or raw_script_segments)
        normalized_source_full_text = _join_source_full_text(normalized_script_segments)
        task_state.update(
            task_id,
            raw_script_segments=raw_script_segments,
            normalized_script_segments=normalized_script_segments,
            source_normalization=source_normalization,
            source_full_text_raw=source_full_text,
            source_full_text_zh=normalized_source_full_text,
        )
        _save_json(task_dir, "source_normalization.av.json", source_normalization)
        script_segments = normalized_script_segments
        source_full_text = normalized_source_full_text
        task = task_state.get(task_id) or task

        runner._set_step(task_id, "translate", "running", "正在分析画面并生成笔记...")
        try:
            shot_notes = generate_shot_notes(
                video_path=video_path,
                script_segments=script_segments,
                target_language=target_language_name,
                target_market=target_market,
                user_id=runner.user_id,
                project_id=task_id,
                max_retries=0,
            )
        except Exception as exc:
            log.warning("AV shot notes unavailable for task %s; using timeline fallback", task_id, exc_info=True)
            runner._set_step(task_id, "translate", "running", "画面分析暂不可用，已使用 ASR 时间轴兜底继续翻译...")
            shot_notes = build_fallback_shot_notes(script_segments, reason=str(exc))
        task = task_state.get(task_id) or task
        variants, variant_state = _ensure_variant_state(task, variant)
        task_state.update(task_id, shot_notes=shot_notes, variants=variants)
        _save_json(task_dir, "shot_notes.json", shot_notes)

        runner._set_step(task_id, "translate", "running", "正在生成音画同步译文...")
        av_output = generate_av_localized_translation(
            script_segments=script_segments,
            shot_notes=shot_notes,
            av_inputs=av_inputs,
            voice_id=speech_rate_voice_id,
            user_id=runner.user_id,
            project_id=task_id,
        )
        av_sentences = _normalize_av_sentences((av_output or {}).get("sentences") or [])
        localized_translation = _build_av_localized_translation(av_sentences)
        task = task_state.get(task_id) or task
        variants, variant_state = _ensure_variant_state(task, variant)
        variant_state["source_normalization"] = source_normalization
        variant_state["sentences"] = av_sentences
        variant_state["localized_translation"] = localized_translation
        variants[variant] = variant_state
        task_state.update(
            task_id,
            variants=variants,
            shot_notes=shot_notes,
            localized_translation=localized_translation,
            source_full_text_zh=source_full_text,
        )
        task_state.set_artifact(
            task_id,
            "translate",
            build_translate_artifact(
                source_full_text,
                localized_translation,
                target_language=target_language,
            ),
        )
        _save_json(task_dir, f"localized_translation.{variant}.json", localized_translation)
        runner._emit(
            task_id,
            EVT_TRANSLATE_RESULT,
            {
                "source_full_text_zh": source_full_text,
                "localized_translation": localized_translation,
                "segments": av_sentences,
            },
        )
        runner._set_step(task_id, "translate", "done", f"{target_language_name}音画同步翻译完成")

        current_step = "tts"
        runner._set_step(task_id, "tts", "running", f"正在生成{target_language_name}配音...")
        tts_input_segments = _build_av_tts_segments(av_sentences)
        tts_output = generate_full_audio(
            tts_input_segments,
            tts_voice_id,
            task_dir,
            variant=variant,
            language_code=target_language,
        )
        av_tts_text = " ".join(
            str(segment.get("tts_text") or segment.get("translated") or "").strip()
            for segment in tts_input_segments
            if segment.get("tts_text") or segment.get("translated")
        ).strip()
        av_language_check_path = f"tts_language_check.{variant}.json"
        try:
            av_language_check = validate_tts_script_language_or_raise(
                text=av_tts_text,
                target_language=target_language,
                user_id=runner.user_id,
                project_id=task_id,
                variant=variant,
                round_index=1,
            )
        except TtsLanguageValidationError as exc:
            _save_json(
                task_dir,
                av_language_check_path,
                exc.result or {"is_target_language": False, "reason": str(exc)},
            )
            raise
        _save_json(task_dir, av_language_check_path, av_language_check)
        final_sentences = reconcile_duration(
            task=task_state.get(task_id) or task,
            av_output={"sentences": av_sentences},
            tts_output=tts_output,
            voice_id=tts_voice_id,
            target_language=target_language,
            av_inputs=av_inputs,
            shot_notes=shot_notes,
            script_segments=script_segments,
            user_id=runner.user_id,
            project_id=task_id,
        )
        av_debug = _build_av_debug_state(final_sentences, source_normalization=source_normalization)
        final_localized_translation = _build_av_localized_translation(final_sentences)
        final_tts_segments = _build_av_tts_segments(final_sentences)
        final_full_audio_path = _rebuild_tts_full_audio_from_segments(task_dir, final_tts_segments, variant=variant)
        subtitle_units = build_subtitle_units_from_sentences(
            final_sentences,
            mode=str(av_inputs.get("sync_granularity") or "hybrid"),
        )
        final_tts_output = {
            "full_audio_path": final_full_audio_path,
            "segments": final_tts_segments,
        }
        task = task_state.get(task_id) or task
        variants, variant_state = _ensure_variant_state(task, variant)
        variant_state.update(
            {
                "sentences": final_sentences,
                "localized_translation": final_localized_translation,
                "tts_result": final_tts_output,
                "tts_audio_path": final_tts_output["full_audio_path"],
                "voice_id": voice.get("id") or tts_voice_id,
                "av_debug": av_debug,
                "source_normalization": source_normalization,
                "subtitle_units": subtitle_units,
            }
        )
        variant_state.setdefault("preview_files", {})["tts_full_audio"] = final_tts_output["full_audio_path"]
        variant_state.setdefault("artifacts", {})["tts"] = build_tts_artifact(final_tts_segments)
        variants[variant] = variant_state
        task_state.update(
            task_id,
            variants=variants,
            segments=final_tts_segments,
            tts_audio_path=final_tts_output["full_audio_path"],
            voice_id=voice.get("id") or tts_voice_id,
            localized_translation=final_localized_translation,
            tts_duration_status="done",
        )
        task_state.set_preview_file(task_id, "tts_full_audio", final_tts_output["full_audio_path"])
        task_state.set_artifact(task_id, "tts", build_tts_artifact(final_tts_segments))
        _save_json(task_dir, f"tts_result.{variant}.json", final_tts_segments)
        runner._set_step(task_id, "tts", "done", f"{target_language_name}配音生成完成")

        current_step = "subtitle"
        runner._set_step(task_id, "subtitle", "running", f"正在生成{target_language_name}字幕...")
        srt_content = build_srt_from_chunks(subtitle_units)
        srt_path = save_srt(srt_content, os.path.join(task_dir, f"subtitle.{variant}.srt"))
        task = task_state.get(task_id) or task
        variants, variant_state = _ensure_variant_state(task, variant)
        variant_state["srt_path"] = srt_path
        variant_state["subtitle_units"] = subtitle_units
        variant_state["corrected_subtitle"] = {"chunks": subtitle_units, "srt_content": srt_content}
        variants[variant] = variant_state
        task_state.update(
            task_id,
            variants=variants,
            srt_path=srt_path,
            corrected_subtitle={"chunks": subtitle_units, "srt_content": srt_content},
        )
        task_state.set_preview_file(task_id, "srt", srt_path)
        task_state.set_artifact(
            task_id,
            "subtitle",
            build_subtitle_artifact(srt_content, target_language=target_language),
        )
        _save_json(task_dir, f"corrected_subtitle.{variant}.json", {"chunks": subtitle_units, "srt_content": srt_content})
        runner._emit(task_id, EVT_SUBTITLE_READY, {"srt": srt_content})
        runner._set_step(task_id, "subtitle", "done", f"{target_language_name}字幕生成完成")

        # Fire-and-forget translation-quality assessment. Failures don't block compose.
        try:
            from appcore import quality_assessment as _qa
            _qa.trigger_assessment(
                task_id=task_id, project_type=runner.project_type,
                triggered_by="auto", user_id=runner.user_id,
            )
        except Exception:  # noqa: BLE001 — assessment failures must not break pipeline
            log.warning("[%s] failed to trigger quality assessment for task %s",
                        runner.project_type, task_id, exc_info=True)
    except Exception as exc:
        _fail_localize(task_id, runner, current_step, str(exc))


def run_analysis_only(
    task_id: str,
    runner: "PipelineRunner",
) -> None:
    """单独执行 AI 视频分析步骤，不影响任务整体 status。

    所有异常只更新 steps.analysis 为 error、记录 step_message；
    绝不触碰 task 整体 status 与 error 字段。
    """
    try:
        runner._step_analysis(task_id)
    except Exception as exc:
        log.exception("AI 分析执行失败 task_id=%s", task_id)
        try:
            runner._set_step(task_id, "analysis", "error", f"AI 分析失败：{exc}")
        except Exception:
            pass
