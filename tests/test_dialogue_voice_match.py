from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest

from appcore.dialogue_translate.voice_match import (
    INSUFFICIENT_SAMPLE_REASON,
    MALFORMED_SEGMENT_REASON,
    NO_VOICE_CANDIDATES_REASON,
    build_speaker_sample_windows,
    extract_sample_for_windows,
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


def test_sample_windows_skip_malformed_segments_and_warn():
    segments = [
        None,
        "bad row",
        {"speaker_id": "A", "start_time": 0.0, "end_time": 4.0, "review_required": False, "overlap": False},
        {"speaker_id": "B", "start_time": "oops", "end_time": 6.0, "review_required": False, "overlap": False},
    ]

    result = build_speaker_sample_windows(segments, min_duration=3.0, target_duration=8.0)

    assert result["A"]["sample_windows"] == [[0.0, 4.0]]
    assert result["A"]["match_warnings"] == [MALFORMED_SEGMENT_REASON]
    assert result["B"]["sample_windows"] == []
    assert result["B"]["match_warnings"] == [MALFORMED_SEGMENT_REASON, INSUFFICIENT_SAMPLE_REASON]


def test_extract_sample_for_windows_rejects_invalid_window_shape(tmp_path):
    with pytest.raises(ValueError, match="invalid sample window"):
        extract_sample_for_windows("video.mp4", [None], tmp_path / "sample.wav")


def test_extract_sample_for_windows_runs_ffmpeg_concat_and_cleans_temp_files(monkeypatch, tmp_path):
    out_path = tmp_path / "sample.wav"
    calls = []
    concat_list_content = {}

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)
        assert check is True
        assert capture_output is True
        assert text is True
        if "-f" in cmd and "concat" in cmd:
            list_path = tmp_path / "sample.wav.concat.txt"
            concat_list_content["text"] = list_path.read_text(encoding="utf-8")
            out_path.write_bytes(b"joined")
        else:
            Path(cmd[-1]).write_bytes(b"part")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("appcore.dialogue_translate.voice_match.subprocess.run", fake_run)

    result = extract_sample_for_windows(
        "video.mp4",
        [[0.0, 1.25], [2.0, 4.5]],
        out_path,
    )

    assert result == str(out_path)
    assert len(calls) == 3
    assert calls[0] == [
        "ffmpeg",
        "-y",
        "-ss",
        "0.000",
        "-i",
        "video.mp4",
        "-t",
        "1.250",
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(tmp_path / "sample.part0.wav"),
    ]
    assert calls[1][-1] == str(tmp_path / "sample.part1.wav")
    assert calls[2] == [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(tmp_path / "sample.wav.concat.txt"),
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(out_path),
    ]
    assert "sample.part0.wav" in concat_list_content["text"]
    assert "sample.part1.wav" in concat_list_content["text"]
    assert not (tmp_path / "sample.part0.wav").exists()
    assert not (tmp_path / "sample.part1.wav").exists()
    assert not (tmp_path / "sample.wav.concat.txt").exists()
    assert out_path.read_bytes() == b"joined"


def test_extract_sample_for_windows_reports_ffmpeg_errors(monkeypatch, tmp_path):
    def fake_run(cmd, check, capture_output, text):
        raise subprocess.CalledProcessError(2, cmd, stderr="boom")

    monkeypatch.setattr("appcore.dialogue_translate.voice_match.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="ffmpeg sample window extraction failed: boom"):
        extract_sample_for_windows("video.mp4", [[0.0, 1.0]], tmp_path / "sample.wav")

    assert not (tmp_path / "sample.part0.wav").exists()
    assert not (tmp_path / "sample.wav.concat.txt").exists()


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
        calls.append(("match", vec, kwargs["language"], kwargs["source_utterances"], kwargs["exclude_voice_ids"]))
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
        dialogue_segments=[
            {"speaker_id": "A", "text": "hi"},
            {"speaker_id": "B", "text": "yes"},
            {"speaker_id": "C", "text": "mixed"},
            None,
        ],
        sample_specs=sample_specs,
        user_id=7,
    )

    assert profiles["A"]["candidates"][0]["voice_id"] == "voice-a"
    assert profiles["B"]["candidates"][0]["voice_id"] == "voice-b"
    assert base64.b64decode(profiles["A"]["query_embedding"]).startswith(b"vec:")
    assert calls[0][0] == "extract"
    assert calls[1][0] == "match"
    match_calls = [call for call in calls if call[0] == "match"]
    assert match_calls[0][3] == [{"speaker_id": "A", "text": "hi"}]
    assert match_calls[1][3] == [{"speaker_id": "B", "text": "yes"}]
    assert set(match_calls[0][4]) == {"default-voice"}
    assert set(match_calls[1][4]) == {"default-voice"}


def test_match_voices_for_speakers_warns_when_no_candidates(monkeypatch, tmp_path):
    sample_specs = {
        "A": {"sample_windows": [[0.0, 5.0]], "match_warnings": []},
        "B": {"sample_windows": [], "match_warnings": [INSUFFICIENT_SAMPLE_REASON]},
    }

    def fake_extract(video_path, windows, out_path):
        out_path.write_bytes(b"wav")
        return str(out_path)

    monkeypatch.setattr("appcore.dialogue_translate.voice_match.extract_sample_for_windows", fake_extract)
    monkeypatch.setattr("pipeline.voice_embedding.embed_audio_file", lambda path: f"vec:{path}")
    monkeypatch.setattr("pipeline.voice_embedding.serialize_embedding", lambda vec: vec.encode("utf-8"))
    monkeypatch.setattr("pipeline.voice_match_speed.match_candidates_speed_aware", lambda vec, **kwargs: [])
    monkeypatch.setattr("appcore.dialogue_translate.voice_match.resolve_default_voice", lambda lang, user_id=None: None)

    profiles = match_voices_for_speakers(
        video_path="video.mp4",
        task_dir=str(tmp_path),
        target_lang="en",
        dialogue_segments=[{"speaker_id": "A", "text": "hi"}],
        sample_specs=sample_specs,
    )

    assert profiles["A"]["candidates"] == []
    assert profiles["A"]["match_warnings"] == [NO_VOICE_CANDIDATES_REASON]
