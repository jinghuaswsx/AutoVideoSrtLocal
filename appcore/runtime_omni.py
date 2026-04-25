"""OmniTranslateRunner: full-language video translation pipeline.

Independent, opt-in module that adds:
- ASR engine dispatch by source language: zh/en→Doubao, others→ElevenLabs Scribe
- LLM-based language identification (LID) after ASR to auto-correct user's
  source-language guess (especially when filename mislabels the language)
- Per-target dynamic word_tolerance / max_rewrite_attempts for the duration
  convergence loop (loosen for de/ja/fi to avoid 5×5=25 burnouts)

This module **does not modify** the existing multi_translate / de_translate /
fr_translate / ja_translate code paths. It is the "treatment" version
ring-fenced into its own runner + routes + templates.
"""
from __future__ import annotations

import logging
import uuid

from appcore import task_state
from appcore.runtime_multi import MultiTranslateRunner

log = logging.getLogger(__name__)


# Per-target rewrite tolerance for the duration convergence inner loop.
# de/ja/fi are slower / longer-word target languages where the LLM struggles
# to compress to ±10%; widening the window keeps the outer 5-round loop from
# burning all attempts on edge cases.
_WORD_TOLERANCE_BY_TARGET = {
    "en": 0.10,
    "de": 0.15,
    "fr": 0.12,
    "es": 0.12,
    "it": 0.12,
    "pt": 0.12,
    "ja": 0.18,
    "nl": 0.12,
    "sv": 0.12,
    "fi": 0.15,
}

# Per-target max rewrite attempts inside one outer round.
_MAX_REWRITE_ATTEMPTS_BY_TARGET = {
    "en": 5,
    "de": 7,
    "fr": 5,
    "es": 5,
    "it": 5,
    "pt": 5,
    "ja": 7,
    "nl": 5,
    "sv": 5,
    "fi": 7,
}


class OmniTranslateRunner(MultiTranslateRunner):
    """Multi-source-language video translation runner."""

    project_type: str = "omni_translate"

    # Override the base ASR step to dispatch by source_language.
    def _step_asr(self, task_id: str, task_dir: str) -> None:
        from appcore.api_keys import resolve_key
        from pipeline.extract import get_video_duration
        from pipeline.lang_labels import lang_label
        from web.preview_artifacts import build_asr_artifact
        from appcore.runtime import (
            _resolve_original_video_passthrough,
            _save_json,
            _seconds_to_request_units,
        )
        from appcore import ai_billing
        from appcore.events import EVT_ASR_RESULT

        task = task_state.get(task_id)
        audio_path = task["audio_path"]
        source_language = task.get("source_language", "zh")
        self._set_step(task_id, "asr", "running", "正在准备 ASR 音频...")

        # === ASR engine dispatch ===
        if source_language in ("zh", "en"):
            from pipeline.asr import transcribe
            from pipeline.storage import delete_file, upload_file
            volc_api_key = resolve_key(self.user_id, "volc", "VOLC_API_KEY")
            tos_key = f"asr-audio/{task_id}_{uuid.uuid4().hex[:8]}.wav"
            audio_url = upload_file(audio_path, tos_key)
            self._set_step(
                task_id, "asr", "running",
                f"正在识别{lang_label(source_language, in_chinese=True)}语音（豆包 ASR）...",
            )
            try:
                utterances = transcribe(audio_url, volc_api_key=volc_api_key)
            finally:
                try:
                    delete_file(tos_key)
                except Exception:
                    pass
            asr_provider = "doubao_asr"
            asr_model = "big-model"
        else:
            from pipeline.asr_scribe import transcribe_local_audio as scribe_transcribe
            elevenlabs_api_key = resolve_key(
                self.user_id, "elevenlabs", "ELEVENLABS_API_KEY",
            )
            self._set_step(
                task_id, "asr", "running",
                f"正在识别{lang_label(source_language, in_chinese=True)}语音（ElevenLabs Scribe）...",
            )
            utterances = scribe_transcribe(
                audio_path,
                language_code=source_language,
                api_key=elevenlabs_api_key,
            )
            audio_url = ""
            asr_provider = "elevenlabs_scribe"
            asr_model = "scribe_v2"

        passthrough = _resolve_original_video_passthrough(utterances)
        source_full_text = passthrough["source_full_text"]
        task_state.update(task_id, utterances=utterances, source_full_text=source_full_text)
        task_state.set_artifact(task_id, "asr", build_asr_artifact(utterances))
        _save_json(task_dir, "asr_result.json", {"utterances": utterances})

        # === LLM-based LID auto-override ===
        # Even after engine dispatch, the user's selection may be wrong (e.g.
        # filename "西班牙语视频.mp4" is actually German). LLM looks at the
        # transcript and corrects task.source_language when it's confident.
        if source_full_text:
            try:
                from pipeline.language_detect_llm import detect_language_llm
                lid = detect_language_llm(
                    source_full_text,
                    fallback=source_language,
                    user_id=self.user_id,
                    project_id=task_id,
                )
                detected = lid["language"]
                conf = lid["confidence"]
                if (
                    lid["source"] == "llm"
                    and conf >= 0.7
                    and detected != source_language
                ):
                    log.info(
                        "[omni-lid-override] task=%s user_said=%s llm_says=%s (conf=%.2f) → overriding",
                        task_id, source_language, detected, conf,
                    )
                    task_state.update(task_id, source_language=detected)
                    source_language = detected
                else:
                    log.info(
                        "[omni-lid-keep] task=%s source_language=%s (llm=%s conf=%.2f source=%s)",
                        task_id, source_language, detected, conf, lid["source"],
                    )
            except Exception:
                log.warning("[omni-lid] LID failed, keeping user-supplied source_language", exc_info=True)

        # === audio duration + billing ===
        try:
            audio_duration_seconds = get_video_duration(audio_path)
        except Exception:
            audio_duration_seconds = max(
                (float(item.get("end_time") or 0.0) for item in utterances),
                default=0.0,
            )
        ai_billing.log_request(
            use_case_code="video_translate.asr",
            user_id=self.user_id,
            project_id=task_id,
            provider=asr_provider,
            model=asr_model,
            request_units=_seconds_to_request_units(audio_duration_seconds),
            units_type="seconds",
            audio_duration_seconds=audio_duration_seconds,
            success=True,
            request_payload={
                "type": "asr",
                "provider": asr_provider,
                "audio_url": audio_url,
                "audio_path": audio_path,
            },
            response_payload={
                "utterances": utterances,
                "source_full_text": source_full_text,
                "audio_duration_seconds": audio_duration_seconds,
            },
        )

        # === passthrough handling (music videos with empty/sparse ASR) ===
        if passthrough["enabled"]:
            task_state.update(
                task_id,
                source_full_text_zh=source_full_text,
                media_passthrough_mode="original_video",
                media_passthrough_reason=passthrough["reason"],
                media_passthrough_source_chars=passthrough["source_chars"],
            )
            if passthrough["reason"] == "no_asr":
                message = "未检测到有效语音，已按音乐视频直通处理"
            else:
                message = "识别结果少于 50 个字符，已按音乐视频直通处理"
            self._set_step(task_id, "asr", "done", message)
            self._emit(task_id, EVT_ASR_RESULT, {"segments": utterances})
            self._complete_original_video_passthrough(task_id, task["video_path"], task_dir)
            return

        if not utterances:
            self._set_step(task_id, "asr", "done", "未检测到语音内容，可能是纯音乐/音效视频")
            self._emit(task_id, EVT_ASR_RESULT, {"segments": []})
            raise RuntimeError("未检测到语音内容。该视频可能是纯音乐或音效背景视频，无法进行语音翻译。")

        self._set_step(task_id, "asr", "done", f"识别完成，共 {len(utterances)} 段")
        self._emit(task_id, EVT_ASR_RESULT, {"segments": utterances})
