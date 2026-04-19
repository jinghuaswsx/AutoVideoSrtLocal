"""German translation pipeline runner.

Subclasses PipelineRunner, overriding translate/tts/subtitle steps
for German-specific prompts, TTS model, and subtitle rules.
"""
from __future__ import annotations

import json
import logging
import os
import uuid

log = logging.getLogger(__name__)

import appcore.task_state as task_state
from appcore.events import (
    EVT_ENGLISH_ASR_RESULT,
    EVT_SUBTITLE_READY,
    EVT_TRANSLATE_RESULT,
    EVT_TTS_SCRIPT_READY,
    EventBus,
)
from appcore.runtime import PipelineRunner, _build_review_segments, _save_json, _resolve_translate_provider
from web.preview_artifacts import (
    build_asr_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
    build_tts_artifact,
)


class DeTranslateRunner(PipelineRunner):
    """German-specific pipeline runner."""

    project_type: str = "de_translate"
    tts_language_code = "de"
    tts_model_id = "eleven_multilingual_v2"
    tts_default_voice_language = "de"
    localization_module = "pipeline.localization_de"
    target_language_label = "de"

    def _step_asr(self, task_id: str, task_dir: str) -> None:
        super()._step_asr(task_id, task_dir)
        # Auto-detect source language from ASR text
        task = task_state.get(task_id)
        if not task.get("source_language"):
            from pipeline.language_detect import detect_language
            asr_text = " ".join(
                u.get("text", "") for u in task.get("utterances", []) if u.get("text")
            )
            detected = detect_language(asr_text)
            task_state.update(task_id, source_language=detected)
            lang_label = "中文" if detected == "zh" else "英文"
            log.info("Auto-detected source language: %s (%s) for task %s", detected, lang_label, task_id)

    def _step_translate(self, task_id: str) -> None:
        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        source_language = task.get("source_language", "zh")
        lang_label = "中文" if source_language == "zh" else "英文"
        self._set_step(task_id, "translate", "running", f"正在将{lang_label}翻译为德语...")

        from pipeline.localization_de import (
            build_source_full_text_zh,
            LOCALIZED_TRANSLATION_SYSTEM_PROMPT as DE_PROMPT,
        )
        from pipeline.translate import (
            generate_localized_translation,
            get_model_display_name,
        )

        provider = _resolve_translate_provider(self.user_id)
        script_segments = task.get("script_segments", [])
        source_full_text = build_source_full_text_zh(script_segments)

        variant = "normal"
        custom_prompt = task.get("custom_translate_prompt")
        system_prompt = custom_prompt or DE_PROMPT

        localized_translation = generate_localized_translation(
            source_full_text, script_segments, variant=variant,
            custom_system_prompt=system_prompt,
            provider=provider, user_id=self.user_id,
        )

        initial_messages = localized_translation.pop("_messages", None)
        if initial_messages:
            _save_json(task_dir, "localized_translate_messages.json", {
                "phase": "initial_translate",
                "target_language": "de",
                "custom_system_prompt_used": bool(custom_prompt),
                "messages": initial_messages,
            })

        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        variant_state["localized_translation"] = localized_translation
        variants[variant] = variant_state
        _save_json(task_dir, "localized_translation.normal.json", localized_translation)

        review_segments = _build_review_segments(script_segments, localized_translation)
        requires_confirmation = bool(task.get("interactive_review"))
        task_state.update(
            task_id,
            source_full_text_zh=source_full_text,
            localized_translation=localized_translation,
            variants=variants,
            segments=review_segments,
            _segments_confirmed=not requires_confirmation,
        )
        task_state.set_artifact(task_id, "asr", build_asr_artifact(task.get("utterances", []), source_full_text, source_language=source_language))
        task_state.set_artifact(task_id, "translate", build_translate_artifact(source_full_text, localized_translation, source_language=source_language, target_language="de"))

        _save_json(task_dir, "source_full_text.json", {"full_text": source_full_text})
        _save_json(task_dir, "localized_translation.json", localized_translation)

        from appcore.usage_log import record as _log_usage
        _translate_usage = localized_translation.get("_usage") or {}
        _log_usage(self.user_id, task_id, provider,
                   model_name=get_model_display_name(provider, self.user_id),
                   success=True,
                   input_tokens=_translate_usage.get("input_tokens"),
                   output_tokens=_translate_usage.get("output_tokens"))

        if requires_confirmation:
            task_state.set_current_review_step(task_id, "translate")
            self._set_step(task_id, "translate", "waiting", "德语翻译结果已生成，等待人工确认")
        else:
            task_state.set_current_review_step(task_id, "")
            self._set_step(task_id, "translate", "done", "德语本土化翻译完成")

        self._emit(task_id, EVT_TRANSLATE_RESULT, {
            "source_full_text_zh": source_full_text,
            "localized_translation": localized_translation,
            "segments": review_segments,
            "requires_confirmation": requires_confirmation,
        })

    def _step_subtitle(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "subtitle", "running", "正在根据德语音频校正字幕...")
        from appcore.api_keys import resolve_key
        from pipeline.asr import transcribe_local_audio
        from pipeline.localization_de import WEAK_STARTERS_DE
        from pipeline.subtitle import build_srt_from_chunks, save_srt
        from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr

        volc_api_key = resolve_key(self.user_id, "volc", "VOLC_API_KEY")

        variant = "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        tts_audio_path = variant_state.get("tts_audio_path", "")

        de_utterances = transcribe_local_audio(
            tts_audio_path, prefix=f"tts-asr/{task_id}/normal", volc_api_key=volc_api_key
        )
        de_asr_result = {
            "full_text": " ".join(
                u.get("text", "").strip() for u in de_utterances if u.get("text")
            ).strip(),
            "utterances": de_utterances,
        }
        tts_script = variant_state.get("tts_script", {})
        from pipeline.tts import _get_audio_duration
        total_duration = _get_audio_duration(tts_audio_path) if tts_audio_path else 0.0
        corrected_chunks = align_subtitle_chunks_to_asr(
            tts_script.get("subtitle_chunks", []),
            de_asr_result,
            total_duration=total_duration,
        )
        srt_content = build_srt_from_chunks(corrected_chunks, weak_boundary_words=WEAK_STARTERS_DE)
        srt_path = save_srt(srt_content, os.path.join(task_dir, "subtitle.normal.srt"))

        variant_state.update({
            "english_asr_result": de_asr_result,
            "corrected_subtitle": {"chunks": corrected_chunks, "srt_content": srt_content},
            "srt_path": srt_path,
        })
        task_state.set_preview_file(task_id, "srt", srt_path)
        variants[variant] = variant_state

        task_state.update(
            task_id,
            variants=variants,
            english_asr_result=de_asr_result,
            corrected_subtitle={"chunks": corrected_chunks, "srt_content": srt_content},
            srt_path=srt_path,
        )
        task_state.set_artifact(task_id, "subtitle", build_subtitle_artifact(de_asr_result, corrected_chunks, srt_content, target_language="de"))

        _save_json(task_dir, "de_asr_result.normal.json", de_asr_result)
        _save_json(task_dir, "corrected_subtitle.normal.json", {"chunks": corrected_chunks, "srt_content": srt_content})

        self._emit(task_id, EVT_ENGLISH_ASR_RESULT, {"english_asr_result": de_asr_result})
        self._emit(task_id, EVT_SUBTITLE_READY, {"srt": srt_content})
        self._set_step(task_id, "subtitle", "done", "德语字幕生成完成")
