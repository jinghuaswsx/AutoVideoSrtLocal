"""ElevenLabs TTS engine — wraps ``pipeline.tts``.

PR5: 把 ``pipeline.tts`` 模块当前暴露的三个接口包成 ``TtsEngine`` ABC 的
方法。SDK 调用、并发池、限流重试、API key 解析仍住在 ``pipeline.tts``，
engine 只是抽象壳，让上游通过 ``profile.get_tts_engine()`` 解耦 provider。
"""
from __future__ import annotations

from typing import Callable, Optional

from .base import TtsEngine


class ElevenLabsEngine(TtsEngine):
    code = "elevenlabs"
    name = "ElevenLabs"
    supports_speed_param = True

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
        from pipeline.tts import generate_full_audio

        kwargs: dict = {}
        if variant is not None:
            kwargs["variant"] = variant
        if model_id is not None:
            kwargs["model_id"] = model_id
        if language_code is not None:
            kwargs["language_code"] = language_code
        if on_progress is not None:
            kwargs["on_progress"] = on_progress
        if on_segment_done is not None:
            kwargs["on_segment_done"] = on_segment_done
        return generate_full_audio(segments, voice_id, output_dir, **kwargs)

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
        from pipeline.tts import regenerate_full_audio_with_speed

        kwargs: dict = {"variant": variant, "speed": speed}
        if model_id is not None:
            kwargs["model_id"] = model_id
        if language_code is not None:
            kwargs["language_code"] = language_code
        if on_segment_done is not None:
            kwargs["on_segment_done"] = on_segment_done
        return regenerate_full_audio_with_speed(segments, voice_id, output_dir, **kwargs)

    def get_audio_duration(self, audio_path: str) -> float:
        from pipeline.tts import _get_audio_duration

        return _get_audio_duration(audio_path)
