from __future__ import annotations

import base64

from appcore.dialogue_translate.voice_match import (
    INSUFFICIENT_SAMPLE_REASON,
    build_speaker_sample_windows,
    match_voices_for_speakers,
)


def test_sample_windows_skip_review_and_overlap_segments():
    segments = [
        {"index": 0, "speaker_id": "A", "start_time": 0.0, "end_time": 2.0, "review_required": False, "overlap": False},
        {"index": 1, "speaker_id": "A", "start_time": 2.1, "end_time": 3.0, "review_required": True, "overlap": False},
        {"index": 2, "speaker_id": "B", "start_time": 3.2, "end_time": 7.2, "review_required": False, "overlap": False},
        {"index": 3, "speaker_id": "A", "start_time": 8.0, "end_time": 13.0, "review_required": False, "overlap": False},
    ]

    result = build_speaker_sample_windows(segments, min_duration=3.0, target_duration=8.0)

    assert result["A"]["sample_windows"] == [[8.0, 13.0], [0.0, 2.0]]
    assert result["A"]["match_warnings"] == []
    assert result["B"]["sample_windows"] == [[3.2, 7.2]]
    assert result["B"]["match_warnings"] == []


def test_sample_windows_warn_when_speaker_has_too_little_audio():
    segments = [
        {"speaker_id": "A", "start_time": 0.0, "end_time": 1.0, "review_required": False, "overlap": False},
    ]

    result = build_speaker_sample_windows(segments, min_duration=3.0, target_duration=8.0)

    assert result["A"]["sample_windows"] == [[0.0, 1.0]]
    assert result["A"]["match_warnings"] == [INSUFFICIENT_SAMPLE_REASON]
    assert result["B"]["sample_windows"] == []
    assert result["B"]["match_warnings"] == [INSUFFICIENT_SAMPLE_REASON]


def test_match_voices_for_speakers_uses_existing_embedding_and_speed_match(monkeypatch, tmp_path):
    sample_specs = {
        "A": {"sample_windows": [[0.0, 5.0]], "match_warnings": []},
        "B": {"sample_windows": [[5.2, 10.0]], "match_warnings": []},
    }
    calls = []

    def fake_extract(video_path, windows, out_path):
        calls.append(("extract", video_path, windows, out_path.name))
        out_path.write_bytes(b"wav")
        return str(out_path)

    def fake_embed(path):
        return f"vec:{path}"

    def fake_serialize(vec):
        return vec.encode("utf-8")

    def fake_match(vec, **kwargs):
        calls.append(("match", vec, kwargs["language"], kwargs["source_utterances"]))
        suffix = "a" if "speaker_A" in vec else "b"
        return [{"voice_id": f"voice-{suffix}", "name": f"Voice {suffix.upper()}", "similarity": 0.91}]

    monkeypatch.setattr("appcore.dialogue_translate.voice_match.extract_sample_for_windows", fake_extract)
    monkeypatch.setattr("pipeline.voice_embedding.embed_audio_file", fake_embed)
    monkeypatch.setattr("pipeline.voice_embedding.serialize_embedding", fake_serialize)
    monkeypatch.setattr("pipeline.voice_match_speed.match_candidates_speed_aware", fake_match)
    monkeypatch.setattr("appcore.dialogue_translate.voice_match.resolve_default_voice", lambda lang, user_id=None: "default-voice")

    profiles = match_voices_for_speakers(
        video_path="video.mp4",
        task_dir=str(tmp_path),
        target_lang="en",
        dialogue_segments=[{"text": "hi"}, {"text": "yes"}],
        sample_specs=sample_specs,
        user_id=7,
    )

    assert profiles["A"]["candidates"][0]["voice_id"] == "voice-a"
    assert profiles["B"]["candidates"][0]["voice_id"] == "voice-b"
    assert base64.b64decode(profiles["A"]["query_embedding"]).startswith(b"vec:")
    assert calls[0][0] == "extract"
    assert calls[1][0] == "match"
