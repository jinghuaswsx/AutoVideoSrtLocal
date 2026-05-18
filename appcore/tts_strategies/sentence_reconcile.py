"""句级 reconcile_duration strategy（av_sync 风格）。

PR6: 把 ``AvSyncProfile.tts`` 的 body 搬到 strategy。新 av_sync 变种
（多人声、shot-aware 重写策略等）只需新写一个 strategy 子类即可，不必
派生 profile 或 runner。
"""
from __future__ import annotations

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
from pipeline.audio_stitch import apply_compact_audio_schedule
from pipeline.speech_shot_alignment import apply_speech_shot_alignment

from .base import TtsConvergenceStrategy

if TYPE_CHECKING:
    from appcore.runtime import PipelineRunner
    from appcore.translate_profiles.base import TranslateProfile


def _float_value(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _first_positive(*values) -> float | None:
    for value in values:
        numeric = _float_value(value, 0.0)
        if numeric > 0:
            return numeric
    return None


def _max_timeline_end(rows: list[dict]) -> float | None:
    max_end = 0.0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        end = _float_value(
            row.get("source_end_time", row.get("end_time", row.get("audio_end_time", 0.0))),
            0.0,
        )
        if end > max_end:
            max_end = end
    return max_end if max_end > 0 else None


def _max_audio_content_end(rows: list[dict]) -> float | None:
    max_end = 0.0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        end = _float_value(
            row.get("audio_end_time", row.get("end_time", row.get("tts_duration", 0.0))),
            0.0,
        )
        if end > max_end:
            max_end = end
    return max_end if max_end > 0 else None


def _sentence_index(row: dict, fallback: int) -> int:
    value = row.get("asr_index", row.get("index", fallback))
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _semantic_issue(row: dict) -> bool:
    omitted = [str(term).strip() for term in (row.get("omitted_source_terms") or []) if str(term).strip()]
    return row.get("coverage_ok") is False or bool(omitted)


def _copy_clip_metadata_to_sentences(sentences: list[dict], segments: list[dict]) -> None:
    segment_by_index = {
        str(segment.get("asr_index", segment.get("index", index))): segment
        for index, segment in enumerate(segments or [])
        if isinstance(segment, dict)
    }
    clip_fields = (
        "audio_clipped",
        "audio_clip_reason",
        "audio_clip_duration",
        "audio_clipped_seconds",
    )
    for fallback, sentence in enumerate(sentences or []):
        if not isinstance(sentence, dict):
            continue
        segment = segment_by_index.get(str(sentence.get("asr_index", sentence.get("index", fallback))))
        if not segment:
            continue
        for field in clip_fields:
            if field in segment:
                sentence[field] = segment[field]


def _should_run_speech_shot_alignment(task: dict) -> bool:
    cfg = task.get("plugin_config") if isinstance(task.get("plugin_config"), dict) else {}
    return (
        task.get("type") in {"omni_translate", "english_redub"}
        and bool(cfg.get("shot_decompose"))
        and cfg.get("tts_strategy") == "sentence_reconcile"
    )


def _default_speech_shot_alignment_summary(
    final_sentences: list[dict],
    *,
    status: str = "skipped_not_omni_sentence_reconcile",
) -> dict:
    return {
        "speech_shot_alignment_enabled": False,
        "speech_shot_alignment_applied": False,
        "speech_shot_alignment_status": status,
        "speech_shot_alignment_analyzed_boundaries": max(0, len(final_sentences or []) - 1),
        "speech_shot_alignment_decisions": [],
        "shot_anchor_cut_count": 0,
        "shot_anchor_extra_silence_total": 0.0,
        "shot_anchor_aligned_boundary_count": 0,
        "shot_anchor_extra_silence_budget": 0.0,
        "shot_anchor_skip_reasons": {},
        "speech_shot_alignment_max_final_gap": round(
            max(
                (
                    _float_value(sentence.get("audio_gap_before"), 0.0)
                    for sentence in (final_sentences or [])
                    if isinstance(sentence, dict)
                ),
                default=0.0,
            ),
            3,
        ),
    }


def _build_final_compose_summary(
    task: dict,
    final_sentences: list[dict],
    final_tts_segments: list[dict],
    *,
    audio_path: str,
    max_compact_gap: float,
) -> dict:
    source_end = _max_timeline_end(final_tts_segments) or _max_timeline_end(final_sentences)
    video_duration = _first_positive(
        task.get("video_duration"),
        task.get("original_video_duration"),
        source_end,
    )
    effective_speech_duration = sum(
        max(0.0, _float_value(sentence.get("tts_duration"), 0.0))
        for sentence in (final_sentences or [])
        if isinstance(sentence, dict)
    )
    target_duration = sum(
        max(0.0, _float_value(sentence.get("target_duration"), 0.0))
        for sentence in (final_sentences or [])
        if isinstance(sentence, dict)
    )
    silence_gap_duration = sum(
        max(0.0, _float_value(sentence.get("audio_gap_before"), 0.0))
        for sentence in (final_sentences or [])
        if isinstance(sentence, dict)
    )
    clipped_segments = [
        segment
        for segment in (final_tts_segments or [])
        if isinstance(segment, dict) and segment.get("audio_clipped")
    ]
    truncated_seconds = round(
        sum(max(0.0, _float_value(segment.get("audio_clipped_seconds"), 0.0)) for segment in clipped_segments),
        3,
    )
    affected_sentence_indices = [
        _sentence_index(segment, index)
        for index, segment in enumerate(clipped_segments)
    ]
    has_best_effort = any(
        bool(sentence.get("best_effort"))
        for sentence in (final_sentences or [])
        if isinstance(sentence, dict)
    )
    semantic_warning_count = sum(
        1
        for sentence in (final_sentences or [])
        if isinstance(sentence, dict) and _semantic_issue(sentence)
    )
    warning_sentence_count = sum(
        1
        for sentence in (final_sentences or [])
        if isinstance(sentence, dict)
        and (
            bool(sentence.get("best_effort"))
            or str(sentence.get("status") or "").startswith("warning")
            or _semantic_issue(sentence)
        )
    )
    if clipped_segments:
        status = "clipped_output"
        status_label = "裁剪输出"
    elif has_best_effort:
        status = "fallback_output"
        status_label = "兜底输出"
    elif semantic_warning_count:
        status = "review_needed"
        status_label = "需人工复核"
    else:
        status = "fully_converged"
        status_label = "完全收敛"

    final_output_audio_duration = _first_positive(video_duration, source_end, effective_speech_duration) or 0.0
    final_video_duration = _first_positive(video_duration, final_output_audio_duration) or 0.0
    audio_content_duration = _first_positive(
        _max_audio_content_end(final_sentences),
        effective_speech_duration + silence_gap_duration,
        effective_speech_duration,
    ) or 0.0
    tail_padding_duration = max(0.0, final_output_audio_duration - audio_content_duration)
    if clipped_segments:
        final_processing_label = (
            f"最终输出 {final_output_audio_duration:.1f}s；"
            f"原始排布 {audio_content_duration:.1f}s（口播 {effective_speech_duration:.1f}s + "
            f"句间静音 {silence_gap_duration:.1f}s + 尾部静音 {tail_padding_duration:.1f}s）；"
            f"已截断 {truncated_seconds:.1f}s"
        )
    else:
        final_processing_label = (
            f"最终输出 {final_output_audio_duration:.1f}s = "
            f"口播 {effective_speech_duration:.1f}s + "
            f"句间静音 {silence_gap_duration:.1f}s + "
            f"尾部静音 {tail_padding_duration:.1f}s；无截断"
        )
    notes = [
        "按每句 audio_start_time 放置音频，句间由静音补齐；输出时长由最终时间轴限制。",
    ]
    if clipped_segments:
        notes.append("存在超出句子窗口或最终时间轴的音频片段，已先裁剪再参与视频合成。")
        if any(
            str(segment.get("final_fallback_action") or "") == "clip_overlong"
            for segment in clipped_segments
        ):
            notes.append("超长截断兜底：句级收敛最终仍超过目标窗口，已按最终时间轴裁剪后输出。")
    elif warning_sentence_count:
        notes.append("存在语义或时长软问题，任务继续输出，结果需复核。")

    return {
        "status": status,
        "status_label": status_label,
        "video_duration": round(video_duration or 0.0, 3),
        "target_sentence_duration": round(target_duration, 3),
        "effective_speech_duration": round(effective_speech_duration, 3),
        "audio_content_duration": round(audio_content_duration, 3),
        "tail_padding_duration": round(tail_padding_duration, 3),
        "final_processing_label": final_processing_label,
        "final_output_audio_duration": round(final_output_audio_duration, 3),
        "final_audio_duration": round(final_output_audio_duration, 3),
        "final_video_duration": round(final_video_duration, 3),
        "target_timeline_duration": round(final_output_audio_duration, 3),
        "source_timeline_duration": round(source_end or 0.0, 3),
        "audio_path": audio_path,
        "timeline_mode": "compact_asr_primary",
        "stitching_method": "按句 audio_start_time 放置，句间静音补齐，最终由 ffmpeg -t 限制到目标时间轴。",
        "max_compact_gap": round(float(max_compact_gap), 3),
        "silence_gap_count": sum(
            1
            for sentence in (final_sentences or [])
            if isinstance(sentence, dict) and _float_value(sentence.get("audio_gap_before"), 0.0) > 0.001
        ),
        "silence_gap_duration": round(silence_gap_duration, 3),
        "audio_truncated": bool(clipped_segments),
        "overflow_clipped": bool(clipped_segments),
        "truncation_seconds": truncated_seconds,
        "affected_sentence_indices": affected_sentence_indices,
        "clipped_segments": [
            {
                "asr_index": _sentence_index(segment, index),
                "reason": segment.get("audio_clip_reason") or "",
                "clipped_seconds": round(_float_value(segment.get("audio_clipped_seconds"), 0.0), 3),
                "clip_duration": round(_float_value(segment.get("audio_clip_duration"), 0.0), 3),
                "final_fallback_action": segment.get("final_fallback_action") or "",
            }
            for index, segment in enumerate(clipped_segments)
        ],
        "has_best_effort": has_best_effort,
        "warning_sentence_count": warning_sentence_count,
        "semantic_warning_count": semantic_warning_count,
        "review_required": bool(has_best_effort or semantic_warning_count or clipped_segments),
        "notes": notes,
    }


class SentenceReconcileStrategy(TtsConvergenceStrategy):
    code = "sentence_reconcile"
    name = "句级 reconcile（shot_notes-aware）"

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
            from pipeline.duration_reconcile import reconcile_duration

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
            from appcore.runtime._helpers import make_tts_progress_emitter

            def _on_initial_tts_progress(snapshot: dict) -> None:
                record = {
                    "mode": "sentence_reconcile",
                    "round": 0,
                    "phase": "initial_audio_gen",
                    "status": "initial_audio_gen",
                    "audio_segments_done": int(snapshot.get("done") or 0),
                    "audio_segments_total": int(snapshot.get("total") or 0),
                    "audio_segments_active": int(snapshot.get("active") or 0),
                    "audio_segments_queued": int(snapshot.get("queued") or 0),
                    "target_language": target_language,
                }
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

            runner._set_step(task_id, "tts", "running", "正在按句联合收敛文案与音频时长...")
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
                    message = f"正在重新翻译句 {asr_index} · {attempt_label} · {record.get('active_action') or status}"
                elif phase == "tts_regen_start":
                    attempt_label = f"第 {active_tts_attempt}/{max_tts_attempts} 次" if max_tts_attempts else f"第 {active_tts_attempt} 次"
                    message = f"正在重生成句 {asr_index} 音频 · {attempt_label}"
                elif phase == "rewrite_attempt":
                    message = f"句 {asr_index} · 译文和音频已测量 · {status}"
                elif phase == "sentence_done":
                    message = f"句 {asr_index} · 收敛处理完成 · {status}"
                else:
                    message = f"正在按句联合收敛文案与音频时长 · 句 {asr_index} · {status}"
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
            final_sentences = apply_compact_audio_schedule(final_sentences, max_gap=0.25)
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
            final_full_audio_path = _rebuild_tts_full_audio_from_segments(task_dir, final_tts_segments, variant="av")
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
            if alignment_summary.get("speech_shot_alignment_applied"):
                final_compose_summary.setdefault("notes", []).append(
                    "语音镜头对齐：优化 "
                    f"{alignment_summary.get('shot_anchor_aligned_boundary_count', 0)} 个断点，"
                    f"额外静音 {alignment_summary.get('shot_anchor_extra_silence_total', 0):.2f}s。"
                )
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
                    "audio_timeline_mode": "compact_asr_primary",
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
                audio_timeline_mode="compact_asr_primary",
                max_compact_gap=0.25,
            )
            task_state.set_preview_file(task_id, "tts_full_audio", final_full_audio_path)
            task_state.set_artifact(task_id, "tts", build_tts_artifact(final_tts_segments))
            _save_json(task_dir, "localized_translation.av.final.json", final_localized_translation)
            _save_json(task_dir, "tts_result.av.json", final_tts_segments)
            runner._set_step(task_id, "tts", "done", f"{target_language_name} 配音收敛完成")
        except Exception as exc:
            _fail_localize(task_id, runner, current_step, str(exc))
