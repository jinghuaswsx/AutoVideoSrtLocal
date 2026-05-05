"""ffmpeg_io tests — focused on parsing and command shape, not real ffmpeg runs."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from appcore.vace_subtitle.ffmpeg_io import (
    FFmpegError,
    MediaInfo,
    _parse_media_info,
    _parse_rate,
    concat_chunks,
    crop_chunk,
    cut_chunk,
    mux_audio_from_source,
    probe_media,
)


# ---------------------------------------------------------------------------
# _parse_rate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("30/1", 30.0),
    ("30000/1001", pytest.approx(29.97, abs=0.01)),
    ("0/0", 0.0),
    ("", 0.0),
    ("60", 60.0),
])
def test_parse_rate(raw, expected):
    assert _parse_rate(raw) == expected


# ---------------------------------------------------------------------------
# _parse_media_info
# ---------------------------------------------------------------------------

def test_parse_media_info_full():
    data = {
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080,
             "avg_frame_rate": "30000/1001", "duration": "12.3", "nb_frames": "370"},
            {"codec_type": "audio"},
        ],
        "format": {"duration": "12.5"},
    }
    info = _parse_media_info(data)
    assert info.width == 1920
    assert info.height == 1080
    assert info.fps == pytest.approx(29.97, abs=0.01)
    assert info.has_audio is True
    assert info.duration == pytest.approx(12.3, abs=0.01)
    assert info.nb_frames == 370
    assert info.resolution == "1920x1080"


def test_parse_media_info_no_audio():
    data = {
        "streams": [{"codec_type": "video", "width": 1280, "height": 720,
                     "r_frame_rate": "30/1"}],
        "format": {"duration": "5.0"},
    }
    info = _parse_media_info(data)
    assert info.has_audio is False
    assert info.fps == 30.0


def test_parse_media_info_garbage_returns_zeros():
    info = _parse_media_info({"streams": [], "format": {}})
    assert info.width == 0 and info.height == 0
    assert info.duration == 0.0


# ---------------------------------------------------------------------------
# probe_media — mock subprocess
# ---------------------------------------------------------------------------

def _fake_probe_proc(stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


def test_probe_media_invokes_ffprobe(tmp_path):
    fake_video = tmp_path / "x.mp4"
    fake_video.write_bytes(b"\x00\x00")
    payload = json.dumps({
        "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                     "avg_frame_rate": "30/1", "duration": "1.0"}],
        "format": {"duration": "1.0"},
    })
    with patch("appcore.vace_subtitle.ffmpeg_io.subprocess.run") as run:
        run.return_value = _fake_probe_proc(payload)
        info = probe_media(fake_video, ffprobe_path="ffprobe")
    assert info.width == 1920 and info.height == 1080
    cmd = run.call_args.args[0]
    assert isinstance(cmd, list)
    assert cmd[0] == "ffprobe"
    assert "-show_streams" in cmd
    assert str(fake_video) in cmd


def test_probe_media_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        probe_media(tmp_path / "nope.mp4")


def test_probe_media_nonzero_raises(tmp_path):
    fake_video = tmp_path / "x.mp4"
    fake_video.write_bytes(b"\x00")
    with patch("appcore.vace_subtitle.ffmpeg_io.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="boom"
        )
        with pytest.raises(FFmpegError) as excinfo:
            probe_media(fake_video)
    assert excinfo.value.returncode == 1
    assert "boom" in (excinfo.value.stderr_tail or "")


# ---------------------------------------------------------------------------
# command shapes (cut_chunk / crop_chunk / concat_chunks / mux_audio)
# ---------------------------------------------------------------------------

def _ok_run(*_a, **_k):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_cut_chunk_command_shape(tmp_path):
    src = tmp_path / "in.mp4"; src.write_bytes(b"x")
    dst = tmp_path / "out.mp4"
    with patch("appcore.vace_subtitle.ffmpeg_io.subprocess.run", side_effect=_ok_run) as run:
        cut_chunk(src=src, dst=dst, start_seconds=1.5, duration_seconds=3.0)
    cmd = run.call_args.args[0]
    assert isinstance(cmd, list)
    assert "shell" not in run.call_args.kwargs or run.call_args.kwargs.get("shell") is False
    assert cmd[0] == "ffmpeg"
    assert "-ss" in cmd and "1.500" in cmd
    assert "-t" in cmd and "3.000" in cmd
    assert str(dst) == cmd[-1]


def test_crop_chunk_filter_string(tmp_path):
    src = tmp_path / "in.mp4"; src.write_bytes(b"x")
    dst = tmp_path / "out.mp4"
    with patch("appcore.vace_subtitle.ffmpeg_io.subprocess.run", side_effect=_ok_run) as run:
        crop_chunk(
            src=src, dst=dst,
            crop_x=0, crop_y=650, crop_w=1920, crop_h=424,
            target_w=832, target_h=192, pad_left=0, pad_top=0,
        )
    cmd = run.call_args.args[0]
    vf_idx = cmd.index("-vf")
    vf = cmd[vf_idx + 1]
    assert "crop=1920:424:0:650" in vf
    assert "scale=" in vf
    assert "pad=832:192:0:0" in vf


def test_concat_chunks_writes_list_file(tmp_path):
    p1 = tmp_path / "a.mp4"; p1.write_bytes(b"a")
    p2 = tmp_path / "b.mp4"; p2.write_bytes(b"b")
    dst = tmp_path / "out.mp4"
    list_file = tmp_path / "list.txt"
    with patch("appcore.vace_subtitle.ffmpeg_io.subprocess.run", side_effect=_ok_run) as run:
        concat_chunks(chunk_paths=[p1, p2], dst=dst, list_file_path=list_file)
    text = list_file.read_text(encoding="utf-8")
    assert "a.mp4" in text and "b.mp4" in text
    cmd = run.call_args.args[0]
    assert "-f" in cmd and "concat" in cmd


def test_mux_audio_command_uses_optional_audio(tmp_path):
    v = tmp_path / "v.mp4"; v.write_bytes(b"v")
    a = tmp_path / "a.mp4"; a.write_bytes(b"a")
    dst = tmp_path / "out.mp4"
    with patch("appcore.vace_subtitle.ffmpeg_io.subprocess.run", side_effect=_ok_run) as run:
        mux_audio_from_source(video_src=v, audio_src=a, dst=dst)
    cmd = run.call_args.args[0]
    # '?' on audio map = optional; tolerate missing audio in source
    map_idx = cmd.index("-map")
    audio_map_idx = cmd.index("1:a:0?")
    assert audio_map_idx > map_idx
