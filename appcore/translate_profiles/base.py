"""TranslateProfile abstract base class."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from appcore.runtime import PipelineRunner


class TranslateProfile(ABC):
    """A pluggable behavior bundle for the video translation pipeline."""

    code: str
    name: str

    # ASR 后处理步骤的对外名称（写入 task.steps、socket 事件、详情页用）。
    # default + av_sync 都叫 "asr_normalize"；omni 叫 "asr_clean"。
    post_asr_step_name: str = "asr_normalize"

    needs_separate: bool = True
    needs_loudness_match: bool = True

    # ===== Duration-loop tunables（profile 可逐目标语言覆盖） =====
    # rewrite 内循环里"字数落进 ±tolerance × target_words 即接受"的容差比例。
    DEFAULT_WORD_TOLERANCE: float = 0.20
    # 一轮外层 round 里 rewrite attempt 的上限。
    DEFAULT_MAX_REWRITE_ATTEMPTS: int = 5

    @abstractmethod
    def post_asr(self, runner: "PipelineRunner", task_id: str) -> None:
        """ASR 后的文本处理：normalize→en / 同语言 clean / 跳过。"""

    @abstractmethod
    def translate(self, runner: "PipelineRunner", task_id: str) -> None:
        """本土化翻译。"""

    @abstractmethod
    def tts(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        """语音生成 + 时长收敛。"""

    @abstractmethod
    def subtitle(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        """字幕生成。"""

    def word_tolerance_for(self, target_lang: str) -> float:
        """rewrite 字数收敛容差比例（相对于 target_words）。

        默认 0.20（multi/av_sync 行为）。OmniProfile 针对 de/ja/fi 等慢收敛
        目标语言放宽到 0.15~0.18，避免 5×5=25 次 attempt 全部用完仍不收敛。
        """
        return self.DEFAULT_WORD_TOLERANCE

    def max_rewrite_attempts_for(self, target_lang: str) -> int:
        """单轮外层 round 内 rewrite attempt 的上限。"""
        return self.DEFAULT_MAX_REWRITE_ATTEMPTS

    def __repr__(self) -> str:
        return f"<TranslateProfile {self.code} ({self.name})>"
