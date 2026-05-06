"""5 轮 rewrite + 变速短路 strategy（multi/omni 风格）。

PR6 把 base PipelineRunner._step_tts 的非 av_sync 主体重构成
``runner._run_default_tts_loop(task_id, task_dir)``。本 strategy 是该
方法的 thin wrapper：调用方拿到 strategy 后只需 ``run(runner, profile,
task_id, task_dir)``，不必关心 5 轮循环、变速短路、bestpick 兜底等
内部机制。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TtsConvergenceStrategy

if TYPE_CHECKING:
    from appcore.runtime import PipelineRunner
    from appcore.translate_profiles.base import TranslateProfile


class FiveRoundRewriteLoopStrategy(TtsConvergenceStrategy):
    code = "five_round_rewrite"
    name = "5 轮 rewrite + 变速短路"

    def run(
        self,
        runner: "PipelineRunner",
        profile: "TranslateProfile",
        task_id: str,
        task_dir: str,
    ) -> None:
        runner._run_default_tts_loop(task_id, task_dir)
