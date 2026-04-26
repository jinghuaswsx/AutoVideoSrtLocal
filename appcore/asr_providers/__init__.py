"""ASR provider adapters.

统一 ASR provider 抽象层。所有 adapter 都实现 BaseASRAdapter，输出对齐
为 Utterance 列表，pipeline 下游不需要知道哪个 provider 生成了 transcript。

Adapter 通过 build_adapter(provider_code, model_id=None) 工厂构造，
provider_code 与 appcore.llm_provider_configs 一致。
"""
from __future__ import annotations

from .base import (
    ASRCapabilities,
    BaseASRAdapter,
    Utterance,
    WordTimestamp,
)

__all__ = [
    "ASRCapabilities",
    "BaseASRAdapter",
    "REGISTRY",
    "Utterance",
    "WordTimestamp",
    "build_adapter",
]


REGISTRY: dict[str, type[BaseASRAdapter]] = {}


def build_adapter(provider_code: str, model_id: str | None = None) -> BaseASRAdapter:
    cls = REGISTRY.get(provider_code)
    if cls is None:
        raise ValueError(
            f"Unknown ASR provider_code: {provider_code!r}. "
            f"Known: {sorted(REGISTRY)}"
        )
    return cls(model_id=model_id)
