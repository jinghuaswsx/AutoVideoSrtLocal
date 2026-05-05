"""Sentence-level AV-sync translation runner.

This runner deliberately uses MultiTranslateRunner as the base workflow:
extract -> asr -> asr_normalize -> voice_match -> alignment -> translate
-> tts -> subtitle -> compose -> export.

PR4: 句级翻译 / TTS / 字幕的算法 body 已经搬进 ``AvSyncProfile``。这里
保留三个 ``_step_xxx`` 覆盖只是 thin shim，原因是 ``av_sync`` 老任务有
直接调 ``runner._step_translate(task_id)`` 的入口（resume / 测试）。
shim 把调用 dispatch 到 ``self.profile``，新 av_sync 风味只需要新写
profile，不必再派生 runner 子类。
"""
from __future__ import annotations

import logging

import appcore.task_state as task_state
from appcore.runtime_multi import MultiTranslateRunner

log = logging.getLogger(__name__)


class SentenceTranslateRunner(MultiTranslateRunner):
    """AV-sync V2 runner built on the multi-language translation workflow."""

    project_type = "sentence_translate"
    profile_code: str = "av_sync"

    def _resolve_target_lang(self, task: dict) -> str:
        av_inputs = task.get("av_translate_inputs") if isinstance(task.get("av_translate_inputs"), dict) else {}
        lang = task.get("target_lang") or av_inputs.get("target_language")
        if not lang:
            raise ValueError("task.target_lang or av_translate_inputs.target_language is required")
        return str(lang).strip().lower()

    def _resolve_av_inputs(self, task: dict) -> dict:
        av_inputs = dict(task.get("av_translate_inputs") or {})
        target_language = self._resolve_target_lang(task)
        av_inputs.setdefault("target_language", target_language)
        av_inputs.setdefault("target_language_name", target_language)
        av_inputs.setdefault("target_market", "US")
        av_inputs.setdefault("sync_granularity", "sentence")
        av_inputs.setdefault("product_overrides", {})
        return av_inputs

    def _target_language_name(self, av_inputs: dict) -> str:
        return str(
            av_inputs.get("target_language_name")
            or av_inputs.get("target_language")
            or "target language"
        ).strip()

    def _get_pipeline_steps(self, task_id: str, video_path: str, task_dir: str) -> list:
        """走统一 profile 驱动的 step 构造器。

        av_sync 走 ``AvSyncProfile``：``needs_separate=False`` +
        ``needs_loudness_match=False``，所以构造出来正好是历史的
        [extract, asr, asr_normalize, voice_match, alignment, translate, tts,
        subtitle, compose, (analysis), export]。
        """
        return self._build_steps_from_profile(task_id, video_path, task_dir)

    def _resolve_av_voice(self, task: dict) -> tuple[dict, str, str]:
        voice = self._resolve_voice(task, self._get_localization_module(task))
        tts_voice_id = str(voice.get("elevenlabs_voice_id") or voice.get("id") or "").strip()
        speech_rate_voice_id = str(voice.get("id") or tts_voice_id or "").strip()
        if not tts_voice_id:
            raise RuntimeError("未找到可用音色，无法继续生成配音")
        return voice, tts_voice_id, speech_rate_voice_id

    def _step_translate(self, task_id: str) -> None:
        self.profile.translate(self, task_id)

    def _step_tts(self, task_id: str, task_dir: str) -> None:
        self.profile.tts(self, task_id, task_dir)

    def _step_subtitle(self, task_id: str, task_dir: str) -> None:
        self.profile.subtitle(self, task_id, task_dir)
