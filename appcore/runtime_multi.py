"""多语种视频翻译 pipeline runner。

单一 Runner 处理 de/fr/es/it/ja/pt 所有目标语言：
- 翻译步骤走 llm_prompt_configs resolver
- 字幕/TTS 走 pipeline.languages.<lang> 规则
- 音色走现有 voice_match + elevenlabs_voices
"""
from __future__ import annotations

import logging
import os

import appcore.task_state as task_state
from appcore.events import EVT_TRANSLATE_RESULT
from appcore.llm_prompt_configs import resolve_prompt_config
from appcore.runtime import (
    PipelineRunner,
    _build_review_segments,
    _save_json,
    _resolve_translate_provider,
)
from appcore.usage_log import record as _log_usage
from pipeline.localization import build_source_full_text_zh
from pipeline.translate import generate_localized_translation, get_model_display_name
from web.preview_artifacts import (
    build_asr_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
    build_tts_artifact,
)

log = logging.getLogger(__name__)


class MultiTranslateRunner(PipelineRunner):
    project_type: str = "multi_translate"
    tts_model_id = "eleven_multilingual_v2"

    def _resolve_target_lang(self, task: dict) -> str:
        lang = task.get("target_lang")
        if not lang:
            raise ValueError("task.target_lang is required for multi_translate")
        return lang

    def _get_lang_rules(self, lang: str):
        from pipeline.languages.registry import get_rules
        return get_rules(lang)

    def _build_system_prompt(self, lang: str) -> str:
        base = resolve_prompt_config("base_translation", lang)
        plugin = resolve_prompt_config("ecommerce_plugin", None)
        return f"{base['content']}\n\n---\n\n{plugin['content']}"

    def _step_translate(self, task_id: str) -> None:
        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        lang = self._resolve_target_lang(task)
        source_language = task.get("source_language", "zh")
        lang_label = "中文" if source_language == "zh" else "英文"

        self._set_step(task_id, "translate", "running",
                       f"正在将{lang_label}翻译为 {lang.upper()}...")

        provider = _resolve_translate_provider(self.user_id)
        script_segments = task.get("script_segments", [])
        source_full_text = build_source_full_text_zh(script_segments)

        system_prompt = self._build_system_prompt(lang)

        localized_translation = generate_localized_translation(
            source_full_text, script_segments, variant="normal",
            custom_system_prompt=system_prompt,
            provider=provider, user_id=self.user_id,
        )

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
        _save_json(task_dir, "source_full_text.json", {"full_text": source_full_text})
        _save_json(task_dir, "localized_translation.json", localized_translation)

        usage = localized_translation.get("_usage") or {}
        _log_usage(self.user_id, task_id, provider,
                    model_name=get_model_display_name(provider, self.user_id),
                    success=True,
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"))

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
