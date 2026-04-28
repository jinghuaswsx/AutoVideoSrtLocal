"""ScribeAdapter 单元测试（mock requests，不真调 ElevenLabs）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from appcore.asr_providers.scribe import ScribeAdapter, parse_scribe_response


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


def test_parse_scribe_response_splits_long_unpunctuated_stream() -> None:
    payload = {
        "audio_duration_secs": 20.0,
        "words": [
            {"text": "this", "start": 3.0, "end": 3.6, "type": "word"},
            {"text": "model", "start": 3.7, "end": 4.5, "type": "word"},
            {"text": "has", "start": 4.6, "end": 5.0, "type": "word"},
            {"text": "a", "start": 5.1, "end": 5.3, "type": "word"},
            {"text": "really", "start": 5.4, "end": 6.2, "type": "word"},
            {"text": "long", "start": 6.3, "end": 7.0, "type": "word"},
            {"text": "demo", "start": 7.1, "end": 8.0, "type": "word"},
            {"text": "without", "start": 8.1, "end": 9.0, "type": "word"},
            {"text": "clear", "start": 9.1, "end": 9.8, "type": "word"},
            {"text": "punctuation", "start": 9.9, "end": 10.8, "type": "word"},
            {"text": "so", "start": 10.9, "end": 11.2, "type": "word"},
            {"text": "we", "start": 11.3, "end": 11.6, "type": "word"},
            {"text": "need", "start": 11.7, "end": 12.2, "type": "word"},
            {"text": "a", "start": 12.3, "end": 12.5, "type": "word"},
            {"text": "timing", "start": 12.6, "end": 13.3, "type": "word"},
            {"text": "break", "start": 13.4, "end": 14.1, "type": "word"},
            {"text": "before", "start": 14.2, "end": 15.0, "type": "word"},
            {"text": "eighteen", "start": 15.1, "end": 16.0, "type": "word"},
            {"text": "seconds", "start": 16.1, "end": 17.0, "type": "word"},
            {"text": "arrives", "start": 17.1, "end": 18.0, "type": "word"},
        ],
    }

    out = parse_scribe_response(payload)

    assert len(out) >= 2
    assert out[0]["start_time"] == 3.0
    assert out[-1]["end_time"] == 18.0
    assert any(item.get("force_break_after") for item in out[:-1])
    assert max(item["end_time"] - item["start_time"] for item in out) <= 6.5
