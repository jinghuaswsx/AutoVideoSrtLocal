"""Omni profile = OmniTranslateRunner behavior.

Source-anchored localization (multi-source-language) with same-language
ASR cleaning instead of normalize→en, plus per-target word_tolerance /
max_rewrite_attempts inside the duration loop.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TranslateProfile

if TYPE_CHECKING:
    from appcore.runtime import PipelineRunner


# 慢收敛目标语言（de/ja/fi）放宽 word_tolerance + 提高 max_rewrite_attempts，
# 让外层 5 轮循环不至于在边角语言上把 attempt 全部烧光。其他目标语言保持基线。
_OMNI_WORD_TOLERANCE_BY_TARGET: dict[str, float] = {
    "en": 0.10,
    "de": 0.15,
    "fr": 0.12,
    "es": 0.12,
    "it": 0.12,
    "pt": 0.12,
    "ja": 0.18,
    "nl": 0.12,
    "sv": 0.12,
    "fi": 0.15,
}

_OMNI_MAX_REWRITE_ATTEMPTS_BY_TARGET: dict[str, int] = {
    "en": 5,
    "de": 7,
    "fr": 5,
    "es": 5,
    "it": 5,
    "pt": 5,
    "ja": 7,
    "nl": 5,
    "sv": 5,
    "fi": 7,
}


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

    def word_tolerance_for(self, target_lang: str) -> float:
        return _OMNI_WORD_TOLERANCE_BY_TARGET.get(
            target_lang, self.DEFAULT_WORD_TOLERANCE
        )

    def max_rewrite_attempts_for(self, target_lang: str) -> int:
        return _OMNI_MAX_REWRITE_ATTEMPTS_BY_TARGET.get(
            target_lang, self.DEFAULT_MAX_REWRITE_ATTEMPTS
        )
