"""多语种视频翻译 pipeline runner。

单一 Runner 处理 de/fr/es/it/ja/pt 所有目标语言：
- 翻译步骤走 llm_prompt_configs resolver
- 字幕/TTS 走 pipeline.languages.<lang> 规则
- 音色走现有 voice_match + elevenlabs_voices
"""
from __future__ import annotations

import logging

import appcore.task_state as task_state
from appcore.runtime import PipelineRunner

log = logging.getLogger(__name__)


class MultiTranslateRunner(PipelineRunner):
    project_type: str = "multi_translate"
    tts_model_id = "eleven_multilingual_v2"
    # target_language_label / tts_language_code / tts_default_voice_language
    # 都动态从 task.target_lang 推导，不作为 class attr 硬编码

    # 以下属性为运行时解析的便捷入口，不被基类逻辑依赖
    def _resolve_target_lang(self, task: dict) -> str:
        lang = task.get("target_lang")
        if not lang:
            raise ValueError("task.target_lang is required for multi_translate")
        return lang

    def _get_lang_rules(self, lang: str):
        """加载 pipeline.languages.<lang> 规则模块。"""
        from pipeline.languages.registry import get_rules
        return get_rules(lang)
