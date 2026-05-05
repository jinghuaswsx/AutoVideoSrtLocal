"""Omni profile = OmniTranslateRunner behavior.

Source-anchored localization (multi-source-language) with same-language
ASR cleaning instead of normalize→en, plus per-target word_tolerance /
max_rewrite_attempts inside the duration loop.

PR4: ``post_asr`` (asr_clean) / ``translate`` 算法 body 直接住在 profile 里。
runner（``OmniTranslateRunner``）只剩 thin shim 把调用 dispatch 回 profile，
所以新 omni 风味（例如不同的 ASR 纯净化策略）只需要新写 profile，不必再
派生 runner 子类。``tts`` 仍走 ``runner._step_tts``（base PipelineRunner
的 5 轮 duration loop + speedup 短路 + per-target tunables via profile），
``subtitle`` 沿用 ``DefaultProfile.subtitle`` 行为。``_step_asr`` 留在
runner 上是 ASR 路由（按 source_language 派发到 Doubao/Scribe），不属于
profile hook。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import appcore.task_state as task_state
from appcore.events import EVT_TRANSLATE_RESULT
from appcore.preview_artifacts import build_asr_artifact, build_translate_artifact
from appcore.runtime import (
    _build_review_segments,
    _llm_request_payload,
    _llm_response_payload,
    _log_translate_billing,
    _resolve_translate_provider,
    _save_json,
)
from pipeline import asr_clean as _asr_clean
from pipeline.localization import build_source_full_text_zh
from pipeline.translate import generate_localized_translation, get_model_display_name

from .base import TranslateProfile

if TYPE_CHECKING:
    from appcore.runtime import PipelineRunner

log = logging.getLogger(__name__)


# 慢收敛目标语言（de/ja/fi）放宽 word_tolerance + 提高 max_rewrite_attempts，
# 让外层 5 轮循环不至于在边角语言上把 attempt 全部烧光。其他目标语言保持基线。
_OMNI_WORD_TOLERANCE_BY_TARGET: dict[str, float] = {
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

_OMNI_MAX_REWRITE_ATTEMPTS_BY_TARGET: dict[str, int] = {
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


class OmniProfile(TranslateProfile):
    code = "omni"
    name = "全能（源语言锚定）"
    post_asr_step_name = "asr_clean"

    needs_separate = True
    needs_loudness_match = True

    def post_asr(self, runner: "PipelineRunner", task_id: str) -> None:
        """Same-language ASR purification (replaces asr_normalize for omni).

        Purify utterances in the manually selected source language. It does
        NOT translate to English — downstream omni runs alignment / translate
        on source-language utterances directly.
        """
        # ``_MANUAL_SOURCE_LANGUAGES`` 住在 runtime_multi，那里顶层 import
        # PipelineRunner，profile module 顶层引会触发 import-time 循环。
        from appcore.runtime_multi import _MANUAL_SOURCE_LANGUAGES

        task = task_state.get(task_id)
        utterances = task.get("utterances") or []
        if not utterances:
            runner._set_step(task_id, "asr_clean", "done", "无音频文本，跳过纯净化")
            return

        # Resume idempotency: skip if already cleaned
        if task.get("utterances_raw"):  # set only after successful purify
            runner._set_step(task_id, "asr_clean", "done", "已纯净化（resume 跳过）")
            return

        source_language = (task.get("source_language") or "").strip()
        if source_language not in _MANUAL_SOURCE_LANGUAGES:
            message = (
                f"source_language={source_language!r} 不在支持范围 "
                f"({', '.join(_MANUAL_SOURCE_LANGUAGES)})；请手动选择源语言"
            )
            task_state.update(task_id, status="error", error=message)
            runner._set_step(task_id, "asr_clean", "failed", message)
            return
        task_state.update(task_id, source_language=source_language, user_specified_source_language=True)
        user_specified = True
        runner._set_step(task_id, "asr_clean", "running",
                       f"正在纯净化 {source_language.upper()} ASR 文本…")

        result = _asr_clean.purify_utterances(
            utterances, language=source_language,
            task_id=task_id, user_id=runner.user_id,
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
            runner._set_step(task_id, "asr_clean", "done", msg)
        else:
            log.warning("[asr_clean] task=%s purify failed: %s", task_id, result["validation_errors"])
            runner._set_step(
                task_id, "asr_clean", "done",
                "ASR 纯净化未通过校验，保留原文本继续",
            )

    def translate(self, runner: "PipelineRunner", task_id: str) -> None:
        """omni: translate directly from source-language transcript to target language.

        Differs from DefaultProfile.translate in two ways:
        1. source_full_text is built from the source-language utterances/script_segments
           (multi reads utterances_en which omni no longer produces).
        2. The base_translation system prompt is augmented with INPUT NOTICE explaining
           that input may be ASR-noisy, to suppress fabrication.
        """
        from appcore.runtime_multi import _MANUAL_SOURCE_LANGUAGES

        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        if runner._complete_original_video_passthrough(
            task_id, task.get("video_path") or "", task_dir,
        ):
            return
        lang = runner._resolve_target_lang(task)
        source_language = (task.get("source_language") or "").strip()
        if source_language not in _MANUAL_SOURCE_LANGUAGES:
            message = (
                f"source_language={source_language!r} 不在支持范围 "
                f"({', '.join(_MANUAL_SOURCE_LANGUAGES)})；请手动选择源语言"
            )
            task_state.update(task_id, status="error", error=message)
            runner._set_step(task_id, "translate", "failed", message)
            return

        provider = _resolve_translate_provider(runner.user_id)
        _model_tag = f"{provider} · {get_model_display_name(provider, runner.user_id)}"
        runner._set_step(task_id, "translate", "running",
                       f"正在从 {source_language.upper()} 直译为 {lang.upper()}...",
                       model_tag=_model_tag)

        script_segments = task.get("script_segments", []) or []
        # build_source_full_text_zh just joins script_segments[*].text — language-agnostic
        source_full_text = build_source_full_text_zh(script_segments)
        task_state.update(task_id, source_full_text_zh=source_full_text)
        _save_json(task_dir, "source_full_text.json",
                   {"full_text": source_full_text, "language": source_language})

        # Source-anchored system prompt: vanilla base_translation + INPUT NOTICE
        base_prompt = runner._build_system_prompt(lang)
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
            provider=provider, user_id=runner.user_id,
            use_case="video_translate.localize",
            project_id=task_id,
        )
        initial_messages = localized_translation.pop("_messages", None)
        if initial_messages:
            _save_json(task_dir, "localized_translate_messages.json", {
                "phase": "initial_translate",
                "source_language": source_language,
                "target_language": lang,
                "messages": initial_messages,
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
            user_id=runner.user_id, project_id=task_id,
            use_case_code="video_translate.localize",
            provider=provider,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            success=True,
            request_payload=_llm_request_payload(
                localized_translation, provider, "video_translate.localize",
                messages=initial_messages,
            ),
            response_payload=_llm_response_payload(localized_translation),
        )

        if requires_confirmation:
            task_state.set_current_review_step(task_id, "translate")
            runner._set_step(task_id, "translate", "waiting",
                           f"{lang.upper()} 翻译已生成，等待人工确认")
        else:
            task_state.set_current_review_step(task_id, "")
            runner._set_step(task_id, "translate", "done",
                           f"{source_language.upper()} → {lang.upper()} 直译完成")

        runner._emit(task_id, EVT_TRANSLATE_RESULT, {
            "source_full_text_zh": source_full_text,
            "localized_translation": localized_translation,
            "segments": review_segments,
            "requires_confirmation": requires_confirmation,
        })

    def tts(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        # omni 的 TTS 走 base PipelineRunner._step_tts。per-target tunables
        # 已经通过 word_tolerance_for / max_rewrite_attempts_for 接通。
        runner._step_tts(task_id, task_dir)

    def subtitle(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        # omni 沿用 multi 的 subtitle 行为（TTS 后 ASR + 字幕对齐）。
        runner._step_subtitle(task_id, task_dir)

    def word_tolerance_for(self, target_lang: str) -> float:
        return _OMNI_WORD_TOLERANCE_BY_TARGET.get(
            target_lang, self.DEFAULT_WORD_TOLERANCE
        )

    def max_rewrite_attempts_for(self, target_lang: str) -> int:
        return _OMNI_MAX_REWRITE_ATTEMPTS_BY_TARGET.get(
            target_lang, self.DEFAULT_MAX_REWRITE_ATTEMPTS
        )
