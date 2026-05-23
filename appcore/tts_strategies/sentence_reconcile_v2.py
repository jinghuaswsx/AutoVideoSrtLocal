"""句级 reconcile_duration strategy V2（声学沙盒优化版）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import appcore.task_state as task_state
from appcore.llm_debug_runtime import save_llm_debug_calls
from appcore.runtime import (
    _build_av_debug_state,
    _build_av_localized_translation,
    _build_av_tts_segments,
    _ensure_variant_state,
    _fail_localize,
    _normalize_av_sentences,
    _rebuild_tts_full_audio_from_segments,
    _save_json,
)
from appcore.preview_artifacts import build_tts_artifact
from appcore.tts_language_guard import (
    TtsLanguageValidationError,
    validate_tts_script_language_or_raise,
)
from pipeline.audio_stitch import apply_asr_window_audio_schedule
from pipeline import speech_rate_model
from pipeline.speech_shot_alignment import apply_speech_shot_alignment

from .base import TtsConvergenceStrategy
from .sentence_reconcile import (
    _resolve_speech_rate_reference,
    _record_tts_speech_rate_sample,
    _speech_rate_diagnostics,
    _default_speech_shot_alignment_summary,
    _build_final_compose_summary,
    _should_run_speech_shot_alignment,
)

if TYPE_CHECKING:
    from appcore.runtime import PipelineRunner
    from appcore.translate_profiles.base import TranslateProfile

log = logging.getLogger(__name__)


class SentenceReconcileStrategyV2(TtsConvergenceStrategy):
    code = "sentence_reconcile_v2"
    name = "句级 reconcile V2（声学沙盒优化版）"

    def run(
        self,
        runner: "PipelineRunner",
        profile: "TranslateProfile",
        task_id: str,
        task_dir: str,
    ) -> None:
        task = task_state.get(task_id)
        if runner._complete_original_video_passthrough(
            task_id,
            task.get("video_path") or "",
            task_dir,
        ):
            return
        if (task.get("steps") or {}).get("tts") == "done":
            return

        current_step = "tts"
        try:
            from appcore.source_video import ensure_local_source_video
            from pipeline.duration_reconcile_v2 import reconcile_duration

            tts_engine = profile.get_tts_engine()
            ensure_local_source_video(task_id)
            task = task_state.get(task_id) or {}
            av_inputs = runner._resolve_av_inputs(task)
            target_language = av_inputs["target_language"]
            target_language_name = runner._target_language_name(av_inputs)

            variants = dict(task.get("variants") or {})
            variant_state = dict(variants.get("av") or {})
            av_sentences = _normalize_av_sentences(variant_state.get("sentences") or [])
            if not av_sentences:
                raise RuntimeError("缺少首版句级译文，无法进入语音收敛")

            voice, tts_voice_id, _speech_rate_voice_id = runner._resolve_av_voice(task)
            script_segments = list(task.get("normalized_script_segments") or task.get("script_segments") or [])
            shot_notes = task.get("shot_notes") or variant_state.get("shot_notes") or {}
            source_normalization = task.get("source_normalization") or variant_state.get("source_normalization") or {}

            runner._set_step(task_id, "tts", "running", f"正在生成{target_language_name}首轮配音...")
            tts_input_segments = _build_av_tts_segments(av_sentences)
            initial_rate_info = _resolve_speech_rate_reference(tts_voice_id, target_language)
            initial_char_count = sum(
                len(str(segment.get("tts_text") or segment.get("translated") or segment.get("text") or ""))
                for segment in tts_input_segments
                if isinstance(segment, dict)
            )
            from appcore.runtime._helpers import make_tts_progress_emitter

            def _on_initial_tts_progress(snapshot: dict) -> None:
                record = {
                    "mode": "sentence_reconcile_v2",
                    "round": 0,
                    "phase": "initial_audio_gen",
                    "status": "initial_audio_gen",
                    "audio_segments_done": int(snapshot.get("done") or 0),
                    "audio_segments_total": int(snapshot.get("total") or 0),
                    "audio_segments_active": int(snapshot.get("active") or 0),
                    "audio_segments_queued": int(snapshot.get("queued") or 0),
                    "target_language": target_language,
                }
                record.update(_speech_rate_diagnostics(
                    rate_info=initial_rate_info,
                    char_count=initial_char_count,
                ))
                runner._emit_duration_round(task_id, 0, "initial_audio_gen", record)

            on_progress = make_tts_progress_emitter(
                runner, task_id,
                lang_label=target_language_name,
                round_label="首轮",
                extra_state_update=_on_initial_tts_progress,
            )
            tts_output = tts_engine.synthesize_full(
                tts_input_segments,
                tts_voice_id,
                task_dir,
                variant="av",
                language_code=target_language,
                on_progress=on_progress,
            )
            measured_chars, measured_duration, speech_rate_recorded = _record_tts_speech_rate_sample(
                voice_id=tts_voice_id,
                target_language=target_language,
                segments=list((tts_output or {}).get("segments") or []),
            )
            if measured_chars > 0 and measured_duration > 0:
                diagnostics = _speech_rate_diagnostics(
                    rate_info=initial_rate_info,
                    char_count=measured_chars,
                    audio_duration=measured_duration,
                )
                if diagnostics.get("speech_rate_source"):
                    diagnostics["speech_rate_actual_recorded"] = speech_rate_recorded
                    diagnostics["speech_rate_reference_switched"] = (
                        speech_rate_recorded
                        and diagnostics.get("speech_rate_source") != "actual_tts"
                    )
                runner._emit_duration_round(
                    task_id,
                    0,
                    "initial_measure",
                    {
                        "mode": "sentence_reconcile_v2",
                        "round": 0,
                        "phase": "initial_measure",
                        "status": "initial_measure",
                        "audio_segments_done": len((tts_output or {}).get("segments") or []),
                        "audio_segments_total": len((tts_output or {}).get("segments") or []),
                        "audio_segments_active": 0,
                        "audio_segments_queued": 0,
                        "target_language": target_language,
                        "audio_duration": round(measured_duration, 3),
                        **diagnostics,
                    },
                )

            av_tts_text = " ".join(
                str(segment.get("tts_text") or segment.get("translated") or "").strip()
                for segment in tts_input_segments
                if segment.get("tts_text") or segment.get("translated")
            ).strip()
            tts_debug_calls: list[dict] = []
            try:
                av_language_check = validate_tts_script_language_or_raise(
                    text=av_tts_text,
                    target_language=target_language,
                    user_id=runner.user_id,
                    project_id=task_id,
                    variant="av",
                    round_index=1,
                )
            except TtsLanguageValidationError as exc:
                error_result = dict(exc.result or {"is_target_language": False, "reason": str(exc)})
                tts_debug_calls.extend(error_result.pop("_llm_debug_calls", []))
                save_llm_debug_calls(
                    task_id=task_id,
                    task_dir=task_dir,
                    step="tts",
                    calls=tts_debug_calls,
                    save_json=_save_json,
                )
                _save_json(
                    task_dir,
                    "tts_language_check.av.json",
                    error_result,
                )
                raise
            tts_debug_calls.extend(av_language_check.pop("_llm_debug_calls", []))
            _save_json(task_dir, "tts_language_check.av.json", av_language_check)

            runner._set_step(task_id, "tts", "running", "正在按句联合收敛文案与音频时长 (V2优化版)...")
            def _on_reconcile_progress(record: dict) -> None:
                round_index = int(record.get("round") or 1)
                phase = str(record.get("phase") or "sentence_progress")
                asr_index = record.get("asr_index")
                status = record.get("status") or ""
                active_attempt = record.get("active_attempt")
                active_tts_attempt = record.get("active_tts_attempt")
                max_text_attempts = record.get("max_text_rewrite_attempts")
                max_tts_attempts = record.get("max_tts_regenerate_attempts")
                if phase == "rewrite_start":
                    attempt_label = f"第 {active_attempt}/{max_text_attempts} 次" if max_text_attempts else f"第 {active_attempt} 次"
                    message = f"正在重新翻译句 {asr_index} · {attempt_label} (沙盒预测) · {record.get('active_action') or status}"
                elif phase == "tts_regen_start":
                    attempt_label = f"第 {active_tts_attempt}/{max_tts_attempts} 次" if max_tts_attempts else f"第 {active_tts_attempt} 次"
                    message = f"正在物理合成最优句 {asr_index} 音频 · {attempt_label}"
                elif phase == "rewrite_attempt":
                    message = f"句 {asr_index} · 译文已生成并沙盒测速 · {status}"
                elif phase == "sentence_done":
                    message = f"句 {asr_index} · 收敛处理完成 (V2) · {status}"
                else:
                    message = f"正在按句联合收敛时长 (V2) · 句 {asr_index} · {status}"
                runner._emit_substep_msg(
                    task_id,
                    "tts",
                    message,
                )
                runner._emit_duration_round(task_id, round_index, phase, record)

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
                on_progress=_on_reconcile_progress,
            )
            final_sentences = apply_asr_window_audio_schedule(
                final_sentences,
                max_gap=0.25,
                preserve_gap_threshold=1.0,
            )
            alignment_summary = _default_speech_shot_alignment_summary(final_sentences)
            task_for_alignment = task_state.get(task_id) or task
            if _should_run_speech_shot_alignment(task_for_alignment):
                final_sentences, alignment_summary = apply_speech_shot_alignment(
                    final_sentences,
                    shots=list(task_for_alignment.get("shots") or []),
                    scene_cuts=list(task_for_alignment.get("scene_cuts") or []),
                    video_duration=(
                        task_for_alignment.get("video_duration")
                        or task_for_alignment.get("original_video_duration")
                    ),
                )
            for sentence in final_sentences:
                if isinstance(sentence, dict):
                    tts_debug_calls.extend(sentence.pop("_llm_debug_calls", []))
            save_llm_debug_calls(
                task_id=task_id,
                task_dir=task_dir,
                step="tts",
                calls=tts_debug_calls,
                save_json=_save_json,
            )
            final_localized_translation = _build_av_localized_translation(final_sentences)
            final_tts_segments = _build_av_tts_segments(final_sentences)
            task_for_audio = task_state.get(task_id) or task
            final_full_audio_path = _rebuild_tts_full_audio_from_segments(
                task_dir,
                final_tts_segments,
                variant="av",
                total_duration=(
                    task_for_audio.get("video_duration")
                    or task_for_audio.get("original_video_duration")
                ),
            )
            from appcore.tts_strategies.sentence_reconcile import _copy_clip_metadata_to_sentences
            _copy_clip_metadata_to_sentences(final_sentences, final_tts_segments)
            av_debug = _build_av_debug_state(final_sentences, source_normalization=source_normalization)
            final_compose_summary = _build_final_compose_summary(
                task_state.get(task_id) or task,
                final_sentences,
                final_tts_segments,
                audio_path=final_full_audio_path,
                max_compact_gap=0.25,
            )
            final_compose_summary.update(alignment_summary)
            av_debug["final_compose_summary"] = final_compose_summary
            final_tts_output = {
                "full_audio_path": final_full_audio_path,
                "segments": final_tts_segments,
            }

            task = task_state.get(task_id) or task
            variants, variant_state = _ensure_variant_state(task, "av")
            variant_state.update(
                {
                    "sentences": final_sentences,
                    "localized_translation": final_localized_translation,
                    "tts_result": final_tts_output,
                    "tts_audio_path": final_full_audio_path,
                    "voice_id": voice.get("id") or tts_voice_id,
                    "av_debug": av_debug,
                    "source_normalization": source_normalization,
                    "audio_timeline_mode": "asr_window_primary",
                    "max_compact_gap": 0.25,
                    "final_compose_summary": final_compose_summary,
                    "speech_shot_alignment": alignment_summary,
                }
            )
            variant_state.setdefault("preview_files", {})["tts_full_audio"] = final_full_audio_path
            variant_state.setdefault("artifacts", {})["tts"] = build_tts_artifact(final_tts_segments)
            variants["av"] = variant_state
            task_state.update(
                task_id,
                variants=variants,
                segments=final_tts_segments,
                tts_audio_path=final_full_audio_path,
                voice_id=voice.get("id") or tts_voice_id,
                localized_translation=final_localized_translation,
                tts_duration_status=final_compose_summary["status"],
                final_compose_summary=final_compose_summary,
                speech_shot_alignment=alignment_summary,
                av_debug=av_debug,
                audio_timeline_mode="asr_window_primary",
                max_compact_gap=0.25,
            )
            task_state.set_preview_file(task_id, "tts_full_audio", final_full_audio_path)
            task_state.set_artifact(task_id, "tts", build_tts_artifact(final_tts_segments))
            _save_json(task_dir, "localized_translation.av.final.json", final_localized_translation)
            _save_json(task_dir, "tts_result.av.json", final_tts_segments)
            runner._set_step(task_id, "tts", "done", f"{target_language_name} 配音收敛完成 (V2)")
        except Exception as exc:
            _fail_localize(task_id, runner, current_step, str(exc))
