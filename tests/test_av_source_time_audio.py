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


def test_runtime_rebuild_rejects_audio_that_exceeds_own_source_window(tmp_path):
    from appcore.runtime import _rebuild_tts_full_audio_from_segments
    from pipeline.audio_stitch import TimelineAudioOverflowError

    seg0 = tmp_path / "seg0.mp3"
    seg1 = tmp_path / "seg1.mp3"
    seg0.write_bytes(b"seg0")
    seg1.write_bytes(b"seg1")
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stderr="")

    with pytest.raises(TimelineAudioOverflowError, match="exceeds source window"):
        with patch("subprocess.run", side_effect=fake_run):
            _rebuild_tts_full_audio_from_segments(
                str(tmp_path),
                [
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
                ],
                variant="av",
            )

    assert calls == []


def test_runtime_rebuild_rejects_audio_that_would_overlap_next_sentence(tmp_path):
    from appcore.runtime import _rebuild_tts_full_audio_from_segments
    from pipeline.audio_stitch import TimelineAudioOverflowError

    seg0 = tmp_path / "seg0.mp3"
    seg1 = tmp_path / "seg1.mp3"
    seg0.write_bytes(b"seg0")
    seg1.write_bytes(b"seg1")

    with pytest.raises(TimelineAudioOverflowError, match="overlaps next"):
        _rebuild_tts_full_audio_from_segments(
            str(tmp_path),
            [
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
            ],
            variant="av",
        )
