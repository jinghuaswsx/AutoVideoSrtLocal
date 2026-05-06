"""AV-sync profile = SentenceTranslateRunner behavior.

Sentence-level localization with shot_notes (visual analysis) and
duration_reconcile (joint sentence-level convergence) instead of the
5-round whole-text rewrite loop. Skips voice/BGM separation and loudness
match; inserts an alignment step before translate.

PR4: ``translate`` / ``tts`` / ``subtitle`` 算法 body 直接住在 profile 里，
runner 子类（``SentenceTranslateRunner``）只剩 thin shim 把调用 dispatch
回 profile，所以新 av_sync 风味（例如多人声）只需要新写 profile，不必再
派生 runner 子类。
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from appcore.runtime import _rebuild_tts_full_audio_from_segments
from appcore.tts_language_guard import (
    TtsLanguageValidationError,
    validate_tts_script_language_or_raise,
)

from .base import TranslateProfile

if TYPE_CHECKING:
    from appcore.runtime import PipelineRunner

log = logging.getLogger(__name__)


class AvSyncProfile(TranslateProfile):
    code = "av_sync"
    name = "音画同步（句级）"
    post_asr_step_name = "asr_normalize"

    needs_separate = False
    needs_loudness_match = False

    def post_asr(self, runner: "PipelineRunner", task_id: str) -> None:
        # SentenceTranslateRunner 继承 MultiTranslateRunner._step_asr_normalize；
        # 这里不做特殊处理，直接复用 multi 的 normalize→en 逻辑。
        runner._step_asr_normalize(task_id)

    def translate(self, runner: "PipelineRunner", task_id: str) -> None:
        import appcore.task_state as task_state
        from appcore.events import EVT_TRANSLATE_RESULT
        from appcore.runtime import (
            _build_av_localized_translation,
            _ensure_variant_state,
            _fail_localize,
            _join_source_full_text,
            _normalize_av_sentences,
            _save_json,
        )
        from appcore.preview_artifacts import build_translate_artifact

        task = task_state.get(task_id)
        if runner._complete_original_video_passthrough(
            task_id,
            task.get("video_path") or "",
            task.get("task_dir") or "",
        ):
            return

        current_step = "translate"
        try:
            from appcore.source_video import ensure_local_source_video
            from pipeline.av_source_normalize import normalize_source_segments
            from pipeline.av_translate import generate_av_localized_translation
            from pipeline.shot_notes import build_fallback_shot_notes, generate_shot_notes

            ensure_local_source_video(task_id)
            task = task_state.get(task_id) or {}
            task_dir = task.get("task_dir", "")
            video_path = task.get("video_path", "")
            raw_script_segments = list(task.get("script_segments") or [])
            if not raw_script_segments:
                raise RuntimeError("缺少对齐后的句子分段，无法生成音画同步译文")

            av_inputs = runner._resolve_av_inputs(task)
            target_language = av_inputs["target_language"]
            target_language_name = runner._target_language_name(av_inputs)

            _voice, _tts_voice_id, speech_rate_voice_id = runner._resolve_av_voice(task)
            source_full_text_raw = _join_source_full_text(raw_script_segments)

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
            script_segments = list(source_normalization.get("segments") or raw_script_segments)
            source_full_text = _join_source_full_text(script_segments)
            task_state.update(
                task_id,
                raw_script_segments=raw_script_segments,
                normalized_script_segments=script_segments,
                source_normalization=source_normalization,
                source_full_text_raw=source_full_text_raw,
                source_full_text_zh=source_full_text,
            )
            _save_json(task_dir, "source_normalization.av.json", source_normalization)

            runner._set_step(task_id, "translate", "running", "正在分析画面并生成时间轴笔记...")
            try:
                shot_notes = generate_shot_notes(
                    video_path=video_path,
                    script_segments=script_segments,
                    target_language=target_language_name,
                    target_market=av_inputs.get("target_market") or "",
                    user_id=runner.user_id,
                    project_id=task_id,
                    max_retries=0,
                )
            except Exception as exc:
                log.warning("av_sync shot notes unavailable for %s", task_id, exc_info=True)
                runner._set_step(
                    task_id,
                    "translate",
                    "running",
                    "画面分析暂不可用，已使用 ASR 时间轴兜底继续翻译...",
                )
                shot_notes = build_fallback_shot_notes(script_segments, reason=str(exc))
            _save_json(task_dir, "shot_notes.json", shot_notes)

            runner._set_step(task_id, "translate", "running", "正在生成首版句级本土化译文...")
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
            variants, variant_state = _ensure_variant_state(task, "av")
            variant_state.update(
                {
                    "source_normalization": source_normalization,
                    "shot_notes": shot_notes,
                    "sentences": av_sentences,
                    "localized_translation": localized_translation,
                }
            )
            variants["av"] = variant_state
            task_state.update(
                task_id,
                av_translate_inputs=av_inputs,
                target_lang=target_language,
                variants=variants,
                shot_notes=shot_notes,
                localized_translation=localized_translation,
                source_full_text_zh=source_full_text,
                segments=av_sentences,
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
            _save_json(task_dir, "localized_translation.av.json", localized_translation)
            runner._emit(
                task_id,
                EVT_TRANSLATE_RESULT,
                {
                    "source_full_text_zh": source_full_text,
                    "localized_translation": localized_translation,
                    "segments": av_sentences,
                },
            )
            runner._set_step(task_id, "translate", "done", f"{target_language_name} 首版句级本土化完成")
        except Exception as exc:
            _fail_localize(task_id, runner, current_step, str(exc))

    def tts(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        import appcore.task_state as task_state
        from appcore.runtime import (
            _build_av_debug_state,
            _build_av_localized_translation,
            _build_av_tts_segments,
            _ensure_variant_state,
            _fail_localize,
            _normalize_av_sentences,
            _save_json,
        )
        from appcore.preview_artifacts import build_tts_artifact

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

            tts_engine = self.get_tts_engine()
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
            on_progress = make_tts_progress_emitter(
                runner, task_id,
                lang_label=target_language_name,
                round_label="首轮",
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
                _save_json(
                    task_dir,
                    "tts_language_check.av.json",
                    exc.result or {"is_target_language": False, "reason": str(exc)},
                )
                raise
            _save_json(task_dir, "tts_language_check.av.json", av_language_check)

            runner._set_step(task_id, "tts", "running", "正在按句联合收敛文案与音频时长...")
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
            final_full_audio_path = _rebuild_tts_full_audio_from_segments(task_dir, final_tts_segments, variant="av")
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
                tts_duration_status="done",
                av_debug=av_debug,
            )
            task_state.set_preview_file(task_id, "tts_full_audio", final_full_audio_path)
            task_state.set_artifact(task_id, "tts", build_tts_artifact(final_tts_segments))
            _save_json(task_dir, "localized_translation.av.final.json", final_localized_translation)
            _save_json(task_dir, "tts_result.av.json", final_tts_segments)
            runner._set_step(task_id, "tts", "done", f"{target_language_name} 配音收敛完成")
        except Exception as exc:
            _fail_localize(task_id, runner, current_step, str(exc))

    def subtitle(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        import appcore.task_state as task_state
        from appcore.events import EVT_SUBTITLE_READY
        from appcore.runtime import _fail_localize, _save_json
        from appcore.preview_artifacts import build_subtitle_artifact

        task = task_state.get(task_id)
        if runner._complete_original_video_passthrough(
            task_id,
            task.get("video_path") or "",
            task_dir,
        ):
            return
        if (task.get("steps") or {}).get("subtitle") == "done":
            return

        current_step = "subtitle"
        try:
            from pipeline.av_subtitle_units import build_subtitle_units_from_sentences
            from pipeline.subtitle import build_srt_from_chunks, save_srt

            av_inputs = runner._resolve_av_inputs(task)
            target_language = av_inputs["target_language"]
            target_language_name = runner._target_language_name(av_inputs)
            variants = dict(task.get("variants") or {})
            variant_state = dict(variants.get("av") or {})
            sentences = [dict(item) for item in variant_state.get("sentences") or [] if isinstance(item, dict)]
            if not sentences:
                raise RuntimeError("缺少最终配音句子，无法生成自定义字幕")

            runner._set_step(task_id, "subtitle", "running", f"正在根据{target_language_name}句级音频生成字幕...")
            subtitle_units = build_subtitle_units_from_sentences(
                sentences,
                mode=str(av_inputs.get("sync_granularity") or "sentence"),
            )
            srt_content = build_srt_from_chunks(subtitle_units)
            srt_path = save_srt(srt_content, os.path.join(task_dir, "subtitle.av.srt"))

            variant_state.update(
                {
                    "srt_path": srt_path,
                    "subtitle_units": subtitle_units,
                    "corrected_subtitle": {"chunks": subtitle_units, "srt_content": srt_content},
                }
            )
            variants["av"] = variant_state
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
            _save_json(
                task_dir,
                "corrected_subtitle.av.json",
                {"chunks": subtitle_units, "srt_content": srt_content},
            )
            runner._emit(task_id, EVT_SUBTITLE_READY, {"srt": srt_content})
            runner._set_step(task_id, "subtitle", "done", f"{target_language_name} 字幕生成完成")
        except Exception as exc:
            _fail_localize(task_id, runner, current_step, str(exc))
