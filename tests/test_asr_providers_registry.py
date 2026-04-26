"""ASR provider REGISTRY + build_adapter 工厂的测试。"""
from __future__ import annotations

import pytest

from appcore.asr_providers import (
    REGISTRY,
    BaseASRAdapter,
    DoubaoAdapter,
    ScribeAdapter,
    build_adapter,
)


def test_registry_contains_two_providers() -> None:
    assert set(REGISTRY) == {"doubao_asr", "elevenlabs_tts"}
    assert REGISTRY["doubao_asr"] is DoubaoAdapter
    assert REGISTRY["elevenlabs_tts"] is ScribeAdapter


def test_build_adapter_returns_correct_subclass() -> None:
    a = build_adapter("doubao_asr")
    assert isinstance(a, DoubaoAdapter)
    assert isinstance(a, BaseASRAdapter)
    b = build_adapter("elevenlabs_tts")
    assert isinstance(b, ScribeAdapter)


def test_build_adapter_passes_model_id() -> None:
    a = build_adapter("doubao_asr", model_id="bigmodel-2")
    assert a.model_id == "bigmodel-2"
    b = build_adapter("elevenlabs_tts", model_id="scribe_v3")
    assert b.model_id == "scribe_v3"


def test_build_adapter_uses_default_model_id_when_none() -> None:
    assert build_adapter("doubao_asr").model_id == "bigmodel"
    assert build_adapter("elevenlabs_tts").model_id == "scribe_v2"


def test_build_adapter_raises_on_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown ASR provider_code"):
        build_adapter("nonexistent_provider")
