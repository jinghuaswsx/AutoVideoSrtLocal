"""audio_stitch 单元测试。

不依赖真实 ffmpeg 二进制，全部通过 monkeypatch subprocess.run 验证
ffmpeg 命令结构是否正确。
"""
from __future__ import annotations

from unittest.mock import patch

from pipeline.audio_stitch import build_stitched_audio, build_timeline_manifest


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
