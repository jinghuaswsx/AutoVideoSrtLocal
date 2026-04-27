"""多语种视频翻译 pipeline runner。

单一 Runner 处理 de/fr/es/it/ja/pt 所有目标语言：
- 翻译步骤走 llm_prompt_configs resolver
- 字幕/TTS 走 pipeline.languages.<lang> 规则
- 音色走现有 voice_match + elevenlabs_voices
"""
from __future__ import annotations

import json
import logging
import math
import os

import appcore.task_state as task_state
from appcore.api_keys import resolve_key
from appcore.events import EVT_ENGLISH_ASR_RESULT, EVT_SUBTITLE_READY, EVT_TRANSLATE_RESULT
from pipeline.asr import transcribe_local_audio
from pipeline.subtitle import build_srt_from_chunks, save_srt
from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr
from pipeline.tts import _get_audio_duration
from appcore.llm_prompt_configs import resolve_prompt_config
from appcore.runtime import (
    PipelineRunner,
    _build_review_segments,
    _llm_request_payload,
    _llm_response_payload,
    _log_translate_billing,
    _save_json,
    _resolve_translate_provider,
)
from appcore.video_translate_defaults import resolve_default_voice
from pipeline.voice_embedding import embed_audio_file
from pipeline.voice_match import extract_sample_from_utterances, match_candidates
from pipeline.localization import (
    build_source_full_text_zh,
    build_tts_segments,
    count_words,
    validate_tts_script,
)
from pipeline.translate import generate_localized_translation, get_model_display_name
from pipeline import asr_normalize as pipeline_asr_normalize
from pipeline.asr_normalize import UnsupportedSourceLanguageError as _AsnUnsupportedError
from web.preview_artifacts import (
    build_asr_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
    build_tts_artifact,
)

log = logging.getLogger(__name__)


def _ensure_source_transcript_is_actionable(
    *,
    source_full_text: str,
    video_duration: float,
    target_lang: str,
) -> None:
    """Fail fast when ASR is too sparse to support long-duration dubbing."""
    source_word_count = count_words(source_full_text)
    if video_duration < 8.0:
        return
    min_words = max(5, int(math.floor(video_duration * 0.45)))
    if source_word_count >= min_words:
        return
    raise RuntimeError(
        f"源视频语音过短（{video_duration:.1f}s 仅识别到 {source_word_count} 词，"
        f"低于可靠翻译所需的 {min_words} 词），无法安全生成 {target_lang.upper()} 配音；"
        "请检查源视频是否为可翻译口播素材，或更换原视频后重试。"
    )


class _PromptLocalizationAdapter:
    """Language-bound prompt adapter for the shared multi-translate TTS loop."""

    def __init__(self, lang: str):
        self.lang = lang
        self.__name__ = f"multi_translate.localization.{lang}"

    def build_tts_script_messages(self, localized_translation: dict) -> list[dict]:
        config = resolve_prompt_config("base_tts_script", self.lang)
        return [
            {"role": "system", "content": config["content"]},
            {
                "role": "user",
                "content": json.dumps(localized_translation, ensure_ascii=False, indent=2),
            },
        ]

    def build_localized_rewrite_messages(
        self,
        source_full_text: str,
        prev_localized_translation: dict,
        target_words: int,
        direction: str,
        source_language: str = "zh",
        feedback_notes: str | None = None,
    ) -> list[dict]:
        config = resolve_prompt_config("base_rewrite", self.lang)
        prompt = config["content"].replace(
            "{target_words}", str(target_words)
        ).replace("{direction}", direction)
        lang_label = {"zh": "Chinese", "en": "English"}.get(source_language, source_language)
        user_content = (
            f"Source {lang_label} full text (for reference, preserve meaning):\n"
            f"{source_full_text}\n\n"
            f"Previous localization (rewrite this to {direction} to ~{target_words} words):\n"
            f"{json.dumps(prev_localized_translation, ensure_ascii=False, indent=2)}"
        )
        if feedback_notes:
            user_content += f"\n\n{feedback_notes}"
        return [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ]

    validate_tts_script = staticmethod(validate_tts_script)
    build_tts_segments = staticmethod(build_tts_segments)


class MultiTranslateRunner(PipelineRunner):
    project_type: str = "multi_translate"
    tts_model_id = "eleven_multilingual_v2"

    def _resolve_target_lang(self, task: dict) -> str:
        lang = task.get("target_lang")
        if not lang:
            raise ValueError("task.target_lang is required for multi_translate")
        return lang

    # 注：不做源语言判别。LLM 自己读原文就能判断；zh/en label 对翻译质量无影响。
    # 任何后续流程都不 depend on source_language 的具体值。

    def _get_lang_rules(self, lang: str):
        from pipeline.languages.registry import get_rules
        return get_rules(lang)

    def _get_localization_module(self, task: dict):
        return _PromptLocalizationAdapter(self._resolve_target_lang(task))

    def _get_tts_target_language_label(self, task: dict) -> str:
        return self._resolve_target_lang(task)

    def _get_tts_model_id(self, task: dict) -> str:
        lang = self._resolve_target_lang(task)
        return getattr(self._get_lang_rules(lang), "TTS_MODEL_ID", self.tts_model_id)

    def _get_tts_language_code(self, task: dict) -> str | None:
        lang = self._resolve_target_lang(task)
        return getattr(self._get_lang_rules(lang), "TTS_LANGUAGE_CODE", lang)

    def _build_system_prompt(self, lang: str) -> str:
        base = resolve_prompt_config("base_translation", lang)
        plugin = resolve_prompt_config("ecommerce_plugin", None)
        return f"{base['content']}\n\n---\n\n{plugin['content']}"

    def _step_translate(self, task_id: str) -> None:
        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        if self._complete_original_video_passthrough(
            task_id,
            task.get("video_path") or "",
            task_dir,
        ):
            return
        lang = self._resolve_target_lang(task)
        source_language = task.get("source_language", "zh")

        provider = _resolve_translate_provider(self.user_id)
        _model_tag = f"{provider} · {get_model_display_name(provider, self.user_id)}"
        self._set_step(task_id, "translate", "running",
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

        system_prompt = self._build_system_prompt(lang)

        localized_translation = generate_localized_translation(
            source_full_text, script_segments, variant="normal",
            custom_system_prompt=system_prompt,
            provider=provider, user_id=self.user_id,
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
            user_id=self.user_id,
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
            self._set_step(task_id, "translate", "waiting",
                           f"{lang.upper()} 翻译已生成，等待人工确认")
        else:
            task_state.set_current_review_step(task_id, "")
            self._set_step(task_id, "translate", "done",
                           f"{lang.upper()} 本土化翻译完成")

        self._emit(task_id, EVT_TRANSLATE_RESULT, {
            "source_full_text_zh": source_full_text,
            "localized_translation": localized_translation,
            "segments": review_segments,
            "requires_confirmation": requires_confirmation,
        })

    def _step_subtitle(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        if self._complete_original_video_passthrough(
            task_id,
            task.get("video_path") or "",
            task_dir,
        ):
            return
        lang = self._resolve_target_lang(task)
        rules = self._get_lang_rules(lang)

        from appcore import asr_router

        # 字幕用 ASR：在 TTS 合成的目标语言音频上跑一次，拿词级时间戳给字幕对齐。
        _sub_adapter, _ = asr_router.resolve_adapter("subtitle_asr", lang)
        _sub_model_tag = f"{_sub_adapter.display_name} · {_sub_adapter.model_id}"
        self._set_step(
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

        self._emit(task_id, EVT_ENGLISH_ASR_RESULT, {"english_asr_result": asr_result})
        self._emit(task_id, EVT_SUBTITLE_READY, {"srt": srt_content})
        self._set_step(task_id, "subtitle", "done", f"{lang.upper()} 字幕生成完成")

        # Fire-and-forget translation-quality assessment. Failures don't block compose.
        try:
            from web.services import quality_assessment as _qa
            _qa.trigger_assessment(
                task_id=task_id, project_type=self.project_type,
                triggered_by="auto", user_id=self.user_id,
            )
        except Exception:  # noqa: BLE001 — assessment failures must not break pipeline
            log.warning("[%s] failed to trigger quality assessment for task %s",
                        self.project_type, task_id, exc_info=True)

    def _step_asr_normalize(self, task_id: str) -> None:
        """ASR 后的原文 → en-US 标准化。

        分两条路径：
        - user_specified=True：跳过 detect_language，按用户选定语言直接路由
          （zh/en/es/pt → run_user_specified），artifact.detection_source="user_specified"
        - user_specified=False：当前完整逻辑，先 detect 再路由

        其它短路：
        - 空 utterances → done
        - asr_normalize_artifact / utterances_en 已存在 → done（resume 幂等）
        - 自动检测路径下 source_language ∈ {en, zh} → done 不写 artifact（LLM 没跑）
        """
        task = task_state.get(task_id)
        utterances = task.get("utterances") or []

        if not utterances:
            self._set_step(
                task_id, "asr_normalize", "done", "无音频文本，跳过标准化",
            )
            return

        # resume 幂等：artifact 或 utterances_en 已经在了
        if task.get("asr_normalize_artifact") or task.get("utterances_en"):
            self._set_step(
                task_id, "asr_normalize", "done", "已标准化（resume 跳过）",
            )
            return

        user_specified = bool(task.get("user_specified_source_language"))
        src_lang = (task.get("source_language") or "").strip()

        if user_specified:
            if src_lang not in ("zh", "en", "es", "pt"):
                err = (
                    f"user_specified=True 但 source_language={src_lang!r} "
                    f"不在支持范围（zh/en/es/pt）"
                )
                self._set_step(task_id, "asr_normalize", "failed", err)
                task_state.update(task_id, error=err, status="error")
                return
            self._set_step(
                task_id, "asr_normalize", "running",
                f"用户指定 {src_lang}，跳过 LLM 语言检测…",
            )
            try:
                artifact = pipeline_asr_normalize.run_user_specified(
                    task_id=task_id, user_id=self.user_id,
                    utterances=utterances, source_language=src_lang,
                )
            except Exception as exc:
                err = f"按用户指定语言标准化失败：{exc}"
                self._set_step(task_id, "asr_normalize", "failed", err)
                task_state.update(task_id, error=err, status="error")
                return
        else:
            # 自动检测路径下，LID 已把 zh/en 写回 source_language 时跳过 detect
            if src_lang in ("en", "zh"):
                self._set_step(
                    task_id, "asr_normalize", "done",
                    "原文为英文/中文，跳过标准化",
                )
                return
            self._set_step(
                task_id, "asr_normalize", "running", "正在识别原文语言…",
            )
            try:
                artifact = pipeline_asr_normalize.run_asr_normalize(
                    task_id=task_id, user_id=self.user_id, utterances=utterances,
                )
            except _AsnUnsupportedError as exc:
                self._set_step(task_id, "asr_normalize", "failed", str(exc))
                task_state.update(task_id, error=str(exc), status="error")
                return
            except Exception as exc:
                err = f"原文标准化失败：{exc}"
                self._set_step(task_id, "asr_normalize", "failed", err)
                task_state.update(task_id, error=err, status="error")
                return

        # 拆 artifact：_utterances_en 单独写到 task["utterances_en"]，不进 artifact 落盘
        utterances_en = artifact.pop("_utterances_en", None)
        updates = {
            "detected_source_language": artifact["detected_source_language"],
            "asr_normalize_artifact": artifact,
        }
        if artifact["route"] == "en_skip":
            updates["source_language"] = "en"
        elif artifact["route"] == "zh_skip":
            updates["source_language"] = "zh"
        else:
            updates["source_language"] = "en"
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
        self._set_step(task_id, "asr_normalize", "done", base_msg)
        task_state.set_artifact(task_id, "asr_normalize", artifact)

    def _step_voice_match(self, task_id: str) -> None:
        """跑向量匹配写候选到 state，然后暂停 pipeline 等待用户在 UI 上选择音色。"""
        from appcore.events import EVT_VOICE_MATCH_READY

        task = task_state.get(task_id)
        if self._skip_original_video_passthrough_step(task_id, "voice_match", task=task):
            return
        lang = self._resolve_target_lang(task)
        utterances = task.get("utterances") or []
        video_path = task.get("video_path")
        default_voice_id = resolve_default_voice(lang, user_id=self.user_id)

        self._set_step(task_id, "voice_match", "running", f"{lang.upper()} 音色库加载中...")

        candidates: list = []
        if utterances and video_path:
            try:
                clip = extract_sample_from_utterances(
                    video_path, utterances, out_dir=task["task_dir"],
                    min_duration=8.0,
                )
                vec = embed_audio_file(clip)
                candidates = match_candidates(
                    vec,
                    language=lang,
                    top_k=10,
                    exclude_voice_ids={default_voice_id} if default_voice_id else None,
                ) or []
                for c in candidates:
                    c["similarity"] = float(c.get("similarity", 0.0))
                # 持久化 query embedding 到 state，以便前端切 gender 时
                # 后端可以不重新 embed、直接对 gender 子集重排 top-10。
                import base64 as _b64
                from pipeline.voice_embedding import serialize_embedding
                query_embedding_b64 = _b64.b64encode(serialize_embedding(vec)).decode("ascii")
            except Exception as exc:
                log.exception("voice match failed for %s: %s", task_id, exc)
                candidates = []
                query_embedding_b64 = None
        else:
            query_embedding_b64 = None

        fallback = None if candidates else default_voice_id

        task_state.update(
            task_id,
            voice_match_candidates=candidates,
            voice_match_fallback_voice_id=fallback,
            voice_match_query_embedding=query_embedding_b64,
        )

        # 暂停 pipeline，等待 /api/multi-translate/<task_id>/confirm-voice
        task_state.set_current_review_step(task_id, "voice_match")
        msg = f"{lang.upper()} 音色库已就绪，请选择 TTS 音色"
        self._set_step(task_id, "voice_match", "waiting", msg)
        self._emit(task_id, EVT_VOICE_MATCH_READY, {
            "candidates": candidates,
            "fallback_voice_id": fallback,
            "target_lang": lang,
        })

    def _resolve_voice(self, task, loc_mod):
        """多语种：优先用户确认的 selected_voice_id → fallback。"""
        voice_id = task.get("selected_voice_id")
        if voice_id:
            return {
                "id": None,
                "elevenlabs_voice_id": voice_id,
                "name": task.get("selected_voice_name") or voice_id,
            }
        lang = self._resolve_target_lang(task)
        fallback = resolve_default_voice(lang, user_id=self.user_id)
        if fallback:
            return {"id": None, "elevenlabs_voice_id": fallback, "name": "Default"}
        return super()._resolve_voice(task, loc_mod)

    def _get_pipeline_steps(self, task_id: str, video_path: str, task_dir: str) -> list:
        """覆盖基类：在 asr 后插入 asr_normalize → voice_match。"""
        base_steps = super()._get_pipeline_steps(task_id, video_path, task_dir)
        out = []
        for name, fn in base_steps:
            out.append((name, fn))
            if name == "asr":
                out.append(("asr_normalize", lambda: self._step_asr_normalize(task_id)))
                out.append(("voice_match", lambda: self._step_voice_match(task_id)))
        return out
