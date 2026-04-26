"""ASR Router 单元测试。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from appcore import asr_router
from appcore.asr_providers import BaseASRAdapter, DoubaoAdapter, ScribeAdapter


# -------------------- resolve_adapter --------------------

def test_resolve_zh_routes_to_doubao():
    adapter, force = asr_router.resolve_adapter("zh")
    assert isinstance(adapter, DoubaoAdapter)
    # 豆包不支持强制语言
    assert force is None


def test_resolve_zh_with_locale_suffix_routes_to_doubao():
    adapter, force = asr_router.resolve_adapter("zh-Hans")
    assert isinstance(adapter, DoubaoAdapter)
    assert force is None


def test_resolve_es_routes_to_scribe_with_force():
    adapter, force = asr_router.resolve_adapter("es")
    assert isinstance(adapter, ScribeAdapter)
    assert force == "es"


def test_resolve_de_routes_to_scribe_with_force():
    adapter, force = asr_router.resolve_adapter("de")
    assert isinstance(adapter, ScribeAdapter)
    assert force == "de"


def test_resolve_unknown_lang_routes_to_scribe_with_force():
    adapter, force = asr_router.resolve_adapter("xx")
    assert isinstance(adapter, ScribeAdapter)
    assert force == "xx"


def test_resolve_auto_routes_to_scribe_no_force():
    adapter, force = asr_router.resolve_adapter("auto")
    assert isinstance(adapter, ScribeAdapter)
    assert force is None


def test_resolve_empty_routes_to_scribe_no_force():
    adapter, force = asr_router.resolve_adapter(None)
    assert isinstance(adapter, ScribeAdapter)
    assert force is None
    adapter, force = asr_router.resolve_adapter("")
    assert isinstance(adapter, ScribeAdapter)
    assert force is None


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
        lambda src: (fake, "es"),
    )
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x")
    out = asr_router.transcribe(audio, source_language="es")
    assert fake.last_language == "es"
    assert len(out) == 1


def test_transcribe_purifies_chinese_pollution_in_es_video(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    polluted = [
        {"text": "Hola amigo, esto es una prueba en español", "start_time": 0.0, "end_time": 3.0, "words": []},
        {"text": "你好这是中文污染段落啊啊啊啊啊", "start_time": 3.0, "end_time": 5.0, "words": []},
        {"text": "Adiós a todos, hasta luego en español", "start_time": 5.0, "end_time": 7.0, "words": []},
    ]
    fake = _FakeAdapter(utterances=polluted)
    monkeypatch.setattr(
        "appcore.asr_router.resolve_adapter",
        lambda src: (fake, "es"),
    )
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x")
    out = asr_router.transcribe(audio, source_language="es")
    assert len(out) == 2
    assert all("你好" not in u["text"] for u in out)
    # 时间合并验证
    assert out[0]["end_time"] == pytest.approx(5.0)
    assert out[1]["start_time"] == pytest.approx(5.0)


def test_transcribe_skips_purify_when_source_auto(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """source=auto 时不跑 purify，所有段保留。"""
    polluted = [
        {"text": "Hola amigo, esto es una prueba en español", "start_time": 0.0, "end_time": 3.0, "words": []},
        {"text": "你好这是一段足够长的中文测试文本啊啊啊啊", "start_time": 3.0, "end_time": 5.0, "words": []},
    ]
    fake = _FakeAdapter(utterances=polluted)
    monkeypatch.setattr(
        "appcore.asr_router.resolve_adapter",
        lambda src: (fake, None),
    )
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x")
    out = asr_router.transcribe(audio, source_language="auto")
    assert len(out) == 2
