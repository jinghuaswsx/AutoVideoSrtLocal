"""TtsEngine abstract base class.

A TTS engine encapsulates "how to turn segment text into audio files" and
"how to ask the engine to natively re-synthesize a segment at a different
speed". The duration-loop strategy and per-target tunables live above the
engine; the engine itself is a thin SDK wrapper.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Optional


class TtsEngine(ABC):
    """A pluggable TTS provider.

    Subclasses ship one provider's SDK calls (ElevenLabs / OpenAI TTS /
    Azure / volc / local model). Per-task wiring goes through
    ``profile.get_tts_engine()`` so swapping providers is a profile config
    change, not a runner subclass change.
    """

    code: str
    name: str

    # Engine 是否原生支持 voice_settings.speed 这种"同段重生成、改语速"的能力。
    # ElevenLabs 支持；早期 OpenAI TTS 不支持，需要走 atempo 后处理变速。
    # _run_tts_duration_loop 的 speedup 短路分支据此决定能否 attempt。
    supports_speed_param: bool = True

    @abstractmethod
    def synthesize_full(
        self,
        segments: list[dict],
        voice_id: str,
        output_dir: str,
        *,
        variant: str | None = None,
        model_id: str | None = None,
        language_code: str | None = None,
        on_progress: Optional[Callable[[dict], None]] = None,
        on_segment_done: Optional[Callable[[int, int, dict], None]] = None,
    ) -> dict:
        """合成全文（多段）音频并 concat 成完整音轨。

        Args:
            segments: ``[{tts_text, ...}, ...]``，每段返回 dict 上会补
                ``tts_path`` / ``tts_duration``。
            voice_id: provider-specific voice identifier。
            output_dir: 任务工作目录。Engine 在 ``<dir>/tts_segments[/<variant>]/``
                里写每段产物，并把 concat 完整音轨写到 ``<dir>/tts_full[.<variant>].mp3``。
            variant: 命名变体（如 ``"normal"``、``"av"``、``"round_2"``），用于
                同一 task 内多版本 TTS 不互相覆盖。
            model_id: provider 特定的 TTS 模型 id（如 ElevenLabs ``eleven_turbo_v2_5``）。
            language_code: BCP-47 语言码，部分 provider 用得到。
            on_progress: 实时进度回调（提交/开始/完成）。
            on_segment_done: 每段完成回调（旧接口，保持兼容）。

        Returns:
            ``{"full_audio_path": str, "segments": [...]}``
        """

    @abstractmethod
    def regenerate_with_speed(
        self,
        segments: list[dict],
        voice_id: str,
        output_dir: str,
        *,
        variant: str,
        speed: float,
        model_id: str | None = None,
        language_code: str | None = None,
        on_segment_done: Optional[Callable[[int, int, dict], None]] = None,
    ) -> dict:
        """以原生语速参数重新合成 segments。

        用于 duration-loop speedup 短路分支。Engine 不支持 native speed 时
        应抛 ``NotImplementedError``，调用方据此走 atempo 兜底或跳过。
        """

    @abstractmethod
    def get_audio_duration(self, audio_path: str) -> float:
        """返回音频时长（秒），通常走 ffprobe；engine 内部决定缓存策略。"""

    def __repr__(self) -> str:  # pragma: no cover — trivial
        return f"<TtsEngine {self.code} ({self.name})>"
