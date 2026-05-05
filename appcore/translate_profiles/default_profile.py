"""Default profile = current MultiTranslateRunner behavior.

Zero-config baseline: 5-round duration loop with speedup short-circuit,
asr_normalize→en, voice/BGM separation, loudness match.
PR1: every hook delegates back to the runner's existing method.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TranslateProfile

if TYPE_CHECKING:
    from appcore.runtime import PipelineRunner


class DefaultProfile(TranslateProfile):
    code = "default"
    name = "多语言（标准）"
    post_asr_step_name = "asr_normalize"

    needs_separate = True
    needs_loudness_match = True

    def post_asr(self, runner: "PipelineRunner", task_id: str) -> None:
        runner._step_asr_normalize(task_id)

    def translate(self, runner: "PipelineRunner", task_id: str) -> None:
        runner._step_translate(task_id)

    def tts(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        runner._step_tts(task_id, task_dir)

    def subtitle(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        runner._step_subtitle(task_id, task_dir)
