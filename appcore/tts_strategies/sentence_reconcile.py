"""句级 reconcile_duration strategy（av_sync 风格）。

PR6: 把 ``AvSyncProfile.tts`` 的 body 搬到 strategy。新 av_sync 变种
（多人声、shot-aware 重写策略等）只需新写一个 strategy 子类即可，不必
派生 profile 或 runner。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import appcore.task_state as task_state
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

from .base import TtsConvergenceStrategy

if TYPE_CHECKING:
    from appcore.runtime import PipelineRunner
    from appcore.translate_profiles.base import TranslateProfile


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
