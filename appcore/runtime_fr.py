"""French translation pipeline runner.

Subclasses PipelineRunner, overriding translate/tts/subtitle steps
for French-specific prompts, TTS model, and subtitle rules.
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
from appcore.runtime import (
    PipelineRunner,
    _build_review_segments,
    _compute_initial_target_words,
    _llm_request_payload,
    _llm_response_payload,
    _log_translate_billing,
    _save_json,
    _resolve_translate_provider,
)
from web.preview_artifacts import (
    build_asr_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
    build_tts_artifact,
)


class FrTranslateRunner(PipelineRunner):
    """French-specific pipeline runner."""

    project_type: str = "fr_translate"
    tts_language_code = "fr"
    tts_model_id = "eleven_multilingual_v2"
    tts_default_voice_language = "fr"
    localization_module = "pipeline.localization_fr"
    target_language_label = "fr"

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
        from pipeline.localization_fr import (
            build_source_full_text_zh,
            LOCALIZED_TRANSLATION_SYSTEM_PROMPT as FR_PROMPT,
        )
        from pipeline.translate import (
            generate_localized_translation,
            get_model_display_name,
        )

        provider = _resolve_translate_provider(self.user_id)
        _model_tag = f"{provider} · {get_model_display_name(provider, self.user_id)}"
        self._set_step(task_id, "translate", "running", f"正在将{lang_label}翻译为法语...", model_tag=_model_tag)
        script_segments = task.get("script_segments", [])
        source_full_text = build_source_full_text_zh(script_segments)

        variant = "normal"
        custom_prompt = task.get("custom_translate_prompt")
        system_prompt = custom_prompt or FR_PROMPT

        from pipeline.extract import get_video_duration
        video_duration = get_video_duration(task.get("video_path") or "") or 0.0
        target_words = _compute_initial_target_words(video_duration, self.target_language_label)
        localized_translation = generate_localized_translation(
            source_full_text, script_segments, variant=variant,
            custom_system_prompt=system_prompt,
            provider=provider, user_id=self.user_id,
            source_language=source_language,
            target_words=target_words or None,
            video_duration=video_duration or None,
        )

        initial_messages = localized_translation.pop("_messages", None)
        if initial_messages:
            _save_json(task_dir, "localized_translate_messages.json", {
                "phase": "initial_translate",
                "target_language": "fr",
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
        task_state.set_artifact(task_id, "translate", build_translate_artifact(source_full_text, localized_translation, source_language=source_language, target_language="fr"))

        _save_json(task_dir, "source_full_text.json", {"full_text": source_full_text})
        _save_json(task_dir, "localized_translation.json", localized_translation)

        _translate_usage = localized_translation.get("_usage") or {}
        _log_translate_billing(
            user_id=self.user_id,
            project_id=task_id,
            use_case_code="video_translate.localize",
            provider=provider,
            input_tokens=_translate_usage.get("input_tokens"),
            output_tokens=_translate_usage.get("output_tokens"),
            success=True,
            request_payload=_llm_request_payload(
                localized_translation,
                provider,
                "video_translate.localize",
                messages=initial_messages,
            ),
            response_payload=_llm_response_payload(localized_translation),
        )

        if requires_confirmation:
            task_state.set_current_review_step(task_id, "translate")
            self._set_step(task_id, "translate", "waiting", "法语翻译结果已生成，等待人工确认")
        else:
            task_state.set_current_review_step(task_id, "")
            self._set_step(task_id, "translate", "done", "法语本土化翻译完成")

        self._emit(task_id, EVT_TRANSLATE_RESULT, {
            "source_full_text_zh": source_full_text,
            "localized_translation": localized_translation,
            "segments": review_segments,
            "requires_confirmation": requires_confirmation,
        })

    def _step_subtitle(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "subtitle", "running", "正在根据法语音频校正字幕...")
        from appcore.api_keys import resolve_key
        from pipeline.asr import transcribe_local_audio_for_source
        from pipeline.localization_fr import WEAK_STARTERS_FR
        from pipeline.subtitle import build_srt_from_chunks, save_srt, apply_french_punctuation
        from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr

        volc_api_key = resolve_key(self.user_id, "volc", "VOLC_API_KEY")
        elevenlabs_api_key = resolve_key(
            self.user_id, "elevenlabs", "ELEVENLABS_API_KEY",
        )

        variant = "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        tts_audio_path = variant_state.get("tts_audio_path", "")

        # 法语 TTS 音频走 Scribe（豆包不支持 fr）；dispatcher 按 "fr" 自动选 Scribe。
        fr_utterances = transcribe_local_audio_for_source(
            tts_audio_path, "fr",
            prefix=f"tts-asr/{task_id}/normal",
            volc_api_key=volc_api_key,
            elevenlabs_api_key=elevenlabs_api_key,
        )
        fr_asr_result = {
            "full_text": " ".join(
                u.get("text", "").strip() for u in fr_utterances if u.get("text")
            ).strip(),
            "utterances": fr_utterances,
        }
        tts_script = variant_state.get("tts_script", {})
        from pipeline.tts import _get_audio_duration
        total_duration = _get_audio_duration(tts_audio_path) if tts_audio_path else 0.0
        corrected_chunks = align_subtitle_chunks_to_asr(
            tts_script.get("subtitle_chunks", []),
            fr_asr_result,
            total_duration=total_duration,
        )
        srt_content = build_srt_from_chunks(corrected_chunks, weak_boundary_words=WEAK_STARTERS_FR)
        srt_content = apply_french_punctuation(srt_content)
        srt_path = save_srt(srt_content, os.path.join(task_dir, "subtitle.normal.srt"))

        variant_state.update({
            "english_asr_result": fr_asr_result,
            "corrected_subtitle": {"chunks": corrected_chunks, "srt_content": srt_content},
            "srt_path": srt_path,
        })
        task_state.set_preview_file(task_id, "srt", srt_path)
        variants[variant] = variant_state

        task_state.update(
            task_id,
            variants=variants,
            english_asr_result=fr_asr_result,
            corrected_subtitle={"chunks": corrected_chunks, "srt_content": srt_content},
            srt_path=srt_path,
        )
        task_state.set_artifact(task_id, "subtitle", build_subtitle_artifact(fr_asr_result, corrected_chunks, srt_content, target_language="fr"))

        _save_json(task_dir, "fr_asr_result.normal.json", fr_asr_result)
        _save_json(task_dir, "corrected_subtitle.normal.json", {"chunks": corrected_chunks, "srt_content": srt_content})

        self._emit(task_id, EVT_ENGLISH_ASR_RESULT, {"english_asr_result": fr_asr_result})
        self._emit(task_id, EVT_SUBTITLE_READY, {"srt": srt_content})
        self._set_step(task_id, "subtitle", "done", "法语字幕生成完成")
