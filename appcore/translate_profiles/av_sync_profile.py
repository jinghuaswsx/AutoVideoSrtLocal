"""AV-sync profile = SentenceTranslateRunner behavior.

Sentence-level localization with shot_notes (visual analysis) and
duration_reconcile (joint sentence-level convergence) instead of the
5-round whole-text rewrite loop. Skips voice/BGM separation and
loudness match; inserts an alignment step before translate.
PR1: every hook delegates back to SentenceTranslateRunner's existing method.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TranslateProfile

if TYPE_CHECKING:
    from appcore.runtime import PipelineRunner


class AvSyncProfile(TranslateProfile):
    code = "av_sync"
    name = "音画同步（句级）"
    post_asr_step_name = "asr_normalize"

    needs_separate = False
    needs_loudness_match = False

    def post_asr(self, runner: "PipelineRunner", task_id: str) -> None:
        # SentenceTranslateRunner inherits MultiTranslateRunner._step_asr_normalize
        # but the base PipelineRunner short-circuits AV tasks via
        # _step_av_asr_normalize. The runtime currently routes through
        # SentenceTranslateRunner._get_pipeline_steps which calls
        # multi's _step_asr_normalize directly. Preserve that behavior.
        runner._step_asr_normalize(task_id)

    def translate(self, runner: "PipelineRunner", task_id: str) -> None:
        runner._step_translate(task_id)

    def tts(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        runner._step_tts(task_id, task_dir)

    def subtitle(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        runner._step_subtitle(task_id, task_dir)
