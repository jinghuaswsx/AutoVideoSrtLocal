from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


def test_runtime_rebuilds_av_tts_on_source_timeline_with_silence_gaps(tmp_path):
    from appcore.runtime import _rebuild_tts_full_audio_from_segments

    seg0 = tmp_path / "seg0.mp3"
    seg1 = tmp_path / "seg1.mp3"
    seg0.write_bytes(b"seg0")
    seg1.write_bytes(b"seg1")
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        result = _rebuild_tts_full_audio_from_segments(
            str(tmp_path),
            [
                {
                    "tts_path": str(seg0),
                    "start_time": 0.0,
                    "end_time": 3.0,
                    "tts_duration": 2.0,
                },
                {
                    "tts_path": str(seg1),
                    "start_time": 28.53,
                    "end_time": 31.35,
                    "tts_duration": 2.82,
                },
            ],
            variant="av",
        )

    assert result == str(tmp_path / "tts_full.av.mp3")
    cmd = calls[0]
    assert "-filter_complex" in cmd
    filter_graph = cmd[cmd.index("-filter_complex") + 1]
    assert "adelay=0|0" in filter_graph
    assert "adelay=28530|28530" in filter_graph
    assert "amix=inputs=2" in filter_graph
    assert cmd[cmd.index("-t") + 1] == "31.350"
    assert "-f" not in cmd
    assert "concat" not in cmd


def test_runtime_rebuild_can_pad_av_tts_to_full_video_duration(tmp_path):
    from appcore.runtime import _rebuild_tts_full_audio_from_segments

    seg0 = tmp_path / "seg0.mp3"
    seg0.write_bytes(b"seg0")
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        _rebuild_tts_full_audio_from_segments(
            str(tmp_path),
            [
                {
                    "tts_path": str(seg0),
                    "source_start_time": 13.979,
                    "source_end_time": 15.779,
                    "audio_start_time": 13.979,
                    "audio_end_time": 15.729,
                    "tts_duration": 1.75,
                },
            ],
            variant="av",
            total_duration=48.181,
        )

    cmd = calls[0]
    filter_graph = cmd[cmd.index("-filter_complex") + 1]
    assert "adelay=13979|13979" in filter_graph
    assert cmd[cmd.index("-t") + 1] == "48.181"


def test_source_time_subtitle_units_keep_original_sentence_positions():
    from pipeline.av_subtitle_units import build_subtitle_units_from_sentences

    units = build_subtitle_units_from_sentences(
        [
            {
                "asr_index": 0,
                "text": "First sentence.",
                "start_time": 0.0,
                "end_time": 3.0,
                "target_duration": 3.0,
                "tts_duration": 2.0,
                "status": "warning_short",
            },
            {
                "asr_index": 1,
                "text": "Tail CTA.",
                "start_time": 28.53,
                "end_time": 31.35,
                "target_duration": 2.82,
                "tts_duration": 2.82,
                "status": "ok",
            },
        ],
        mode="sentence",
        timeline_mode="source_time",
    )

    assert len(units) == 2
    assert units[0]["start_time"] == pytest.approx(0.0)
    assert units[0]["end_time"] == pytest.approx(2.0)
    assert units[1]["start_time"] == pytest.approx(28.53)
    assert units[1]["end_time"] == pytest.approx(31.35)


def test_runtime_rebuild_clips_audio_that_exceeds_own_source_window(tmp_path):
    from appcore.runtime import _rebuild_tts_full_audio_from_segments

    seg0 = tmp_path / "seg0.mp3"
    seg1 = tmp_path / "seg1.mp3"
    seg0.write_bytes(b"seg0")
    seg1.write_bytes(b"seg1")
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stderr="")

    segments = [
        {
            "asr_index": 0,
            "tts_path": str(seg0),
            "start_time": 0.0,
            "end_time": 1.0,
            "tts_duration": 1.4,
        },
        {
            "asr_index": 1,
            "tts_path": str(seg1),
            "start_time": 1.2,
            "end_time": 2.0,
            "tts_duration": 0.7,
        },
    ]
    with patch("subprocess.run", side_effect=fake_run):
        _rebuild_tts_full_audio_from_segments(str(tmp_path), segments, variant="av")

    assert calls
    filter_graph = calls[0][calls[0].index("-filter_complex") + 1]
    assert "atrim=duration=1.000" in filter_graph
    assert segments[0]["audio_clipped"] is True
    assert segments[0]["audio_clipped_seconds"] == pytest.approx(0.4)
    assert segments[0]["audio_clip_reason"] == "source_window"


def test_runtime_rebuild_clips_audio_that_would_overlap_next_sentence(tmp_path):
    from appcore.runtime import _rebuild_tts_full_audio_from_segments

    seg0 = tmp_path / "seg0.mp3"
    seg1 = tmp_path / "seg1.mp3"
    seg0.write_bytes(b"seg0")
    seg1.write_bytes(b"seg1")

    segments = [
        {
            "asr_index": 0,
            "tts_path": str(seg0),
            "start_time": 0.0,
            "tts_duration": 1.4,
        },
        {
            "asr_index": 1,
            "tts_path": str(seg1),
            "start_time": 1.2,
            "end_time": 2.0,
            "tts_duration": 0.7,
        },
    ]

    with patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stderr="")) as run:
        _rebuild_tts_full_audio_from_segments(str(tmp_path), segments, variant="av")

    filter_graph = run.call_args[0][0][run.call_args[0][0].index("-filter_complex") + 1]
    assert "atrim=duration=1.200" in filter_graph
    assert segments[0]["audio_clipped"] is True
    assert segments[0]["audio_clip_reason"] == "next_sentence"
