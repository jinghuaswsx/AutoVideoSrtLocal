"""OmniTranslateRunner: full-language video translation pipeline.

Independent, opt-in module that adds:
- ASR engine dispatch by source language: zh/en→Doubao, others→ElevenLabs Scribe
- Source language is fully manual; ASR and downstream steps preserve the user's
  selected language and never auto-correct it.
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
from appcore.llm_debug_payloads import prompt_file_payload
from appcore.llm_debug_runtime import save_llm_debug_calls
from appcore.runtime_multi import MultiTranslateRunner, _MANUAL_SOURCE_LANGUAGES

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


import json as _json_anchor
from appcore.llm_prompt_configs import resolve_prompt_config as _resolve_prompt_anchor
from appcore.runtime_multi import _PromptLocalizationAdapter as _BaseAdapter


class OmniLocalizationAdapter(_BaseAdapter):
    """omni-flavored adapter: rewrite messages carry the original ASR transcript."""

    _SOURCE_LANG_LABEL: dict[str, str] = {
        "zh": "Chinese", "en": "English", "es": "Spanish", "pt": "Portuguese",
        "fr": "French", "it": "Italian", "ja": "Japanese", "de": "German",
        "nl": "Dutch", "sv": "Swedish", "fi": "Finnish",
    }

    def __init__(self, lang: str, source_language: str, original_asr_text: str):
        super().__init__(lang)
        self.source_language = source_language
        self.original_asr_text = original_asr_text
        self.__name__ = f"omni_translate.localization.{lang}"

    def build_localized_rewrite_messages(
        self,
        source_full_text: str,
        prev_localized_translation: dict,
        target_words: int,
        direction: str,
        source_language: str = "zh",
        feedback_notes: str | None = None,
    ) -> list[dict]:
        config = _resolve_prompt_anchor("base_rewrite", self.lang)
        prompt = config["content"].replace(
            "{target_words}", str(target_words)
        ).replace("{direction}", direction)

        src_label = self._SOURCE_LANG_LABEL.get(self.source_language, self.source_language)

        user_content = (
            f"ORIGINAL VIDEO TRANSCRIPT ({src_label}, ground truth — what the video actually says):\n"
            f"{self.original_asr_text}\n\n"
            f"INITIAL LOCALIZATION (target language, written from the transcript above):\n"
            f"{_json_anchor.dumps(prev_localized_translation, ensure_ascii=False, indent=2)}\n\n"
            f"REWRITE TASK:\n"
            f"Rewrite the initial localization to {direction} to ~{target_words} words. "
            f"STAY ANCHORED in the original transcript. Do NOT fabricate details that "
            f"are not in the transcript above."
        )
        if feedback_notes:
            user_content += f"\n\n{feedback_notes}"

        return [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ]


class OmniTranslateRunner(MultiTranslateRunner):
    """Multi-source-language video translation runner."""

    project_type: str = "omni_translate"
    profile_code: str = "omni"

    # Override the base ASR step to dispatch by source_language.
    def _step_asr(self, task_id: str, task_dir: str) -> None:
        from pipeline.extract import get_video_duration
        from pipeline.lang_labels import lang_label
        from appcore.preview_artifacts import build_asr_artifact
        from appcore.runtime import (
            _resolve_original_video_passthrough,
            _save_json,
            _seconds_to_request_units,
        )
        from appcore import ai_billing, asr_router
        from appcore.events import EVT_ASR_RESULT

        task = task_state.get(task_id)
        audio_path = task["audio_path"]
        source_language = (task.get("source_language") or "").strip()
        if source_language not in _MANUAL_SOURCE_LANGUAGES:
            message = (
                f"source_language={source_language!r} 不在支持范围 "
                f"({', '.join(_MANUAL_SOURCE_LANGUAGES)})；请手动选择源语言"
            )
            task_state.update(task_id, status="error", error=message)
            self._set_step(task_id, "asr", "failed", message)
            return
        task_state.update(task_id, source_language=source_language, user_specified_source_language=True)

        # 先解析 adapter 拿元数据生成 model_tag，让前端在 running 状态就能看到
        # 当前用的是哪个 ASR provider（豆包 / Scribe）。
        _adapter, _ = asr_router.resolve_adapter("asr_main", source_language)
        _asr_model_tag = f"{_adapter.display_name} · {_adapter.model_id}"
        self._set_step(
            task_id, "asr", "running",
            f"正在识别{lang_label(source_language, in_chinese=True)}语音...",
            model_tag=_asr_model_tag,
        )

        # === Unified ASR call via router ===
        # 路由器内部已做语言污染清理（fast-langdetect 删除非主语言段 + 时间合并）。
        result = asr_router.transcribe(
            audio_path, source_language=source_language, stage="asr_main",
        )
        utterances = result["utterances"]
        asr_provider = result["provider_code"]
        asr_model = result["model_id"]
        audio_url = ""

        passthrough = _resolve_original_video_passthrough(utterances)
        source_full_text = passthrough["source_full_text"]
        task_state.update(task_id, utterances=utterances, source_full_text=source_full_text)
        task_state.set_artifact(task_id, "asr", build_asr_artifact(utterances))
        _save_json(task_dir, "asr_result.json", {"utterances": utterances})

        if source_full_text:
            task_state.update(
                task_id,
                source_language=source_language,
                user_specified_source_language=True,
            )

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

        # 这一轮 ASR 不再触发 passthrough（utterances 够长），清掉之前留下的
        # passthrough flag。否则下游 voice_match / translate / tts / subtitle
        # 仍按"音乐视频直通"短路，整个翻译流程跑空。
        task_state.update(
            task_id,
            media_passthrough_mode=None,
            media_passthrough_reason=None,
            media_passthrough_source_chars=None,
        )

        if not utterances:
            self._set_step(task_id, "asr", "done", "未检测到语音内容，可能是纯音乐/音效视频")
            self._emit(task_id, EVT_ASR_RESULT, {"segments": []})
            raise RuntimeError("未检测到语音内容。该视频可能是纯音乐或音效背景视频，无法进行语音翻译。")

        self._set_step(task_id, "asr", "done", f"识别完成，共 {len(utterances)} 段")
        self._emit(task_id, EVT_ASR_RESULT, {"segments": utterances})

    def _step_asr_clean(self, task_id: str) -> None:
        """Same-language ASR purification (replaces asr_normalize for omni).

        Purify utterances in the manually selected source language. It does
        NOT translate to English — downstream omni runs alignment / translate
        on source-language utterances directly.
        """
        from pipeline import asr_clean as _asr_clean
        from appcore.runtime import _save_json

        task = task_state.get(task_id)
        utterances = task.get("utterances") or []
        if not utterances:
            self._set_step(task_id, "asr_clean", "done", "无音频文本，跳过纯净化")
            return

        # Resume idempotency: skip if already cleaned
        if task.get("utterances_raw"):  # set only after successful purify
            self._set_step(task_id, "asr_clean", "done", "已纯净化（resume 跳过）")
            return

        source_language = (task.get("source_language") or "").strip()
        if source_language not in _MANUAL_SOURCE_LANGUAGES:
            message = (
                f"source_language={source_language!r} 不在支持范围 "
                f"({', '.join(_MANUAL_SOURCE_LANGUAGES)})；请手动选择源语言"
            )
            task_state.update(task_id, status="error", error=message)
            self._set_step(task_id, "asr_clean", "failed", message)
            return
        task_state.update(task_id, source_language=source_language, user_specified_source_language=True)
        user_specified = True
        self._set_step(task_id, "asr_clean", "running",
                       f"正在纯净化 {source_language.upper()} ASR 文本…")

        result = _asr_clean.purify_utterances(
            utterances, language=source_language,
            task_id=task_id, user_id=self.user_id,
        )
        save_llm_debug_calls(
            task_id=task_id,
            task_dir=task.get("task_dir") or "",
            step="asr_clean",
            calls=result.get("_llm_debug_calls") or [],
            save_json=_save_json,
        )

        artifact = {
            "language": source_language,
            "user_specified": user_specified,
            "cleaned": result["cleaned"],
            "fallback_used": result["fallback_used"],
            "model_used": result["model_used"],
            "validation_errors": result["validation_errors"],
            "input_preview": " ".join(u.get("text", "") for u in utterances)[:200],
            "output_preview": " ".join(u.get("text", "") for u in result["utterances"])[:200],
        }
        task_state.set_artifact(task_id, "asr_clean", artifact)

        if result["cleaned"]:
            task_state.update(
                task_id,
                utterances=result["utterances"],
                utterances_raw=utterances,  # keep original for audit
            )
            msg = "ASR 同语言纯净化完成"
            if result["fallback_used"]:
                msg += "（兜底）"
            self._set_step(task_id, "asr_clean", "done", msg)
        else:
            log.warning("[asr_clean] task=%s purify failed: %s", task_id, result["validation_errors"])
            self._set_step(
                task_id, "asr_clean", "done",
                "ASR 纯净化未通过校验，保留原文本继续",
            )

    def _get_pipeline_steps(self, task_id: str, video_path: str, task_dir: str) -> list:
        """走统一 profile 驱动的 step 构造器。

        omni 走 ``OmniProfile``：``post_asr_step_name=asr_clean``，其余位置
        与 multi 一致。
        """
        return self._build_steps_from_profile(task_id, video_path, task_dir)

    def _step_translate(self, task_id: str) -> None:
        """omni: translate directly from source-language transcript to target language.

        Differs from MultiTranslateRunner._step_translate in two ways:
        1. source_full_text is built from the source-language utterances/script_segments
           (multi reads utterances_en which omni no longer produces).
        2. The base_translation system prompt is augmented with INPUT NOTICE explaining
           that input may be ASR-noisy, to suppress fabrication.
        """
        from appcore.events import EVT_TRANSLATE_RESULT
        from appcore.runtime import (
            _build_review_segments,
            _llm_request_payload,
            _llm_response_payload,
            _log_translate_billing,
            _save_json,
        )
        from appcore.runtime_multi import _TRANSLATE_USE_CASE, _resolve_translate_use_case_binding
        from pipeline.localization import build_source_full_text_zh
        from pipeline.translate import generate_localized_translation
        from appcore.preview_artifacts import build_asr_artifact, build_translate_artifact

        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        if self._complete_original_video_passthrough(
            task_id, task.get("video_path") or "", task_dir,
        ):
            return
        lang = self._resolve_target_lang(task)
        source_language = (task.get("source_language") or "").strip()
        if source_language not in _MANUAL_SOURCE_LANGUAGES:
            message = (
                f"source_language={source_language!r} 不在支持范围 "
                f"({', '.join(_MANUAL_SOURCE_LANGUAGES)})；请手动选择源语言"
            )
            task_state.update(task_id, status="error", error=message)
            self._set_step(task_id, "translate", "failed", message)
            return

        provider_code, model_id = _resolve_translate_use_case_binding(_TRANSLATE_USE_CASE)
        _model_tag = f"{provider_code} · {model_id}"
        self._set_step(task_id, "translate", "running",
                       f"正在从 {source_language.upper()} 直译为 {lang.upper()}...",
                       model_tag=_model_tag)

        script_segments = task.get("script_segments", []) or []
        # build_source_full_text_zh just joins script_segments[*].text — language-agnostic
        source_full_text = build_source_full_text_zh(script_segments)
        task_state.update(task_id, source_full_text_zh=source_full_text)
        _save_json(task_dir, "source_full_text.json",
                   {"full_text": source_full_text, "language": source_language})

        # Source-anchored system prompt: vanilla base_translation + INPUT NOTICE
        base_prompt = self._build_system_prompt(lang)
        notice = (
            f"\n\nINPUT NOTICE: The source script provided below is in "
            f"{source_language.upper()}. It came from automatic speech recognition "
            f"of the original video and may contain transcription artifacts. "
            f"Treat it as the source of truth for content; do NOT invent details "
            f"that are not implied by it. If a segment is unintelligible, keep "
            f"your version brief instead of fabricating context."
        )
        system_prompt = base_prompt + notice

        localized_translation = generate_localized_translation(
            source_full_text, script_segments, variant="normal",
            custom_system_prompt=system_prompt,
            user_id=self.user_id,
            use_case=_TRANSLATE_USE_CASE,
            project_id=task_id,
        )
        initial_messages = localized_translation.pop("_messages", None)
        request_payload = _llm_request_payload(
            localized_translation,
            provider_code,
            _TRANSLATE_USE_CASE,
            messages=initial_messages,
        )
        if request_payload:
            request_payload["model"] = model_id
        if initial_messages:
            _save_json(task_dir, "localized_translate_messages.json", prompt_file_payload(
                phase="initial_translate",
                label="初始翻译",
                use_case_code=_TRANSLATE_USE_CASE,
                provider=provider_code,
                model=model_id,
                messages=initial_messages,
                request_payload=request_payload,
                meta={
                    "source_language": source_language,
                    "target_language": lang,
                },
            ))
            task_state.add_llm_debug_ref(task_id, "translate", {
                "id": "translate.initial",
                "label": "初始翻译",
                "path": "localized_translate_messages.json",
                "use_case": _TRANSLATE_USE_CASE,
                "provider": provider_code,
                "model": model_id,
                "source_language": source_language,
                "target_language": lang,
            })

        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get("normal", {}))
        variant_state["localized_translation"] = localized_translation
        variants["normal"] = variant_state
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
        task_state.set_artifact(task_id, "asr",
                                 build_asr_artifact(task.get("utterances", []),
                                                    source_full_text,
                                                    source_language=source_language))
        task_state.set_artifact(task_id, "translate",
                                 build_translate_artifact(source_full_text,
                                                          localized_translation,
                                                          source_language=source_language,
                                                          target_language=lang))
        _save_json(task_dir, "localized_translation.json", localized_translation)

        usage = localized_translation.get("_usage") or {}
        _log_translate_billing(
            user_id=self.user_id, project_id=task_id,
            use_case_code=_TRANSLATE_USE_CASE,
            provider=_TRANSLATE_USE_CASE,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            success=True,
            request_payload=request_payload,
            response_payload=_llm_response_payload(localized_translation),
        )

        if requires_confirmation:
            task_state.set_current_review_step(task_id, "translate")
            self._set_step(task_id, "translate", "waiting",
                           f"{lang.upper()} 翻译已生成，等待人工确认")
        else:
            task_state.set_current_review_step(task_id, "")
            self._set_step(task_id, "translate", "done",
                           f"{source_language.upper()} → {lang.upper()} 直译完成")

        self._emit(task_id, EVT_TRANSLATE_RESULT, {
            "source_full_text_zh": source_full_text,
            "localized_translation": localized_translation,
            "segments": review_segments,
            "requires_confirmation": requires_confirmation,
        })

    def _get_localization_module(self, task: dict):
        lang = self._resolve_target_lang(task)
        source_language = (task.get("source_language") or "").strip()
        if source_language not in _MANUAL_SOURCE_LANGUAGES:
            source_language = "unknown"
        utterances = task.get("utterances") or []
        original_asr_text = " ".join(
            (u.get("text") or "").strip() for u in utterances if u.get("text")
        ).strip()
        return OmniLocalizationAdapter(
            lang=lang,
            source_language=source_language,
            original_asr_text=original_asr_text,
        )
