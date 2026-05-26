"""OmniTranslateRunner: full-language video translation pipeline.

Independent, opt-in module that adds:
- ASR engine dispatch by source language: zh/en→Doubao, others→ElevenLabs Scribe
- Source language is fully manual; ASR and downstream steps preserve the user's
  selected language and never auto-correct it.
- Per-target dynamic word_tolerance / max_rewrite_attempts for the duration
  convergence loop (loosen for de/ja/fi to avoid 5×5=25 burnouts) — these
  values live on ``OmniProfile`` (see appcore.translate_profiles.omni_profile)
  and are read by ``_run_tts_duration_loop`` via ``self.profile``.

This module **does not modify** the existing multi_translate / de_translate /
fr_translate / ja_translate code paths. It is the "treatment" version
ring-fenced into its own runner + routes + templates.
"""
from __future__ import annotations

import importlib
import logging
import uuid

from appcore import task_state
from appcore.llm_debug_payloads import prompt_file_payload
from appcore.llm_debug_runtime import save_llm_debug_calls
from appcore.runtime_multi import MultiTranslateRunner, _MANUAL_SOURCE_LANGUAGES

log = logging.getLogger(__name__)


import json as _json_anchor
from appcore.llm_prompt_configs import resolve_prompt_config as _resolve_prompt_anchor
from appcore.runtime_multi import _PromptLocalizationAdapter as _BaseAdapter
from appcore.runtime_multi import _JapaneseMultiTranslateAdapter as _JaAdapter


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


class OmniJapaneseLocalizationAdapter(OmniLocalizationAdapter):
    """Omni rewrite context plus Japanese character-budget TTS hooks."""

    rewrite_unit_label = _JaAdapter.rewrite_unit_label
    DEFAULT_TTS_UNITS_PER_SECOND = _JaAdapter.DEFAULT_TTS_UNITS_PER_SECOND
    rewrite_use_case_code = _JaAdapter.rewrite_use_case_code

    def __init__(self, source_language: str, original_asr_text: str):
        super().__init__(
            lang="ja",
            source_language=source_language,
            original_asr_text=original_asr_text,
        )
        self._ja_adapter = _JaAdapter()
        self.module = self._ja_adapter.module
        self.build_tts_segments = self._ja_adapter.build_tts_segments

    def count_tts_units(self, text: str) -> int:
        return self._ja_adapter.count_tts_units(text)

    def build_tts_script_from_localized(self, localized_translation: dict) -> dict:
        return self._ja_adapter.build_tts_script_from_localized(localized_translation)

    def generate_duration_rewrite(self, **kwargs) -> dict:
        return self._ja_adapter.generate_duration_rewrite(**kwargs)


class OmniModuleLocalizationAdapter(OmniLocalizationAdapter):
    """Omni source-anchored adapter backed by a language localization module."""

    def __init__(
        self,
        *,
        lang: str,
        source_language: str,
        original_asr_text: str,
        module_name: str,
    ):
        super().__init__(
            lang=lang,
            source_language=source_language,
            original_asr_text=original_asr_text,
        )
        self.module = importlib.import_module(module_name)
        self.__name__ = self.module.__name__
        self.validate_tts_script = getattr(
            self.module, "validate_tts_script", self.validate_tts_script,
        )
        self.build_tts_segments = getattr(
            self.module, "build_tts_segments", self.build_tts_segments,
        )

    def _use_module_message_builders(self) -> bool:
        return bool(getattr(self.module, "USE_MODULE_MESSAGE_BUILDERS", False))

    def build_tts_script_messages(self, localized_translation: dict) -> list[dict]:
        builder = getattr(self.module, "build_tts_script_messages", None)
        if self._use_module_message_builders() and callable(builder):
            return builder(localized_translation)
        return super().build_tts_script_messages(localized_translation)

    def build_localized_rewrite_messages(
        self,
        source_full_text: str,
        prev_localized_translation: dict,
        target_words: int,
        direction: str,
        source_language: str = "zh",
        feedback_notes: str | None = None,
    ) -> list[dict]:
        builder = getattr(self.module, "build_omni_localized_rewrite_messages", None)
        if self._use_module_message_builders() and callable(builder):
            return builder(
                source_full_text=source_full_text,
                prev_localized_translation=prev_localized_translation,
                target_words=target_words,
                direction=direction,
                source_language=self.source_language or source_language,
                original_asr_text=self.original_asr_text,
                feedback_notes=feedback_notes,
            )
        return super().build_localized_rewrite_messages(
            source_full_text,
            prev_localized_translation,
            target_words,
            direction,
            source_language=source_language,
            feedback_notes=feedback_notes,
        )


class OmniTranslateRunner(MultiTranslateRunner):
    """Multi-source-language video translation runner."""

    project_type: str = "omni_translate"
    profile_code: str = "omni"

    # ------------------------------------------------------------------
    # AV-sync helpers (av_sentence translate / sentence_units subtitle 路径用)
    # 从 SentenceTranslateRunner 复制（spec §6.2 物理隔离原则；调 AvSyncProfile
    # 时 runner 是 OmniTranslateRunner，缺这些 helper 会 AttributeError）。
    # ------------------------------------------------------------------

    def _resolve_av_inputs(self, task: dict) -> dict:
        # 注意：task_state 给每个 task 默认初始化 av_translate_inputs 含
        # ``target_language=None`` （AV_TRANSLATE_INPUTS_DEFAULT），所以这些
        # key 在 dict 里已存在但是 None。setdefault 看到 key 已存不会覆盖，
        # 会把 None 透传给下游 → 撞"task.target_lang is required"。
        # 用 active "or" 写入：None / 空串 都被当作未设。
        av_inputs = dict(task.get("av_translate_inputs") or {})
        target_language = self._resolve_target_lang(task)
        av_inputs["target_language"] = av_inputs.get("target_language") or target_language
        av_inputs["target_language_name"] = (
            av_inputs.get("target_language_name") or target_language
        )
        av_inputs["target_market"] = av_inputs.get("target_market") or "US"
        av_inputs["sync_granularity"] = av_inputs.get("sync_granularity") or "sentence"
        av_inputs["product_overrides"] = av_inputs.get("product_overrides") or {}
        return av_inputs

    def _target_language_name(self, av_inputs: dict) -> str:
        return str(
            av_inputs.get("target_language_name")
            or av_inputs.get("target_language")
            or "target language"
        ).strip()

    def _resolve_av_voice(self, task: dict) -> tuple[dict, str, str]:
        voice = self._resolve_voice(task, self._get_localization_module(task))
        tts_voice_id = str(
            voice.get("elevenlabs_voice_id") or voice.get("id") or ""
        ).strip()
        speech_rate_voice_id = str(voice.get("id") or tts_voice_id or "").strip()
        if not tts_voice_id:
            raise RuntimeError("未找到可用音色，无法继续生成配音")
        return voice, tts_voice_id, speech_rate_voice_id

    def _resolve_compose_variant_name(self, task: dict) -> str:
        """根据 plugin_config 解析 compose / export 用的 variant。

        av_sync_profile.tts / translate / subtitle 都把数据写到 variants["av"]，
        所以 omni 任务在句级链路下 compose / export 也得读 "av"。
        非句级链路（standard / shot_char_limit + five_round）写 "normal"。

        2026-05-07 fix：base ``_is_av_pipeline_task`` 看 task.type / pipeline_version
        判断，omni 任务两个都不满足 → 误读 "normal" → variant_state["tts_audio_path"]
        KeyError 卡 compose。
        """
        cfg = self._resolve_plugin_config_for_task_state(task)
        if (
            cfg.get("translate_algo") == "av_sentence"
            or cfg.get("tts_strategy") == "sentence_reconcile"
            or cfg.get("subtitle") == "sentence_units"
        ):
            return "av"
        return super()._resolve_compose_variant_name(task)

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
        audio_path = self._ensure_audio_path_for_asr(task_id, task_dir)
        task = task_state.get(task_id) or task
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

        # === passthrough handling (music videos with empty ASR) ===
        if passthrough["enabled"]:
            task_state.update(
                task_id,
                source_full_text_zh=source_full_text,
                media_passthrough_mode="original_video",
                media_passthrough_reason=passthrough["reason"],
                media_passthrough_source_chars=passthrough["source_chars"],
            )
            message = "未检测到有效语音，已按音乐视频直通处理"
            self._set_step(task_id, "asr", "done", message)
            self._emit(task_id, EVT_ASR_RESULT, {"segments": utterances})
            self._complete_original_video_passthrough(task_id, task["video_path"], task_dir)
            return

        # 这一轮 ASR 不再触发 passthrough（存在有效文本），清掉之前留下的
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
        source_language = (task.get("source_language") or "").strip()
        if not utterances:
            if not (task.get("artifacts") or {}).get("asr_clean"):
                task_state.set_artifact(
                    task_id,
                    "asr_clean",
                    self._build_asr_clean_artifact(
                        language=source_language,
                        input_utterances=[],
                        output_utterances=[],
                        cleaned=False,
                        skipped=True,
                        skip_reason="no_utterances",
                    ),
                )
            self._set_step(task_id, "asr_clean", "done", "无音频文本，跳过纯净化")
            return

        # Resume idempotency: skip if already cleaned
        if task.get("utterances_raw"):  # set only after successful purify
            if not (task.get("artifacts") or {}).get("asr_clean"):
                task_state.set_artifact(
                    task_id,
                    "asr_clean",
                    self._build_asr_clean_artifact(
                        language=source_language,
                        input_utterances=task.get("utterances_raw") or [],
                        output_utterances=utterances,
                        cleaned=True,
                        skipped=True,
                        skip_reason="already_cleaned",
                    ),
                )
            self._set_step(task_id, "asr_clean", "done", "已纯净化（resume 跳过）")
            return

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

        artifact = self._build_asr_clean_artifact(
            language=source_language,
            input_utterances=utterances,
            output_utterances=result["utterances"],
            cleaned=result["cleaned"],
            fallback_used=result["fallback_used"],
            model_used=result["model_used"],
            validation_errors=result["validation_errors"],
            user_specified=user_specified,
        )
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

    @staticmethod
    def _build_asr_clean_artifact(
        *,
        language: str,
        input_utterances: list,
        output_utterances: list,
        cleaned: bool,
        fallback_used: bool = False,
        model_used: str = "",
        validation_errors: list | None = None,
        user_specified: bool = True,
        skipped: bool = False,
        skip_reason: str = "",
    ) -> dict:
        return {
            "language": language,
            "user_specified": user_specified,
            "cleaned": cleaned,
            "fallback_used": fallback_used,
            "model_used": model_used,
            "validation_errors": validation_errors or [],
            "skipped": skipped,
            "skip_reason": skip_reason,
            "input_utterances": input_utterances,
            "utterances": output_utterances,
            "input_preview": " ".join(u.get("text", "") for u in input_utterances)[:200],
            "output_preview": " ".join(u.get("text", "") for u in output_utterances)[:200],
        }

    # ------------------------------------------------------------------
    # Phase 2: plugin_config-driven step builder + thin shims
    # ------------------------------------------------------------------

    def _resolve_plugin_config(self, task_id: str) -> dict:
        """读 task.plugin_config；缺失时回退全站默认 preset；再缺失回退 DEFAULT。"""
        from appcore.omni_plugin_config import (
            DEFAULT_PLUGIN_CONFIG, validate_plugin_config,
        )
        from appcore import omni_preset_dao

        task = task_state.get(task_id) or {}
        cfg = task.get("plugin_config")
        if cfg:
            try:
                return validate_plugin_config(cfg)
            except ValueError:
                log.warning(
                    "[omni] task=%s plugin_config invalid, falling back to default",
                    task_id, exc_info=True,
                )
        # 回退顺序：全站默认 preset → 硬编码 DEFAULT
        try:
            preset = omni_preset_dao.get_default()
            if preset and preset.get("plugin_config"):
                return validate_plugin_config(preset["plugin_config"])
        except Exception:  # noqa: BLE001 — DB 异常不阻塞，走硬编码兜底
            log.warning("[omni] resolve default preset failed", exc_info=True)
        return dict(DEFAULT_PLUGIN_CONFIG)

    def _resolve_plugin_config_for_task_state(self, task: dict | None) -> dict:
        """Resolve plugin_config from a loaded task dict, including default fallback."""
        from appcore.omni_plugin_config import (
            DEFAULT_PLUGIN_CONFIG, validate_plugin_config,
        )

        task = task or {}
        cfg = task.get("plugin_config")
        if cfg:
            try:
                return validate_plugin_config(cfg)
            except ValueError:
                log.warning(
                    "[omni] task=%s plugin_config invalid, falling back to default",
                    task.get("id") or "?",
                    exc_info=True,
                )
        task_id = str(task.get("id") or "").strip()
        if task_id:
            return self._resolve_plugin_config(task_id)
        return dict(DEFAULT_PLUGIN_CONFIG)

    @staticmethod
    def pipeline_step_names_for_config(
        plugin_config: dict,
        *,
        include_analysis: bool = False,
    ) -> list[str]:
        """Return the real Omni step order for a validated plugin config."""
        from appcore.omni_plugin_config import validate_plugin_config

        cfg = validate_plugin_config(plugin_config)
        names = ["extract", "asr"]
        if cfg["voice_separation"]:
            names.append("separate")
        names.append("asr_clean" if cfg["asr_post"] == "asr_clean" else "asr_normalize")
        names.extend(["voice_match", "alignment"])
        if cfg["shot_decompose"]:
            names.append("shot_decompose")
        names.extend(["translate", "tts"])
        if cfg["av_sync_audit"] != "off":
            names.append("av_sync_audit")
        if cfg["loudness_match"]:
            names.append("loudness_match")
        names.extend(["subtitle", "compose"])
        if include_analysis:
            names.append("analysis")
        names.append("export")
        return names

    def pipeline_step_names_for_task(
        self,
        task_id: str,
        *,
        include_analysis: bool | None = None,
    ) -> list[str]:
        """Resolve task config and return the dynamic Omni step order."""
        if include_analysis is None:
            include_analysis = self.include_analysis_in_main_flow
        cfg = self._resolve_plugin_config(task_id)
        return self.pipeline_step_names_for_config(
            cfg,
            include_analysis=include_analysis,
        )

    def _get_pipeline_steps(self, task_id: str, video_path: str, task_dir: str) -> list:
        """plugin_config-driven dynamic step builder（Phase 2）。

        不再走 PR2 的 ``_build_steps_from_profile``。step 顺序固定，但每步
        是否插入由 ``plugin_config`` 决定；step body 都通过 ``self.profile.X``
        / ``self._step_X`` 调用，profile 内部按 cfg 二次 dispatch 到具体算法。
        """
        step_fns = {
            "extract": lambda: self._step_extract(task_id, video_path, task_dir),
            "asr": lambda: self._step_asr(task_id, task_dir),
            "separate": lambda: self._step_separate(task_id, task_dir),
            "shot_decompose": lambda: self._step_shot_decompose(task_id, video_path, task_dir),
            "asr_clean": lambda: self.profile.post_asr(self, task_id),
            "asr_normalize": lambda: self.profile.post_asr(self, task_id),
            "voice_match": lambda: self._step_voice_match(task_id),
            # alignment 在所有 cfg 下都需要 —— av_sentence translate 也依赖
            # alignment 产出的 ``script_segments``（task 必备字段），不能跳过。
            "alignment": lambda: self._step_alignment(task_id, video_path, task_dir),
            "translate": lambda: self.profile.translate(self, task_id),
            "tts": lambda: self.profile.tts(self, task_id, task_dir),
            "av_sync_audit": lambda: self._step_av_sync_audit(task_id, video_path, task_dir),
            "loudness_match": lambda: self._step_loudness_match(task_id, task_dir),
            "subtitle": lambda: self.profile.subtitle(self, task_id, task_dir),
            "compose": lambda: self._step_compose(task_id, video_path, task_dir),
            "analysis": lambda: self._step_analysis(task_id),
            "export": lambda: self._step_export(task_id, video_path, task_dir),
        }
        return [
            (name, step_fns[name])
            for name in self.pipeline_step_names_for_task(task_id)
        ]

    # Thin shims dispatching to runtime_omni_steps (5 个物理复制的算法体).
    # 这些方法 spec §6.2 要求暴露在 OmniTranslateRunner 上，便于 resume / 测试
    # 直接 ``runner._step_translate_standard(task_id)``。OmniProfile 也调它们。
    def _step_asr_normalize(self, task_id: str) -> None:
        from appcore import runtime_omni_steps
        runtime_omni_steps.step_asr_normalize(self, task_id)

    def _step_shot_decompose(self, task_id: str, video_path: str, task_dir: str) -> None:
        from appcore import runtime_omni_steps
        runtime_omni_steps.step_shot_decompose(self, task_id, video_path, task_dir)

    def _step_translate_standard(self, task_id: str, *, source_anchored: bool = True) -> None:
        from appcore import runtime_omni_steps
        runtime_omni_steps.step_translate_standard(
            self, task_id, source_anchored=source_anchored,
        )

    def _step_translate_shot_limit(self, task_id: str) -> None:
        from appcore import runtime_omni_steps
        runtime_omni_steps.step_translate_shot_limit(self, task_id)

    def _step_subtitle_asr_realign(self, task_id: str, task_dir: str) -> None:
        from appcore import runtime_omni_steps
        runtime_omni_steps.step_subtitle_asr_realign(self, task_id, task_dir)

    def _step_av_sync_audit(self, task_id: str, video_path: str, task_dir: str) -> None:
        from pipeline import omni_av_sync_audit
        omni_av_sync_audit.run(self, task_id, video_path, task_dir)

    def _get_localization_module(self, task: dict):
        lang = self._resolve_target_lang(task)
        source_language = (task.get("source_language") or "").strip()
        if source_language not in _MANUAL_SOURCE_LANGUAGES:
            source_language = "unknown"
        utterances = task.get("utterances") or []
        original_asr_text = " ".join(
            (u.get("text") or "").strip() for u in utterances if u.get("text")
        ).strip()
        if lang == "ja":
            return OmniJapaneseLocalizationAdapter(
                source_language=source_language,
                original_asr_text=original_asr_text,
            )
        if lang in {"es", "it"}:
            return OmniModuleLocalizationAdapter(
                lang=lang,
                source_language=source_language,
                original_asr_text=original_asr_text,
                module_name=f"pipeline.localization_{lang}",
            )
        return OmniLocalizationAdapter(
            lang=lang,
            source_language=source_language,
            original_asr_text=original_asr_text,
        )
