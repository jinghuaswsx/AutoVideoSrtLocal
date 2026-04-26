"""DoubaoAdapter 单元测试（mock requests，不真调火山云）。"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from appcore.asr_providers import build_adapter
from appcore.asr_providers.doubao import DoubaoAdapter


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict | None = None,
        json_data: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_data or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._json


def _stub_resolve_key(monkeypatch: pytest.MonkeyPatch, key: str = "fake-key") -> None:
    monkeypatch.setattr(
        "appcore.asr_providers.doubao._resolve_doubao_asr_key",
        lambda: key,
    )
    monkeypatch.setattr(
        "appcore.asr_providers.doubao._resolve_doubao_asr_resource_id",
        lambda: "volc.seedasr.auc",
    )


def test_capabilities_declare_no_force_language() -> None:
    cap = DoubaoAdapter().capabilities
    assert cap.supports_force_language is False
    assert "zh" in cap.supported_languages
    assert "en" in cap.supported_languages
    assert cap.accepts_local_file is False


def test_transcribe_url_submit_then_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_resolve_key(monkeypatch)

    submit_resp = _FakeResponse(headers={"X-Api-Status-Code": "20000000"})
    poll_resp = _FakeResponse(
        headers={"X-Api-Status-Code": "20000000"},
        json_data={
            "result": {
                "utterances": [
                    {
                        "text": "Hola mundo",
                        "start_time": 1000,
                        "end_time": 2500,
                        "words": [
                            {"text": "Hola", "start_time": 1000, "end_time": 1500, "confidence": 0.95},
                            {"text": "mundo", "start_time": 1600, "end_time": 2500, "confidence": 0.9},
                        ],
                    }
                ]
            }
        },
    )
    calls: list[str] = []

    def _fake_post(url: str, **kwargs):
        calls.append(url)
        if "submit" in url.lower() or len(calls) == 1:
            return submit_resp
        return poll_resp

    monkeypatch.setattr("appcore.asr_providers.doubao.requests.post", _fake_post)
    adapter = DoubaoAdapter()
    out = adapter.transcribe_url("https://example.com/a.mp3")
    assert len(out) == 1
    seg = out[0]
    assert seg["text"] == "Hola mundo"
    assert seg["start_time"] == pytest.approx(1.0)
    assert seg["end_time"] == pytest.approx(2.5)
    assert len(seg["words"]) == 2
    assert seg["words"][0]["text"] == "Hola"


def test_transcribe_url_silent_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_resolve_key(monkeypatch)

    submit_resp = _FakeResponse(headers={"X-Api-Status-Code": "20000000"})
    silent_resp = _FakeResponse(
        headers={"X-Api-Status-Code": "20000003"},
        json_data={"resp": {"text": "", "utterances": []}},
    )
    seq = iter([submit_resp, silent_resp])
    monkeypatch.setattr(
        "appcore.asr_providers.doubao.requests.post",
        lambda *a, **kw: next(seq),
    )
    out = DoubaoAdapter().transcribe_url("https://example.com/silent.mp3")
    assert out == []


def test_transcribe_url_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_resolve_key(monkeypatch)
    bad = _FakeResponse(
        headers={"X-Api-Status-Code": "55000001", "X-Api-Message": "boom"},
    )
    monkeypatch.setattr(
        "appcore.asr_providers.doubao.requests.post",
        lambda *a, **kw: bad,
    )
    with pytest.raises(RuntimeError, match="提交失败"):
        DoubaoAdapter().transcribe_url("https://example.com/x.mp3")


def test_transcribe_local_uploads_then_cleans(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_resolve_key(monkeypatch)

    upload_calls: list[tuple[str, str]] = []
    delete_calls: list[str] = []

    def _fake_upload(path: str, key: str) -> str:
        upload_calls.append((path, key))
        return f"https://tos.example.com/{key}"

    def _fake_delete(key: str) -> None:
        delete_calls.append(key)

    fake_storage = mock.MagicMock()
    fake_storage.upload_file = _fake_upload
    fake_storage.delete_file = _fake_delete
    monkeypatch.setitem(__import__("sys").modules, "pipeline.storage", fake_storage)

    submit_resp = _FakeResponse(headers={"X-Api-Status-Code": "20000000"})
    poll_resp = _FakeResponse(
        headers={"X-Api-Status-Code": "20000000"},
        json_data={"result": {"utterances": []}},
    )
    seq = iter([submit_resp, poll_resp])
    monkeypatch.setattr(
        "appcore.asr_providers.doubao.requests.post",
        lambda *a, **kw: next(seq),
    )

    audio = tmp_path / "input.mp3"
    audio.write_bytes(b"fake-mp3")
    out = DoubaoAdapter().transcribe(audio, language="es")  # language 被忽略
    assert out == []
    assert len(upload_calls) == 1
    assert upload_calls[0][0] == str(audio)
    assert len(delete_calls) == 1
    assert delete_calls[0] == upload_calls[0][1]


def test_legacy_wrapper_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧 pipeline.asr.transcribe(url) 仍可调通。"""
    _stub_resolve_key(monkeypatch)
    submit_resp = _FakeResponse(headers={"X-Api-Status-Code": "20000000"})
    poll_resp = _FakeResponse(
        headers={"X-Api-Status-Code": "20000000"},
        json_data={"result": {"utterances": [{"text": "ok", "start_time": 0, "end_time": 100}]}},
    )
    seq = iter([submit_resp, poll_resp])
    monkeypatch.setattr(
        "appcore.asr_providers.doubao.requests.post",
        lambda *a, **kw: next(seq),
    )
    from pipeline.asr import transcribe

    out = transcribe("https://example.com/x.mp3")
    assert len(out) == 1
    assert out[0]["text"] == "ok"


def test_build_adapter_does_not_know_doubao_yet() -> None:
    """REGISTRY 在 Task 5 才注册；这里仅占位说明，无 assert。"""
    # adapter 类本身可以直接构造
    adapter = DoubaoAdapter(model_id="bigmodel-test")
    assert adapter.model_id == "bigmodel-test"
    assert adapter.provider_code == "doubao_asr"
