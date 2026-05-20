"""audio_stitch 单元测试。

不依赖真实 ffmpeg 二进制，全部通过 monkeypatch subprocess.run 验证
ffmpeg 命令结构是否正确。
"""
from __future__ import annotations

from unittest.mock import patch

from pipeline.audio_stitch import (
    apply_asr_window_audio_schedule,
    apply_compact_audio_schedule,
    build_stitched_audio,
    build_timeline_manifest,
)


def test_build_timeline_manifest_entries_match_segments():
    segs = [
        {"shot_start": 0.0, "shot_duration": 5.0, "audio_path": "/a.mp3"},
        {"shot_start": 5.0, "shot_duration": 3.0, "audio_path": "/b.mp3"},
    ]
    manifest = build_timeline_manifest(segs)
    entries = manifest["entries"]
    assert len(entries) == 2
    assert entries[0]["start"] == 0.0
    assert entries[0]["end"] == 5.0
    assert entries[1]["start"] == 5.0
    assert entries[1]["end"] == 8.0


def test_apply_compact_audio_schedule_caps_large_source_gap():
    scheduled = apply_compact_audio_schedule(
        [
            {
                "asr_index": 0,
                "start_time": 0.179,
                "end_time": 4.159,
                "tts_duration": 2.926,
                "text": "Hook",
            },
            {
                "asr_index": 1,
                "start_time": 4.319,
                "end_time": 8.679,
                "tts_duration": 3.657,
                "text": "Second",
            },
            {
                "asr_index": 2,
                "start_time": 11.9,
                "end_time": 14.0,
                "tts_duration": 1.8,
                "text": "Collapsed gap",
            },
        ],
        max_gap=0.25,
    )

    assert scheduled[0]["audio_start_time"] == 0.0
    assert scheduled[0]["audio_end_time"] == 2.926
    assert scheduled[0]["source_start_time"] == 0.179
    assert scheduled[1]["source_gap_before"] == 0.16
    assert scheduled[1]["audio_gap_before"] == 0.16
    assert scheduled[1]["compact_gap_applied"] is False
    assert scheduled[1]["audio_start_time"] == 3.086
    assert scheduled[2]["source_gap_before"] == 3.221
    assert scheduled[2]["audio_gap_before"] == 0.25
    assert scheduled[2]["compact_gap_applied"] is True
    assert scheduled[2]["audio_start_time"] == 6.993
    assert scheduled[2]["timeline_mode"] == "compact_asr_primary"


def test_apply_asr_window_audio_schedule_preserves_initial_no_asr_gap():
    scheduled = apply_asr_window_audio_schedule(
        [
            {
                "asr_index": 0,
                "start_time": 13.979,
                "end_time": 15.779,
                "tts_duration": 1.75,
                "text": "First speech",
            },
            {
                "asr_index": 1,
                "start_time": 16.399,
                "end_time": 19.92,
                "tts_duration": 3.422,
                "text": "Second speech",
            },
        ],
        max_gap=0.25,
        preserve_gap_threshold=1.0,
    )

    assert scheduled[0]["source_gap_before"] == 13.979
    assert scheduled[0]["audio_gap_before"] == 13.979
    assert scheduled[0]["audio_start_time"] == 13.979
    assert scheduled[0]["audio_end_time"] == 15.729
    assert scheduled[0]["asr_window_gap_preserved"] is True
    assert scheduled[0]["compact_gap_applied"] is False
    assert scheduled[0]["timeline_mode"] == "asr_window_primary"

    assert scheduled[1]["source_gap_before"] == 0.62
    assert scheduled[1]["audio_gap_before"] == 0.25
    assert scheduled[1]["audio_start_time"] == 15.979
    assert scheduled[1]["asr_window_gap_preserved"] is False
    assert scheduled[1]["compact_gap_applied"] is True


def test_apply_asr_window_audio_schedule_preserves_large_middle_gap_and_compacts_short_gap():
    scheduled = apply_asr_window_audio_schedule(
        [
            {
                "asr_index": 0,
                "start_time": 0.12,
                "end_time": 1.8,
                "tts_duration": 1.6,
                "text": "Hook",
            },
            {
                "asr_index": 1,
                "start_time": 2.05,
                "end_time": 3.0,
                "tts_duration": 0.9,
                "text": "Short gap",
            },
            {
                "asr_index": 2,
                "start_time": 8.25,
                "end_time": 10.0,
                "tts_duration": 1.7,
                "text": "After music",
            },
        ],
        max_gap=0.25,
        preserve_gap_threshold=1.0,
    )

    assert scheduled[0]["audio_start_time"] == 0.0
    assert scheduled[0]["source_gap_before"] == 0.12
    assert scheduled[0]["audio_gap_before"] == 0.0
    assert scheduled[0]["asr_window_gap_preserved"] is False

    assert scheduled[1]["source_gap_before"] == 0.25
    assert scheduled[1]["audio_gap_before"] == 0.25
    assert scheduled[1]["audio_start_time"] == 1.85
    assert scheduled[1]["compact_gap_applied"] is False

    assert scheduled[2]["source_gap_before"] == 5.25
    assert scheduled[2]["audio_gap_before"] == 5.25
    assert scheduled[2]["audio_start_time"] == 8.0
    assert scheduled[2]["asr_window_gap_preserved"] is True
    assert scheduled[2]["compact_gap_applied"] is False


def test_build_timeline_manifest_provides_segments_and_video_ranges():
    """compose_video 依赖 manifest['segments'][*]['video_ranges']。"""
    segs = [
        {"shot_start": 0.0, "shot_duration": 4.0, "audio_path": "/a.mp3"},
        {"shot_start": 4.0, "shot_duration": 2.5, "audio_path": "/b.mp3"},
    ]
    manifest = build_timeline_manifest(segs)
    assert "segments" in manifest
    assert len(manifest["segments"]) == 2
    vr0 = manifest["segments"][0]["video_ranges"]
    assert vr0 == [{"start": 0.0, "end": 4.0}]
    vr1 = manifest["segments"][1]["video_ranges"]
    assert vr1 == [{"start": 4.0, "end": 6.5}]
    assert manifest["total_tts_duration"] == 6.5
    assert manifest["video_consumed_duration"] == 6.5


def test_build_stitched_audio_builds_ffmpeg_command(tmp_path):
    segs = [
        {"shot_start": 0.0, "shot_duration": 2.0,
         "audio_path": str(tmp_path / "a.mp3")},
        {"shot_start": 2.0, "shot_duration": 3.0,
         "audio_path": str(tmp_path / "b.mp3")},
    ]
    output = tmp_path / "out.mp3"
    captured: dict = {}

    def fake_run(cmd, check, capture_output):
        captured["cmd"] = cmd

    with patch("pipeline.audio_stitch.subprocess.run",
               side_effect=fake_run):
        build_stitched_audio(segs, total_duration=5.0,
                             output_path=str(output))

    cmd = captured["cmd"]
    assert cmd[0] == "ffmpeg"
    assert "-filter_complex" in cmd
    filter_idx = cmd.index("-filter_complex") + 1
    assert "adelay=0|0" in cmd[filter_idx]
    assert "adelay=2000|2000" in cmd[filter_idx]
    assert "amix=inputs=2" in cmd[filter_idx]
    # 输出时长被裁到 total_duration
    t_idx = cmd.index("-t") + 1
    assert cmd[t_idx].startswith("5")


def test_build_stitched_audio_raises_on_empty_segments(tmp_path):
    try:
        build_stitched_audio([], total_duration=5.0,
                             output_path=str(tmp_path / "o.mp3"))
    except ValueError:
        return
    assert False, "应该抛 ValueError"
