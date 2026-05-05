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

    def __repr__(self) -> str:
        return f"<TranslateProfile {self.code} ({self.name})>"
