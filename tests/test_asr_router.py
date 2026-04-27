"""ASR Router 单元测试。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from appcore import asr_router, asr_routing_config
from appcore.asr_providers import BaseASRAdapter, DoubaoAdapter, ScribeAdapter


# -------------------- resolve_adapter --------------------
# 当前默认 stage 路由：asr_main / subtitle_asr 都走 doubao_asr。
# 豆包不支持 force_language，所以无论传什么 source_language，force 都为 None。

def test_resolve_asr_main_zh_routes_to_doubao():
    adapter, force = asr_router.resolve_adapter("asr_main", "zh")
    assert isinstance(adapter, DoubaoAdapter)
    assert force is None


def test_resolve_asr_main_zh_with_locale_suffix_routes_to_doubao():
    adapter, force = asr_router.resolve_adapter("asr_main", "zh-Hans")
    assert isinstance(adapter, DoubaoAdapter)
    assert force is None


def test_resolve_asr_main_es_auto_falls_back_to_scribe():
    # 默认 stage 是豆包，但豆包不支持 es → 自动切到 Scribe + force_language=es
    adapter, force = asr_router.resolve_adapter("asr_main", "es")
    assert isinstance(adapter, ScribeAdapter)
    assert force == "es"


def test_resolve_subtitle_asr_de_auto_falls_back_to_scribe():
    adapter, force = asr_router.resolve_adapter("subtitle_asr", "de")
    assert isinstance(adapter, ScribeAdapter)
    assert force == "de"


def test_resolve_unknown_stage_falls_back_to_doubao_default_then_scribe():
    # 未知 stage → asr_routing_config 兜底 doubao_asr → 豆包不支持 es → Scribe
    adapter, force = asr_router.resolve_adapter("not_a_stage", "es")
    assert isinstance(adapter, ScribeAdapter)
    assert force == "es"


def test_resolve_auto_source_language_no_force():
    adapter, force = asr_router.resolve_adapter("asr_main", "auto")
    assert isinstance(adapter, DoubaoAdapter)
    assert force is None


def test_resolve_empty_source_language_no_force():
    adapter, force = asr_router.resolve_adapter("asr_main", None)
    assert force is None
    adapter, force = asr_router.resolve_adapter("asr_main", "")
    assert force is None


def test_resolve_scribe_when_stage_overridden_to_elevenlabs(monkeypatch):
    # admin 在 /settings 把某 stage 切到 Scribe 时，路由读 system_settings 该 stage
    # 应实例化 ScribeAdapter，且 source_language 透传给 force_language
    # asr_router 用 from-import 拿到 get_stage_provider 的本地引用，
    # 所以 monkeypatch 要打到 asr_router.get_stage_provider，不是源模块。
    monkeypatch.setattr(
        "appcore.asr_router.get_stage_provider",
        lambda stage: "elevenlabs_tts" if stage == "asr_main" else "doubao_asr",
    )
    adapter, force = asr_router.resolve_adapter("asr_main", "es")
    assert isinstance(adapter, ScribeAdapter)
    assert force == "es"


# -------------------- auto-fallback when adapter doesn't support source --------------------

def test_resolve_es_auto_falls_back_from_doubao_to_scribe():
    """user-picked doubao + source=es → 豆包不支持 es → 自动切到 Scribe，
    并把 es 透传给 force_language（Scribe 强制西语解码）。"""
    adapter, force = asr_router.resolve_adapter("asr_main", "es")
    assert isinstance(adapter, ScribeAdapter)
    assert force == "es"


def test_resolve_de_auto_falls_back_from_doubao_to_scribe():
    adapter, force = asr_router.resolve_adapter("asr_main", "de")
    assert isinstance(adapter, ScribeAdapter)
    assert force == "de"


def test_resolve_zh_does_not_fallback():
    """中文 → 豆包支持，不 fallback。"""
    adapter, force = asr_router.resolve_adapter("asr_main", "zh")
    assert isinstance(adapter, DoubaoAdapter)
    assert force is None


def test_resolve_en_does_not_fallback():
    """英文 → 豆包支持，不 fallback。"""
    adapter, force = asr_router.resolve_adapter("asr_main", "en")
    assert isinstance(adapter, DoubaoAdapter)
    assert force is None


def test_resolve_subtitle_asr_target_lang_fallback_to_scribe():
    """字幕用 ASR：target_lang=fr 跑在 TTS 法语音频上 → 豆包不支持 fr → Scribe。"""
    adapter, force = asr_router.resolve_adapter("subtitle_asr", "fr")
    assert isinstance(adapter, ScribeAdapter)
    assert force == "fr"


# -------------------- transcribe (集成 router + adapter + purify) --------------------

class _FakeAdapter(BaseASRAdapter):
    provider_code = "fake_asr"
    display_name = "Fake"
    default_model_id = "fake-1"

    from appcore.asr_providers.base import ASRCapabilities
    capabilities = ASRCapabilities(
        supports_force_language=True,
        supported_languages=frozenset({"*"}),
        accepts_local_file=True,
    )

    def __init__(self, utterances=None, **kwargs):
        super().__init__(**kwargs)
        self._utterances = utterances or []
        self.last_language: str | None = None

    def transcribe(self, local_audio_path, language=None):
        self.last_language = language
        return self._utterances


def test_transcribe_passes_force_language_for_es(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    fake_utterances = [
        {"text": "Hola amigo, esto es una prueba", "start_time": 0.0, "end_time": 3.0, "words": []},
    ]
    fake = _FakeAdapter(utterances=fake_utterances)
    monkeypatch.setattr(
        "appcore.asr_router.resolve_adapter",
        lambda stage, src: (fake, "es"),
    )
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x")
    out = asr_router.transcribe(audio, source_language="es")
    assert fake.last_language == "es"
    assert len(out["utterances"]) == 1
    assert out["provider_code"] == "fake_asr"
    assert out["model_id"] == "fake-1"
    assert out["display_name"] == "Fake"
    assert out["stage"] == "asr_main"


def test_transcribe_returns_raw_utterances_no_purify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """router 不再做"删段"清理：adapter 给什么就返回什么，下游 LLM 步骤负责规整。

    回归用例：之前 source_language 与音频实际语言不符时（豆包硬识别非中-英文）
    会被 purify_language 一刀切删光，导致流水线"未检测到语音"。现在原样返回。
    """
    raw = [
        {"text": "Hola amigo, esto es una prueba en español", "start_time": 0.0, "end_time": 3.0, "words": []},
        {"text": "你好这是中文污染段落啊啊啊啊啊", "start_time": 3.0, "end_time": 5.0, "words": []},
        {"text": "Adiós a todos, hasta luego en español", "start_time": 5.0, "end_time": 7.0, "words": []},
    ]
    fake = _FakeAdapter(utterances=raw)
    monkeypatch.setattr(
        "appcore.asr_router.resolve_adapter",
        lambda stage, src: (fake, "es"),
    )
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x")
    out = asr_router.transcribe(audio, source_language="es")
    utterances = out["utterances"]
    # 全部 3 段原样返回；中文"污染段"也保留，由下游 LLM 规整改文本（不删段）
    assert len(utterances) == 3
    assert utterances[0]["text"].startswith("Hola")
    assert "你好" in utterances[1]["text"]
    assert utterances[2]["text"].startswith("Adiós")


def test_transcribe_with_auto_source_returns_raw_utterances(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """source=auto 时同样原样返回；行为与显式 source_language 一致。"""
    raw = [
        {"text": "Hola amigo, esto es una prueba en español", "start_time": 0.0, "end_time": 3.0, "words": []},
        {"text": "你好这是一段足够长的中文测试文本啊啊啊啊", "start_time": 3.0, "end_time": 5.0, "words": []},
    ]
    fake = _FakeAdapter(utterances=raw)
    monkeypatch.setattr(
        "appcore.asr_router.resolve_adapter",
        lambda stage, src: (fake, None),
    )
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x")
    out = asr_router.transcribe(audio, source_language="auto")
    assert len(out["utterances"]) == 2


def test_transcribe_passes_stage_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    fake = _FakeAdapter(utterances=[
        {"text": "ok", "start_time": 0.0, "end_time": 1.0, "words": []},
    ])
    captured: dict[str, Any] = {}

    def _resolve(stage: str, src: str | None):
        captured["stage"] = stage
        captured["src"] = src
        return (fake, None)

    monkeypatch.setattr("appcore.asr_router.resolve_adapter", _resolve)
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x")
    out = asr_router.transcribe(audio, source_language="en", stage="subtitle_asr")
    assert captured == {"stage": "subtitle_asr", "src": "en"}
    assert out["stage"] == "subtitle_asr"


# -------------------- routing_config helpers --------------------

def test_routing_config_default_when_setting_missing(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: None)
    assert asr_routing_config.get_stage_provider("asr_main") == "doubao_asr"
    assert asr_routing_config.get_stage_provider("subtitle_asr") == "doubao_asr"


def test_routing_config_reads_override(monkeypatch):
    monkeypatch.setattr(
        "appcore.settings.get_setting",
        lambda key: '{"asr_main": "elevenlabs_tts", "subtitle_asr": "doubao_asr"}',
    )
    assert asr_routing_config.get_stage_provider("asr_main") == "elevenlabs_tts"
    assert asr_routing_config.get_stage_provider("subtitle_asr") == "doubao_asr"


def test_routing_config_unknown_provider_falls_back(monkeypatch):
    monkeypatch.setattr(
        "appcore.settings.get_setting",
        lambda key: '{"asr_main": "no_such_provider"}',
    )
    assert asr_routing_config.get_stage_provider("asr_main") == "doubao_asr"


def test_routing_config_get_all_returns_full_map(monkeypatch):
    monkeypatch.setattr(
        "appcore.settings.get_setting",
        lambda key: '{"asr_main": "elevenlabs_tts"}',
    )
    out = asr_routing_config.get_all_stage_providers()
    assert out == {"asr_main": "elevenlabs_tts", "subtitle_asr": "doubao_asr"}


def test_routing_config_list_available_providers_exposes_registry():
    items = asr_routing_config.list_available_providers()
    codes = [it["provider_code"] for it in items]
    assert "doubao_asr" in codes
    assert "elevenlabs_tts" in codes
