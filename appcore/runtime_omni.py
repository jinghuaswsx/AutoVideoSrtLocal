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
        from pipeline.extract import get_video_duration
        from pipeline.lang_labels import lang_label
        from web.preview_artifacts import build_asr_artifact
        from appcore.runtime import (
            _resolve_original_video_passthrough,
            _save_json,
            _seconds_to_request_units,
        )
        from appcore import ai_billing, asr_router
        from appcore.events import EVT_ASR_RESULT

        task = task_state.get(task_id)
        audio_path = task["audio_path"]
        source_language = task.get("source_language", "zh")
        self._set_step(
            task_id, "asr", "running",
            f"正在识别{lang_label(source_language, in_chinese=True)}语音...",
        )

        # === Unified ASR call via router (zh→豆包，其他→Scribe v2 强制语言) ===
        # 路由器内部已做语言污染清理（fast-langdetect 删除非主语言段 + 时间合并）。
        result = asr_router.transcribe(audio_path, source_language=source_language)
        utterances = result["utterances"]
        asr_provider = result["provider_code"]
        asr_model = result["model_id"]
        audio_url = ""

        passthrough = _resolve_original_video_passthrough(utterances)
        source_full_text = passthrough["source_full_text"]
        task_state.update(task_id, utterances=utterances, source_full_text=source_full_text)
        task_state.set_artifact(task_id, "asr", build_asr_artifact(utterances))
        _save_json(task_dir, "asr_result.json", {"utterances": utterances})

        # === LLM-based LID auto-override ===
        # 用户没明确选源语言时，让 LLM 看 ASR 文本判定语言并自动改写
        # task.source_language。用户明确指定（user_specified_source_language=True）
        # 时彻底跳过这层 LID，不调 LLM、不覆盖。
        if source_full_text and not task.get("user_specified_source_language"):
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
        elif source_full_text:
            log.info(
                "[omni-lid-skip] task=%s source_language=%s (user_specified=True, skipping LID)",
                task_id, source_language,
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

        if not utterances:
            self._set_step(task_id, "asr", "done", "未检测到语音内容，可能是纯音乐/音效视频")
            self._emit(task_id, EVT_ASR_RESULT, {"segments": []})
            raise RuntimeError("未检测到语音内容。该视频可能是纯音乐或音效背景视频，无法进行语音翻译。")

        self._set_step(task_id, "asr", "done", f"识别完成，共 {len(utterances)} 段")
        self._emit(task_id, EVT_ASR_RESULT, {"segments": utterances})

    def _step_asr_clean(self, task_id: str) -> None:
        """Same-language ASR purification (replaces asr_normalize for omni).

        Detect if needed, then purify utterances in their own language. Does
        NOT translate to English — downstream omni runs alignment / translate
        on source-language utterances directly.
        """
        from pipeline import asr_clean as _asr_clean

        task = task_state.get(task_id)
        utterances = task.get("utterances") or []
        if not utterances:
            self._set_step(task_id, "asr_clean", "done", "无音频文本，跳过纯净化")
            return

        # Resume idempotency: skip if already cleaned
        if task.get("utterances_raw"):  # set only after successful purify
            self._set_step(task_id, "asr_clean", "done", "已纯净化（resume 跳过）")
            return

        source_language = task.get("source_language", "zh")
        user_specified = bool(task.get("user_specified_source_language"))
        self._set_step(task_id, "asr_clean", "running",
                       f"正在纯净化 {source_language.upper()} ASR 文本…")

        result = _asr_clean.purify_utterances(
            utterances, language=source_language,
            task_id=task_id, user_id=self.user_id,
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
        """Replace parent's asr_normalize step with asr_clean (omni-specific)."""
        from appcore.runtime import PipelineRunner
        base_steps = PipelineRunner._get_pipeline_steps(self, task_id, video_path, task_dir)
        out = []
        for name, fn in base_steps:
            out.append((name, fn))
            if name == "asr":
                out.append(("asr_clean", lambda: self._step_asr_clean(task_id)))
                out.append(("voice_match", lambda: self._step_voice_match(task_id)))
        return out
