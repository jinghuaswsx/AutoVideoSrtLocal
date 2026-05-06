"""TtsEngine registry — pluggable per-provider TTS implementations.

Mirrors ``appcore.translate_profiles`` and ``appcore.asr_router``: profiles
declare ``tts_engine_code``; ``get_engine(code)`` resolves to a singleton
TtsEngine instance. Adding a new provider is a single-file change here
plus listing the code on a profile.

PR5: 注册 ``ElevenLabsEngine`` 作为默认；后续 PR 可加 ``OpenAITtsEngine``、
``AzureTtsEngine``、``VolcTtsEngine``、``LocalTtsEngine`` 等。
"""
from __future__ import annotations

from .base import TtsEngine
from .elevenlabs import ElevenLabsEngine

_REGISTRY: dict[str, TtsEngine] = {}


def register_engine(engine: TtsEngine) -> None:
    if engine.code in _REGISTRY:
        raise ValueError(f"tts engine already registered: {engine.code!r}")
    _REGISTRY[engine.code] = engine


def get_engine(code: str) -> TtsEngine:
    try:
        return _REGISTRY[code]
    except KeyError as exc:
        raise KeyError(
            f"unknown tts engine: {code!r}. "
            f"available: {sorted(_REGISTRY)}"
        ) from exc


def available_engines() -> list[TtsEngine]:
    return list(_REGISTRY.values())


register_engine(ElevenLabsEngine())


__all__ = [
    "TtsEngine",
    "ElevenLabsEngine",
    "register_engine",
    "get_engine",
    "available_engines",
]
