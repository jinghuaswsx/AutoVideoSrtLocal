"""TranslateProfile registry — pluggable per-pipeline behavior.

A profile encapsulates the parts of the video-translation pipeline that
diverge between the three legacy modules (multi / omni / av_sync):

- post_asr   : what to do after ASR (normalize→en, same-lang clean, or skip)
- translate  : how to localize (whole-text rewrite vs sentence-level + shot notes)
- tts        : how to converge audio duration (5-round rewrite loop vs reconcile_duration)
- subtitle   : how to produce SRT (ASR-realign vs sentence-units)

Capability flags decide which optional steps the runner inserts:
- needs_separate         : voice/BGM separation after ASR
- needs_alignment        : sentence-level alignment step (av_sync only)
- needs_loudness_match   : loudness match after TTS

PR1 scope: the abstraction + 3 profile instances that delegate every hook
back to the existing runner methods. Zero behavior change.
Subsequent PRs incrementally move logic from runners into profile bodies.
"""
from __future__ import annotations

from .base import TranslateProfile
from .default_profile import DefaultProfile
from .omni_profile import OmniProfile
from .av_sync_profile import AvSyncProfile

_REGISTRY: dict[str, TranslateProfile] = {}


def register_profile(profile: TranslateProfile) -> None:
    if profile.code in _REGISTRY:
        raise ValueError(f"profile already registered: {profile.code!r}")
    _REGISTRY[profile.code] = profile


def get_profile(code: str) -> TranslateProfile:
    try:
        return _REGISTRY[code]
    except KeyError as exc:
        raise KeyError(
            f"unknown translate profile: {code!r}. "
            f"available: {sorted(_REGISTRY)}"
        ) from exc


def available_profiles() -> list[TranslateProfile]:
    return list(_REGISTRY.values())


register_profile(DefaultProfile())
register_profile(OmniProfile())
register_profile(AvSyncProfile())


__all__ = [
    "TranslateProfile",
    "DefaultProfile",
    "OmniProfile",
    "AvSyncProfile",
    "register_profile",
    "get_profile",
    "available_profiles",
]
