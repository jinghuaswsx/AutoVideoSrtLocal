"""Default profile = current MultiTranslateRunner behavior.

Zero-config baseline: 5-round duration loop with speedup short-circuit,
asr_normalize→en, voice/BGM separation, loudness match.

PR4: ``post_asr`` / ``translate`` / ``subtitle`` 算法 body 直接住在 profile
里。runner（``MultiTranslateRunner``）只剩 thin shim 把调用 dispatch 回
profile，所以新 multi 风味（例如不同的字幕对齐器）只需要新写 profile，
不必再派生 runner 子类。``tts`` 仍走 ``runner._step_tts``，它走 base
``PipelineRunner._step_tts`` + 5 轮 duration loop——TTS 的 per-target
tunables 已经在 PR3 中走 profile（``word_tolerance_for`` /
``max_rewrite_attempts_for``）。
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import appcore.task_state as task_state
from appcore.events import EVT_ENGLISH_ASR_RESULT, EVT_SUBTITLE_READY, EVT_TRANSLATE_RESULT
from appcore.preview_artifacts import (
    build_asr_artifact,
    build_asr_normalize_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
)
from appcore.runtime import (
    _build_review_segments,
    _llm_request_payload,
    _llm_response_payload,
    _log_translate_billing,
    _resolve_translate_provider,
    _save_json,
)
from pipeline import asr_normalize as pipeline_asr_normalize
from pipeline.localization import build_source_full_text_zh
from pipeline.subtitle import build_srt_from_chunks, save_srt
from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr
from pipeline.translate import generate_localized_translation, get_model_display_name
from pipeline.tts import _get_audio_duration

from .base import TranslateProfile

if TYPE_CHECKING:
    from appcore.runtime import PipelineRunner

log = logging.getLogger(__name__)


class DefaultProfile(TranslateProfile):
    code = "default"
    name = "多语言（标准）"
    post_asr_step_name = "asr_normalize"

    needs_separate = True
    needs_loudness_match = True

    def post_asr(self, runner: "PipelineRunner", task_id: str) -> None:
        # ``_MANUAL_SOURCE_LANGUAGES`` 住在 runtime_multi，runtime_multi 顶层
        # import PipelineRunner——此 import 在 module 顶层会构成 import-time
        # 循环（runtime_multi → runtime/__init__ → 反过来 import 之前 module
        # 还没初始化完毕）。lazy import 在调用时才解析，避开循环。
        from appcore.runtime_multi import _MANUAL_SOURCE_LANGUAGES

        task = task_state.get(task_id)
        utterances = task.get("utterances") or []

        if not utterances:
            runner._set_step(
                task_id, "asr_normalize", "done", "无音频文本，跳过标准化",
            )
            return

        # resume 幂等：artifact 或 utterances_en 已经在了
        if task.get("asr_normalize_artifact") or task.get("utterances_en"):
            runner._set_step(
                task_id, "asr_normalize", "done", "已标准化（resume 跳过）",
            )
            return

        src_lang = (task.get("source_language") or "").strip()

        if src_lang not in _MANUAL_SOURCE_LANGUAGES:
            err = (
                f"source_language={src_lang!r} 不在支持范围 "
                f"({', '.join(_MANUAL_SOURCE_LANGUAGES)})；请手动选择源语言"
            )
            runner._set_step(task_id, "asr_normalize", "failed", err)
            task_state.update(task_id, error=err, status="error")
            return

        runner._set_step(
            task_id, "asr_normalize", "running",
            f"按手动选择的源语言 {src_lang} 标准化…",
        )
        try:
            artifact = pipeline_asr_normalize.run_user_specified(
                task_id=task_id, user_id=runner.user_id,
                utterances=utterances, source_language=src_lang,
            )
        except Exception as exc:
            err = f"按手动选择源语言标准化失败：{exc}"
            runner._set_step(task_id, "asr_normalize", "failed", err)
            task_state.update(task_id, error=err, status="error")
            return

        # 拆 artifact：_utterances_en 单独写到 task["utterances_en"]，不进 artifact 落盘
        utterances_en = artifact.pop("_utterances_en", None)
        updates = {
            "source_language": src_lang,
            "user_specified_source_language": True,
            "detected_source_language": artifact["detected_source_language"],
            "asr_normalize_artifact": artifact,
        }
        if artifact["route"] not in ("en_skip", "zh_skip"):
            updates["utterances_en"] = utterances_en
        task_state.update(task_id, **updates)

        msg_map = {
            "en_skip": "原文为英文，跳过标准化",
            "zh_skip": "原文为中文，走中文路径",
            "es_specialized": "西班牙语 → 英文标准化完成",
            "generic_fallback":
                f"{artifact['detected_source_language']} → 英文标准化完成（通用）",
            "generic_fallback_low_confidence":
                f"{artifact['detected_source_language']} → 英文标准化完成（低置信兜底）",
            "generic_fallback_mixed": "混合语言 → 英文标准化完成（兜底）",
        }
        base_msg = msg_map.get(artifact["route"], "原文标准化完成")
        if artifact.get("detection_source") == "user_specified":
            base_msg = f"{base_msg}（用户指定）"
        runner._set_step(task_id, "asr_normalize", "done", base_msg)
        task_state.set_artifact(task_id, "asr_normalize", build_asr_normalize_artifact(
            artifact,
            source_utterances=utterances,
            en_utterances=utterances_en,
        ))

    def translate(self, runner: "PipelineRunner", task_id: str) -> None:
        from appcore.runtime_multi import (
            _MANUAL_SOURCE_LANGUAGES,
            _ensure_source_transcript_is_actionable,
        )

        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        if runner._complete_original_video_passthrough(
            task_id,
            task.get("video_path") or "",
            task_dir,
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
                       f"正在翻译为 {lang.upper()}...", model_tag=_model_tag)
        script_segments = task.get("script_segments", [])
        source_full_text = build_source_full_text_zh(script_segments)
        task_state.update(task_id, source_full_text_zh=source_full_text)
        _save_json(task_dir, "source_full_text.json", {"full_text": source_full_text})

        from pipeline.extract import get_video_duration

        video_duration = get_video_duration(task.get("video_path") or "")
        _ensure_source_transcript_is_actionable(
            source_full_text=source_full_text,
            video_duration=video_duration,
            target_lang=lang,
        )

        system_prompt = runner._build_system_prompt(lang)

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
                "target_language": lang,
                "custom_system_prompt_used": True,
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
            user_id=runner.user_id,
            project_id=task_id,
            use_case_code="video_translate.localize",
            provider=provider,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
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
            runner._set_step(task_id, "translate", "waiting",
                           f"{lang.upper()} 翻译已生成，等待人工确认")
        else:
            task_state.set_current_review_step(task_id, "")
            runner._set_step(task_id, "translate", "done",
                           f"{lang.upper()} 本土化翻译完成")

        runner._emit(task_id, EVT_TRANSLATE_RESULT, {
            "source_full_text_zh": source_full_text,
            "localized_translation": localized_translation,
            "segments": review_segments,
            "requires_confirmation": requires_confirmation,
        })

    def tts(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        # PR6：dispatch 到 ``self.tts_strategy_code`` 解析出来的策略，
        # 默认 ``FiveRoundRewriteLoopStrategy``（5 轮 rewrite + 变速短路）。
        self.get_tts_strategy().run(runner, self, task_id, task_dir)

    def subtitle(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        from appcore import asr_router

        task = task_state.get(task_id)
        if runner._complete_original_video_passthrough(
            task_id,
            task.get("video_path") or "",
            task_dir,
        ):
            return
        lang = runner._resolve_target_lang(task)
        rules = runner._get_lang_rules(lang)

        # 字幕用 ASR：在 TTS 合成的目标语言音频上跑一次，拿词级时间戳给字幕对齐。
        _sub_adapter, _ = asr_router.resolve_adapter("subtitle_asr", lang)
        _sub_model_tag = f"{_sub_adapter.display_name} · {_sub_adapter.model_id}"
        runner._set_step(
            task_id, "subtitle", "running",
            f"正在根据 {lang.upper()} 音频校正字幕...",
            model_tag=_sub_model_tag,
        )

        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get("normal", {}))
        tts_audio_path = variant_state.get("tts_audio_path", "")

        _sub_result = asr_router.transcribe(
            tts_audio_path, source_language=lang, stage="subtitle_asr",
        )
        utterances = _sub_result["utterances"]
        asr_result = {
            "full_text": " ".join(u.get("text", "").strip()
                                    for u in utterances if u.get("text")).strip(),
            "utterances": utterances,
        }
        tts_script = variant_state.get("tts_script", {})
        total_duration = _get_audio_duration(tts_audio_path) if tts_audio_path else 0.0
        corrected_chunks = align_subtitle_chunks_to_asr(
            tts_script.get("subtitle_chunks", []),
            asr_result,
            total_duration=total_duration,
        )

        srt_content = build_srt_from_chunks(
            corrected_chunks,
            weak_boundary_words=rules.WEAK_STARTERS,
        )
        srt_content = rules.post_process_srt(srt_content)

        srt_path = save_srt(srt_content, os.path.join(task_dir, "subtitle.normal.srt"))

        variant_state.update({
            "english_asr_result": asr_result,
            "corrected_subtitle": {"chunks": corrected_chunks,
                                     "srt_content": srt_content},
            "srt_path": srt_path,
        })
        task_state.set_preview_file(task_id, "srt", srt_path)
        variants["normal"] = variant_state

        task_state.update(
            task_id, variants=variants,
            english_asr_result=asr_result,
            corrected_subtitle={"chunks": corrected_chunks,
                                  "srt_content": srt_content},
            srt_path=srt_path,
        )
        task_state.set_artifact(task_id, "subtitle",
                                 build_subtitle_artifact(asr_result, corrected_chunks,
                                                          srt_content,
                                                          target_language=lang))

        _save_json(task_dir, f"{lang}_asr_result.normal.json", asr_result)
        _save_json(task_dir, "corrected_subtitle.normal.json",
                   {"chunks": corrected_chunks, "srt_content": srt_content})

        runner._emit(task_id, EVT_ENGLISH_ASR_RESULT, {"english_asr_result": asr_result})
        runner._emit(task_id, EVT_SUBTITLE_READY, {"srt": srt_content})
        runner._set_step(task_id, "subtitle", "done", f"{lang.upper()} 字幕生成完成")

        # Fire-and-forget translation-quality assessment. Failures don't block compose.
        try:
            from appcore import quality_assessment as _qa
            _qa.trigger_assessment(
                task_id=task_id, project_type=runner.project_type,
                triggered_by="auto", user_id=runner.user_id,
            )
        except Exception:  # noqa: BLE001 — assessment failures must not break pipeline
            log.warning("[%s] failed to trigger quality assessment for task %s",
                        runner.project_type, task_id, exc_info=True)
