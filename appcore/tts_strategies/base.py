"""TtsConvergenceStrategy abstract base class.

A strategy encapsulates the *whole* TTS step body — how to take the
initial localized translation + voice + video duration, generate audio,
and converge audio length to the target window. Two concrete strategies
ship today:

- ``FiveRoundRewriteLoopStrategy`` — multi/omni 风格：5 轮 rewrite + 变速短路
  + bestpick 兜底。住在 base PipelineRunner._run_default_tts_loop。
- ``SentenceReconcileStrategy`` — av_sync 风格：句级 reconcile_duration，
  shot_notes-aware，per-sentence 速率调整。

新 strategy 只需新增一个文件 + 在某个 profile 上设 ``tts_strategy_code``。
不必动 base runner 也不必派生新 runner 子类。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from appcore.runtime import PipelineRunner
    from appcore.translate_profiles.base import TranslateProfile


class TtsConvergenceStrategy(ABC):
    """A pluggable TTS convergence workflow."""

    code: str
    name: str

    @abstractmethod
    def run(
        self,
        runner: "PipelineRunner",
        profile: "TranslateProfile",
        task_id: str,
        task_dir: str,
    ) -> None:
        """跑完 TTS 步骤：合成 → 收敛 → 写 task_state → set_step done。

        Strategy 实现可以自由选择 engine（``profile.get_tts_engine()``）、
        per-target tunables（``profile.word_tolerance_for(...)``）等 profile
        提供的钩子。
        """

    def __repr__(self) -> str:  # pragma: no cover — trivial
        return f"<TtsConvergenceStrategy {self.code} ({self.name})>"
