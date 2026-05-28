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


class LowConfidenceFallbackClient:
    def run(self, *, audio_path: str, task_id: str) -> list[dict]:
        assert audio_path == "input.mp4"
        assert task_id == "task-low-confidence"
        return [
            {"speaker": "s1", "start_time": 0.0, "end_time": 1.0, "confidence": 0.95},
            {"speaker": "s2", "start_time": 1.1, "end_time": 2.0, "confidence": 0.94},
        ]


class FailingDiarizationClient:
    def run(self, *, audio_path: str, task_id: str) -> list[dict]:
        raise RuntimeError("diarizer crashed")


class EmptyDiarizationClient:
    def run(self, *, audio_path: str, task_id: str) -> list[dict]:
        return []


class TupleDiarizationClient:
    def run(self, *, audio_path: str, task_id: str) -> tuple[dict, ...]:
        return ({"speaker": "s1", "start_time": 0.0, "end_time": 1.0, "confidence": 0.95},)


class ValidDiarizationClient:
    def run(self, *, audio_path: str, task_id: str) -> list[dict]:
        return [{"speaker": "s1", "start_time": 0.0, "end_time": 1.0, "confidence": 0.95}]


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


def test_detect_uses_diarization_when_provider_speaker_confidence_is_low():
    utterances = [
        {"text": "hello", "start_time": 0.0, "end_time": 1.0, "speaker": "a", "speaker_confidence": 0.99},
        {"text": "yes", "start_time": 1.1, "end_time": 2.0, "speaker": "b", "speaker_confidence": 0.2},
    ]

    result = detect_dialogue_segments(
        utterances=utterances,
        audio_path="input.mp4",
        task_id="task-low-confidence",
        diarization_client=LowConfidenceFallbackClient(),
    )

    assert result["speaker_strategy"] == "diarization"
    assert [s["speaker_id"] for s in result["dialogue_segments"]] == ["A", "B"]


def test_detect_raises_when_diarization_required_but_unavailable(monkeypatch):
    monkeypatch.delenv("DIALOGUE_DIARIZATION_URL", raising=False)
    utterances = [{"text": "hello", "start_time": 0.0, "end_time": 1.0}]

    with pytest.raises(DiarizationUnavailable) as exc:
        detect_dialogue_segments(
            utterances=utterances,
            audio_path="input.mp4",
            task_id="task-2",
            diarization_client=None,
        )

    assert "diarization fallback is required" in str(exc.value)


def test_detect_wraps_diarization_client_failure_with_task_id():
    utterances = [{"text": "hello", "start_time": 0.0, "end_time": 1.0}]

    with pytest.raises(DiarizationUnavailable) as exc:
        detect_dialogue_segments(
            utterances=utterances,
            audio_path="input.mp4",
            task_id="task-failure",
            diarization_client=FailingDiarizationClient(),
        )

    assert "diarization fallback failed for task task-failure" in str(exc.value)
    assert "diarizer crashed" in str(exc.value)


def test_detect_wraps_injected_empty_diarization_segments_with_task_id():
    utterances = [{"text": "hello", "start_time": 0.0, "end_time": 1.0}]

    with pytest.raises(DiarizationUnavailable) as exc:
        detect_dialogue_segments(
            utterances=utterances,
            audio_path="input.mp4",
            task_id="task-empty",
            diarization_client=EmptyDiarizationClient(),
        )

    assert "diarization fallback failed for task task-empty" in str(exc.value)
    assert "no segments" in str(exc.value)


def test_detect_wraps_injected_non_list_diarization_segments_with_task_id():
    utterances = [{"text": "hello", "start_time": 0.0, "end_time": 1.0}]

    with pytest.raises(DiarizationUnavailable) as exc:
        detect_dialogue_segments(
            utterances=utterances,
            audio_path="input.mp4",
            task_id="task-tuple",
            diarization_client=TupleDiarizationClient(),
        )

    assert "diarization fallback failed for task task-tuple" in str(exc.value)


def test_detect_wraps_diarization_join_failure_with_task_id(monkeypatch):
    utterances = [{"text": "hello", "start_time": 0.0, "end_time": 1.0}]

    def fail_join(utterances, diarization_segments):
        raise ValueError("join exploded")

    monkeypatch.setattr("appcore.dialogue_translate.speaker_detection.join_diarization_to_utterances", fail_join)

    with pytest.raises(DiarizationUnavailable) as exc:
        detect_dialogue_segments(
            utterances=utterances,
            audio_path="input.mp4",
            task_id="task-join",
            diarization_client=ValidDiarizationClient(),
        )

    assert "diarization fallback failed for task task-join" in str(exc.value)
    assert "join exploded" in str(exc.value)


def test_resolve_diarization_client_requires_endpoint(monkeypatch):
    monkeypatch.delenv("DIALOGUE_DIARIZATION_URL", raising=False)

    with pytest.raises(DiarizationUnavailable):
        resolve_diarization_client()


def test_http_diarization_client_posts_audio(monkeypatch, tmp_path):
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

    path = tmp_path / "audio.mp4"
    path.write_bytes(b"audio")
    monkeypatch.setattr("requests.post", fake_post)

    client = HttpDiarizationClient(endpoint="http://diarizer.local/run", timeout_seconds=12)
    segments = client.run(audio_path=str(path), task_id="task-http")

    assert segments == [{"speaker": "x", "start_time": 0.0, "end_time": 1.0}]
    assert captured["url"] == "http://diarizer.local/run"
    assert captured["data"] == {"task_id": "task-http"}
    assert captured["timeout"] == 12


def test_http_diarization_client_accepts_positional_audio_path_and_task_id(monkeypatch, tmp_path):
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"segments": [{"speaker": "x", "start_time": 0.0, "end_time": 1.0}]}

    def fake_post(url, files, data, timeout):
        files["audio"].close()
        return FakeResponse()

    path = tmp_path / "audio.mp4"
    path.write_bytes(b"audio")
    monkeypatch.setattr("requests.post", fake_post)

    client = HttpDiarizationClient(endpoint="http://diarizer.local/run", timeout_seconds=12)
    segments = client.run(str(path), "task-http")

    assert segments == [{"speaker": "x", "start_time": 0.0, "end_time": 1.0}]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"segments": {"speaker": "x", "start_time": 0.0, "end_time": 1.0}},
        {"segments": []},
        {"segments": ["not-a-dict"]},
        {"segments": [{"start_time": 0.0, "end_time": 1.0}]},
        {"segments": [{"speaker": "x", "start_time": "bad", "end_time": 1.0}]},
        {"segments": [{"speaker": "x", "start_time": 0.0, "end_time": "nan"}]},
        {"segments": [{"speaker": "x", "start_time": 2.0, "end_time": 1.0}]},
    ],
)
def test_http_diarization_client_rejects_empty_or_malformed_segments(monkeypatch, tmp_path, payload):
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return payload

    def fake_post(url, files, data, timeout):
        files["audio"].close()
        return FakeResponse()

    path = tmp_path / "audio.mp4"
    path.write_bytes(b"audio")
    monkeypatch.setattr("requests.post", fake_post)

    client = HttpDiarizationClient(endpoint="http://diarizer.local/run", timeout_seconds=12)

    with pytest.raises(DiarizationUnavailable):
        client.run(str(path), "task-http")
