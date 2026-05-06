"""TtsConvergenceStrategy registry — pluggable TTS workflows.

Mirrors ``appcore.translate_profiles`` and ``appcore.tts_engines``: profiles
declare ``tts_strategy_code``; ``get_strategy(code)`` resolves to a
singleton strategy instance. Adding a new convergence flavor (e.g.
multi-pass shot-aware rewrite, real-time streaming) is a single-file
change here plus listing the code on a profile.

PR6: 注册当前两个策略；PR7+ 可加 ``MultiSpeakerStrategy``、
``StreamingStrategy``、``HybridSentenceLoopStrategy`` 等。
"""
from __future__ import annotations

from .base import TtsConvergenceStrategy
from .five_round_rewrite import FiveRoundRewriteLoopStrategy
from .sentence_reconcile import SentenceReconcileStrategy

_REGISTRY: dict[str, TtsConvergenceStrategy] = {}


def register_strategy(strategy: TtsConvergenceStrategy) -> None:
    if strategy.code in _REGISTRY:
        raise ValueError(f"tts strategy already registered: {strategy.code!r}")
    _REGISTRY[strategy.code] = strategy


def get_strategy(code: str) -> TtsConvergenceStrategy:
    try:
        return _REGISTRY[code]
    except KeyError as exc:
        raise KeyError(
            f"unknown tts strategy: {code!r}. "
            f"available: {sorted(_REGISTRY)}"
        ) from exc


def available_strategies() -> list[TtsConvergenceStrategy]:
    return list(_REGISTRY.values())


register_strategy(FiveRoundRewriteLoopStrategy())
register_strategy(SentenceReconcileStrategy())


__all__ = [
    "TtsConvergenceStrategy",
    "FiveRoundRewriteLoopStrategy",
    "SentenceReconcileStrategy",
    "register_strategy",
    "get_strategy",
    "available_strategies",
]
