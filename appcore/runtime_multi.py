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
import re

import appcore.task_state as task_state
from appcore.llm_prompt_configs import resolve_prompt_config
from appcore.runtime import PipelineRunner
from appcore.video_translate_defaults import resolve_default_voice
from pipeline.localization import (
    build_tts_segments,
    count_words,
    validate_tts_script,
)

log = logging.getLogger(__name__)


_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_MANUAL_SOURCE_LANGUAGES = ("zh", "en", "es", "pt", "fr", "it", "ja", "de", "nl", "sv", "fi")


def _count_source_speech_units(text: str) -> int:
    """Count source speech density across spaced languages and CJK transcripts."""
    if not text:
        return 0
    cjk_chars = len(_CJK_CHAR_RE.findall(text))
    if cjk_chars:
        return cjk_chars + count_words(_CJK_CHAR_RE.sub(" ", text))
    return count_words(text)


def _ensure_source_transcript_is_actionable(
    *,
    source_full_text: str,
    video_duration: float,
    target_lang: str,
) -> None:
    """Fail fast when ASR is too sparse to support long-duration dubbing."""
    source_unit_count = _count_source_speech_units(source_full_text)
    if video_duration < 8.0:
        return
    min_words = max(5, int(math.floor(video_duration * 0.45)))
    if source_unit_count >= min_words:
        return
    raise RuntimeError(
        f"源视频语音过短（{video_duration:.1f}s 仅识别到 {source_unit_count} 字/词，"
        f"低于可靠翻译所需的 {min_words} 字/词），无法安全生成 {target_lang.upper()} 配音；"
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
    profile_code: str = "default"
    tts_model_id = "eleven_multilingual_v2"

    def _resolve_target_lang(self, task: dict) -> str:
        lang = task.get("target_lang")
        if not lang:
            raise ValueError("task.target_lang is required for multi_translate")
        return lang

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

    # PR4: translate / subtitle / asr_normalize 算法 body 已经搬进
    # ``DefaultProfile``。这里保留 thin shim 是因为大量历史代码（resume 入口、
    # tests）直接调 ``runner._step_xxx(task_id)``，shim 把调用 dispatch 回
    # ``self.profile``。新 multi 风味只要新写 profile，不必再派生 runner 子类。

    def _step_translate(self, task_id: str) -> None:
        self.profile.translate(self, task_id)

    def _step_subtitle(self, task_id: str, task_dir: str) -> None:
        self.profile.subtitle(self, task_id, task_dir)

    def _step_asr_normalize(self, task_id: str) -> None:
        self.profile.post_asr(self, task_id)

    def _step_voice_match(self, task_id: str) -> None:
        """PR7 thin shim — body 已搬到 ``DefaultProfile.voice_match``。"""
        self.profile.voice_match(self, task_id)

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
        """走统一 profile 驱动的 step 构造器。

        历史上这里手工插 separate / asr_normalize / voice_match / loudness_match；
        现在由 ``self.profile`` 决定哪些步骤要插，以及 post_asr 的步骤名（默认
        ``asr_normalize``）。multi 走 ``DefaultProfile``，最终步骤序列与历史完全一致。
        """
        return self._build_steps_from_profile(task_id, video_path, task_dir)
