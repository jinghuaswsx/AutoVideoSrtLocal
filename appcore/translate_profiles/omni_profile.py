"""Omni profile = OmniTranslateRunner behavior.

Source-anchored localization (multi-source-language) with same-language
ASR cleaning instead of normalize→en, plus per-target word_tolerance /
max_rewrite_attempts inside the duration loop.
PR1: every hook delegates back to OmniTranslateRunner's existing method.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TranslateProfile

if TYPE_CHECKING:
    from appcore.runtime import PipelineRunner


class OmniProfile(TranslateProfile):
    code = "omni"
    name = "全能（源语言锚定）"
    post_asr_step_name = "asr_clean"

    needs_separate = True
    needs_loudness_match = True

    def post_asr(self, runner: "PipelineRunner", task_id: str) -> None:
        runner._step_asr_clean(task_id)

    def translate(self, runner: "PipelineRunner", task_id: str) -> None:
        runner._step_translate(task_id)

    def tts(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        runner._step_tts(task_id, task_dir)

    def subtitle(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        runner._step_subtitle(task_id, task_dir)
