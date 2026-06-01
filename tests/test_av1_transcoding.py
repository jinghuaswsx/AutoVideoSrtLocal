import os
import shutil
import subprocess
import pytest
from pipeline.ffutil import probe_media_info, ensure_h264_video

def test_probe_media_info_codec(monkeypatch):
    class FakeCompletedProcess:
        returncode = 0
        stdout = '{"streams": [{"width": 720, "height": 1280, "codec_name": "av1"}], "format": {"duration": "15.0"}}'
        stderr = ''

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())

    info = probe_media_info("dummy_path.mp4")
    assert info["width"] == 720
    assert info["height"] == 1280
    assert info["resolution"] == "720x1280"
    assert info["duration"] == 15.0
    assert info["video_codec"] == "av1"

def test_ensure_h264_video_already_h264(monkeypatch, tmp_path):
    # Simulate video that is already h264
    def fake_probe(path):
        return {
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 15.0,
            "video_codec": "h264"
        }
    monkeypatch.setattr("pipeline.ffutil.probe_media_info", fake_probe)

    copied_called = []
    def fake_copy(src, dst):
        copied_called.append((src, dst))
        with open(dst, "w") as f:
            f.write("copied")

    monkeypatch.setattr(shutil, "copyfile", fake_copy)

    input_file = tmp_path / "input.mp4"
    output_file = tmp_path / "output.mp4"
    input_file.write_text("source")

    res = ensure_h264_video(str(input_file), str(output_file))
    assert res is True
    assert len(copied_called) == 1
    assert copied_called[0] == (str(input_file), str(output_file))

def test_ensure_h264_video_transcodes_non_h264(monkeypatch, tmp_path):
    # Simulate av1 video
    def fake_probe(path):
        return {
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 15.0,
            "video_codec": "av1"
        }
    monkeypatch.setattr("pipeline.ffutil.probe_media_info", fake_probe)

    ffmpeg_commands = []
    class FakeFFmpegProcess:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffmpeg":
            ffmpeg_commands.append(cmd)
            # Create output file
            out_path = cmd[-1]
            with open(out_path, "w") as f:
                f.write("transcoded")
            return FakeFFmpegProcess()
        raise RuntimeError("Unexpected command: " + str(cmd))

    monkeypatch.setattr(subprocess, "run", fake_run)

    input_file = tmp_path / "input.mp4"
    output_file = tmp_path / "output.mp4"
    input_file.write_text("source")

    res = ensure_h264_video(str(input_file), str(output_file))
    assert res is True
    assert len(ffmpeg_commands) == 1
    assert "libx264" in ffmpeg_commands[0]
