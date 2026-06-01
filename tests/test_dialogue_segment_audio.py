from __future__ import annotations

import subprocess
from pathlib import Path

from appcore.dialogue_translate.segment_audio import build_dialogue_segment_audio_assets


def test_build_dialogue_segment_audio_assets_extracts_sentence_clips_and_speaker_tracks(
    monkeypatch,
    tmp_path,
):
    calls: list[list[str]] = []
    track_calls: list[tuple[str, list[list[float]], str]] = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)
        assert check is True
        assert capture_output is True
        assert text is True
        Path(cmd[-1]).write_bytes(b"sentence wav")
        return subprocess.CompletedProcess(cmd, 0)

    def fake_extract_sample_for_windows(video_path, windows, out_path):
        track_calls.append((video_path, windows, out_path.name))
        out_path.write_bytes(b"speaker track")
        return str(out_path)

    monkeypatch.setattr("appcore.dialogue_translate.segment_audio.subprocess.run", fake_run)
    monkeypatch.setattr(
        "appcore.dialogue_translate.segment_audio.extract_sample_for_windows",
        fake_extract_sample_for_windows,
    )

    result = build_dialogue_segment_audio_assets(
        video_path="source.mp4",
        task_dir=str(tmp_path),
        dialogue_segments=[
            {
                "index": 0,
                "speaker_id": "A",
                "start_time": 0.0,
                "end_time": 1.25,
                "text": "first",
            },
            {
                "index": 1,
                "speaker_id": "B",
                "start_time": 2.0,
                "end_time": 3.0,
                "text": "second",
            },
        ],
    )

    segments = result["dialogue_segments"]
    assert segments[0]["source_audio_relpath"] == "dialogue_segments/segment_000_speaker_A.wav"
    assert segments[1]["source_audio_relpath"] == "dialogue_segments/segment_001_speaker_B.wav"
    assert result["dialogue_segment_audio_manifest"]["segments"] == [
        {
            "index": 0,
            "speaker_id": "A",
            "start_time": 0.0,
            "end_time": 1.25,
            "duration": 1.25,
            "source_audio_relpath": "dialogue_segments/segment_000_speaker_A.wav",
        },
        {
            "index": 1,
            "speaker_id": "B",
            "start_time": 2.0,
            "end_time": 3.0,
            "duration": 1.0,
            "source_audio_relpath": "dialogue_segments/segment_001_speaker_B.wav",
        },
    ]
    assert result["speaker_audio_tracks"] == {
        "A": {
            "relative_path": "dialogue_segments/speaker_A_source.wav",
            "segment_count": 1,
            "duration": 1.25,
        },
        "B": {
            "relative_path": "dialogue_segments/speaker_B_source.wav",
            "segment_count": 1,
            "duration": 1.0,
        },
    }
    assert calls[0] == [
        "ffmpeg",
        "-y",
        "-ss",
        "0.000",
        "-i",
        "source.mp4",
        "-t",
        "1.250",
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(tmp_path / "dialogue_segments" / "segment_000_speaker_A.wav"),
    ]
    assert track_calls == [
        ("source.mp4", [[0.0, 1.25]], "speaker_A_source.wav"),
        ("source.mp4", [[2.0, 3.0]], "speaker_B_source.wav"),
    ]
