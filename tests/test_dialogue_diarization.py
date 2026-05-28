from __future__ import annotations

import pytest

from appcore.dialogue_translate.diarization import (
    DiarizationUnavailable,
    HttpDiarizationClient,
    resolve_diarization_client,
)
from appcore.dialogue_translate.speaker_detection import detect_dialogue_segments


class FakeDiarizationClient:
    def run(self, *, audio_path: str, task_id: str) -> list[dict]:
        assert audio_path == "input.mp4"
        assert task_id == "task-1"
        return [
            {"speaker": "s1", "start_time": 0.0, "end_time": 1.0, "confidence": 0.95},
            {"speaker": "s2", "start_time": 1.2, "end_time": 2.0, "confidence": 0.93},
        ]


def test_detect_uses_diarization_when_provider_is_unreliable():
    utterances = [
        {"text": "hello", "start_time": 0.0, "end_time": 1.0},
        {"text": "yes", "start_time": 1.2, "end_time": 2.0},
    ]

    result = detect_dialogue_segments(
        utterances=utterances,
        audio_path="input.mp4",
        task_id="task-1",
        diarization_client=FakeDiarizationClient(),
    )

    assert result["speaker_strategy"] == "diarization"
    assert [s["speaker_id"] for s in result["dialogue_segments"]] == ["A", "B"]


def test_detect_raises_when_diarization_required_but_unavailable():
    utterances = [{"text": "hello", "start_time": 0.0, "end_time": 1.0}]

    with pytest.raises(DiarizationUnavailable) as exc:
        detect_dialogue_segments(
            utterances=utterances,
            audio_path="input.mp4",
            task_id="task-2",
            diarization_client=None,
        )

    assert "diarization fallback is required" in str(exc.value)


def test_resolve_diarization_client_requires_endpoint(monkeypatch):
    monkeypatch.delenv("DIALOGUE_DIARIZATION_URL", raising=False)

    with pytest.raises(DiarizationUnavailable):
        resolve_diarization_client()


def test_http_diarization_client_posts_audio(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"segments": [{"speaker": "x", "start_time": 0.0, "end_time": 1.0}]}

    def fake_post(url, files, data, timeout):
        captured["url"] = url
        captured["data"] = data
        captured["timeout"] = timeout
        files["audio"].close()
        return FakeResponse()

    audio = monkeypatch.context()
    with audio:
        import tempfile

        path = tempfile.NamedTemporaryFile(delete=False)
        path.write(b"audio")
        path.close()
        monkeypatch.setattr("requests.post", fake_post)

        client = HttpDiarizationClient(endpoint="http://diarizer.local/run", timeout_seconds=12)
        segments = client.run(audio_path=path.name, task_id="task-http")

    assert segments == [{"speaker": "x", "start_time": 0.0, "end_time": 1.0}]
    assert captured["url"] == "http://diarizer.local/run"
    assert captured["data"] == {"task_id": "task-http"}
    assert captured["timeout"] == 12


def test_http_diarization_client_accepts_positional_audio_path_and_task_id(monkeypatch):
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"segments": [{"speaker": "x", "start_time": 0.0, "end_time": 1.0}]}

    def fake_post(url, files, data, timeout):
        files["audio"].close()
        return FakeResponse()

    audio = monkeypatch.context()
    with audio:
        import tempfile

        path = tempfile.NamedTemporaryFile(delete=False)
        path.write(b"audio")
        path.close()
        monkeypatch.setattr("requests.post", fake_post)

        client = HttpDiarizationClient(endpoint="http://diarizer.local/run", timeout_seconds=12)
        segments = client.run(path.name, "task-http")

    assert segments == [{"speaker": "x", "start_time": 0.0, "end_time": 1.0}]
