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
from appcore.runtime import PipelineRunner, _build_review_segments, _save_json, _resolve_translate_provider
from web.preview_artifacts import (
    build_asr_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
    build_tts_artifact,
)


class FrTranslateRunner(PipelineRunner):
    """French-specific pipeline runner."""

    def _step_translate(self, task_id: str) -> None:
        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        source_language = task.get("source_language", "zh")
        lang_label = "中文" if source_language == "zh" else "英文"
        self._set_step(task_id, "translate", "running", f"正在将{lang_label}翻译为法语...")

        from pipeline.localization_fr import (
            build_source_full_text_zh,
            LOCALIZED_TRANSLATION_SYSTEM_PROMPT as FR_PROMPT,
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
        system_prompt = custom_prompt or FR_PROMPT

        localized_translation = generate_localized_translation(
            source_full_text, script_segments, variant=variant,
            custom_system_prompt=system_prompt,
            provider=provider, user_id=self.user_id,
        )

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
        task_state.set_artifact(task_id, "asr", build_asr_artifact(task.get("utterances", []), source_full_text))
        task_state.set_artifact(task_id, "translate", build_translate_artifact(source_full_text, localized_translation))

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

    def _step_tts(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "tts", "running", "正在生成法语配音...")
        from appcore.api_keys import resolve_key
        from pipeline.extract import get_video_duration
        from pipeline.localization_fr import (
            TTS_LANGUAGE_CODE,
            TTS_MODEL_ID,
            DEFAULT_MALE_VOICE_ID,
            DEFAULT_FEMALE_VOICE_ID,
            build_tts_script_messages as build_fr_tts_messages,
            build_tts_segments,
            validate_tts_script,
        )
        from pipeline.timeline import build_timeline_manifest
        from pipeline.translate import generate_tts_script, get_model_display_name
        from pipeline.tts import generate_full_audio, get_voice_by_id

        provider = _resolve_translate_provider(self.user_id)
        elevenlabs_api_key = resolve_key(self.user_id, "elevenlabs", "ELEVENLABS_API_KEY")

        voice = None
        if task.get("voice_id"):
            voice = get_voice_by_id(task["voice_id"], self.user_id)
        if not voice:
            gender = task.get("voice_gender", "male")
            fr_voice_id = DEFAULT_MALE_VOICE_ID if gender == "male" else DEFAULT_FEMALE_VOICE_ID
            voice = {
                "id": None,
                "elevenlabs_voice_id": fr_voice_id,
                "name": "Antoine" if gender == "male" else "Jeanne",
            }

        variant = "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        localized_translation = variant_state.get("localized_translation", {})
        video_duration = get_video_duration(task["video_path"])

        tts_script = generate_tts_script(
            localized_translation,
            provider=provider,
            user_id=self.user_id,
            messages_builder=build_fr_tts_messages,
            validator=validate_tts_script,
        )
        tts_segments = build_tts_segments(tts_script, task.get("script_segments", []))
        result = generate_full_audio(
            tts_segments,
            voice["elevenlabs_voice_id"],
            task_dir,
            variant=variant,
            elevenlabs_api_key=elevenlabs_api_key,
            model_id=TTS_MODEL_ID,
            language_code=TTS_LANGUAGE_CODE,
        )
        timeline_manifest = build_timeline_manifest(result["segments"], video_duration=video_duration)

        variant_state.update({
            "segments": result["segments"],
            "tts_script": tts_script,
            "tts_audio_path": result["full_audio_path"],
            "timeline_manifest": timeline_manifest,
            "voice_id": voice.get("id"),
        })
        variants[variant] = variant_state
        task_state.set_preview_file(task_id, "tts_full_audio", result["full_audio_path"])
        _save_json(task_dir, "tts_script.normal.json", tts_script)
        _save_json(task_dir, "tts_result.normal.json", result["segments"])
        _save_json(task_dir, "timeline_manifest.normal.json", timeline_manifest)

        task_state.update(
            task_id,
            variants=variants,
            segments=result["segments"],
            tts_script=tts_script,
            tts_audio_path=result["full_audio_path"],
            voice_id=voice.get("id"),
            timeline_manifest=timeline_manifest,
        )

        task_state.set_artifact(task_id, "tts", build_tts_artifact(tts_script, result["segments"]))
        self._emit(task_id, EVT_TTS_SCRIPT_READY, {"tts_script": tts_script})
        self._set_step(task_id, "tts", "done", "法语配音生成完成")
        from appcore.usage_log import record as _log_usage
        _tts_script_usage = tts_script.get("_usage") or {}
        _log_usage(self.user_id, task_id, provider,
                   model_name=get_model_display_name(provider, self.user_id),
                   success=True,
                   input_tokens=_tts_script_usage.get("input_tokens"),
                   output_tokens=_tts_script_usage.get("output_tokens"))
        _log_usage(self.user_id, task_id, "elevenlabs", success=True)

    def _step_subtitle(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "subtitle", "running", "正在根据法语音频校正字幕...")
        from appcore.api_keys import resolve_key
        from pipeline.asr import transcribe_local_audio
        from pipeline.localization_fr import WEAK_STARTERS_FR
        from pipeline.subtitle import build_srt_from_chunks, save_srt, apply_french_punctuation
        from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr

        volc_api_key = resolve_key(self.user_id, "volc", "VOLC_API_KEY")

        variant = "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        tts_audio_path = variant_state.get("tts_audio_path", "")

        fr_utterances = transcribe_local_audio(
            tts_audio_path, prefix=f"tts-asr/{task_id}/normal", volc_api_key=volc_api_key
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
        task_state.set_artifact(task_id, "subtitle", build_subtitle_artifact(fr_asr_result, corrected_chunks, srt_content))

        _save_json(task_dir, "fr_asr_result.normal.json", fr_asr_result)
        _save_json(task_dir, "corrected_subtitle.normal.json", {"chunks": corrected_chunks, "srt_content": srt_content})

        self._emit(task_id, EVT_ENGLISH_ASR_RESULT, {"english_asr_result": fr_asr_result})
        self._emit(task_id, EVT_SUBTITLE_READY, {"srt": srt_content})
        self._set_step(task_id, "subtitle", "done", "法语字幕生成完成")
