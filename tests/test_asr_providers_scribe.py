"""ScribeAdapter 单元测试（mock requests，不真调 ElevenLabs）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from appcore.asr_providers.scribe import ScribeAdapter


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: dict | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self) -> dict:
        return self._json


def _stub_key(monkeypatch: pytest.MonkeyPatch, key: str = "fake-elevenlabs-key") -> None:
    monkeypatch.setattr(
        "appcore.asr_providers.scribe._resolve_elevenlabs_api_key",
        lambda: key,
    )


def test_capabilities_supports_force_language_for_all() -> None:
    cap = ScribeAdapter().capabilities
    assert cap.supports_force_language is True
    assert "*" in cap.supported_languages
    assert cap.accepts_local_file is True
    assert cap.supports_language("zh") is True
    assert cap.supports_language("xx-unknown") is True


def test_default_model_is_scribe_v2() -> None:
    assert ScribeAdapter().model_id == "scribe_v2"
    assert ScribeAdapter(model_id="scribe_v3").model_id == "scribe_v3"


def test_transcribe_passes_language_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_key(monkeypatch)
    captured: dict[str, Any] = {}

    def _fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["data"] = kwargs.get("data")
        captured["headers"] = kwargs.get("headers")
        return _FakeResponse(
            json_data={
                "language_code": "es",
                "language_probability": 0.99,
                "audio_duration_secs": 1.0,
                "words": [
                    {"text": "Hola", "start": 0.0, "end": 0.4, "type": "word"},
                    {"text": "mundo.", "start": 0.5, "end": 1.0, "type": "word"},
                ],
            }
        )

    monkeypatch.setattr("appcore.asr_providers.scribe.requests.post", _fake_post)
    audio = tmp_path / "input.mp3"
    audio.write_bytes(b"fake-mp3")

    out = ScribeAdapter().transcribe(audio, language="es")
    assert captured["data"]["language_code"] == "es"
    assert captured["data"]["model_id"] == "scribe_v2"
    assert captured["data"]["timestamps_granularity"] == "word"
    assert captured["headers"]["xi-api-key"] == "fake-elevenlabs-key"
    assert len(out) == 1
    assert out[0]["text"] == "Hola mundo."


def test_transcribe_no_language_omits_param(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_key(monkeypatch)
    captured: dict[str, Any] = {}

    def _fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured["data"] = kwargs.get("data")
        return _FakeResponse(
            json_data={"words": [], "text": "", "audio_duration_secs": 0.0}
        )

    monkeypatch.setattr("appcore.asr_providers.scribe.requests.post", _fake_post)
    audio = tmp_path / "input.mp3"
    audio.write_bytes(b"x")
    ScribeAdapter().transcribe(audio, language=None)
    assert "language_code" not in captured["data"]


def test_transcribe_http_error_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_key(monkeypatch)
    monkeypatch.setattr(
        "appcore.asr_providers.scribe.requests.post",
        lambda *a, **kw: _FakeResponse(status_code=500, text="server boom"),
    )
    audio = tmp_path / "x.mp3"
    audio.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="HTTP 500"):
        ScribeAdapter().transcribe(audio, language="es")


def test_legacy_wrapper_compat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """旧 pipeline.asr_scribe.transcribe_local_audio 仍可调通。"""
    _stub_key(monkeypatch)
    monkeypatch.setattr(
        "appcore.asr_providers.scribe.requests.post",
        lambda *a, **kw: _FakeResponse(
            json_data={
                "audio_duration_secs": 0.5,
                "words": [{"text": "Hi.", "start": 0.0, "end": 0.5, "type": "word"}],
            }
        ),
    )
    from pipeline.asr_scribe import transcribe_local_audio

    audio = tmp_path / "x.mp3"
    audio.write_bytes(b"x")
    out = transcribe_local_audio(str(audio), language_code="en")
    assert len(out) == 1
    assert out[0]["text"] == "Hi."
