"""End-to-end dry-run test: VaceWindowsSubtitleRemover wires modules together.

Real ffmpeg / ffprobe / VACE are NOT invoked; we mock probe_media to avoid
needing ffprobe on PATH and the dry_run flag short-circuits the rest.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from appcore.vace_subtitle.ffmpeg_io import MediaInfo
from appcore.vace_subtitle.manifest import Manifest, manifest_path_for
from appcore.vace_subtitle.remover import VaceWindowsSubtitleRemover


def _stub_info(width=1920, height=1080, fps=30.0, duration=8.0):
    return MediaInfo(width=width, height=height, fps=fps, duration=duration,
                     has_audio=True, nb_frames=int(duration * fps))


def test_dry_run_writes_manifest(tmp_path, monkeypatch):
    fake_in = tmp_path / "in.mp4"
    fake_in.write_bytes(b"\x00")
    fake_out = tmp_path / "out.mp4"

    # Avoid env validation (no real VACE).
    monkeypatch.delenv("VACE_REPO_DIR", raising=False)
    monkeypatch.delenv("VACE_PYTHON_EXE", raising=False)
    monkeypatch.delenv("VACE_MODEL_DIR", raising=False)

    remover = VaceWindowsSubtitleRemover(profile="rtx3060_safe")
    with patch("appcore.vace_subtitle.remover.probe_media",
               return_value=_stub_info()):
        out = remover.remove_subtitles(
            input_video=fake_in,
            output_video=fake_out,
            bbox=(0, 780, 1920, 1025),
            dry_run=True,
        )

    assert out == fake_out.resolve()
    manifest_file = manifest_path_for(out)
    assert manifest_file.is_file()
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert data["mode"] == "roi_1080"
    assert data["profile"] == "rtx3060_safe"
    assert data["bbox_original"] == [0, 780, 1920, 1025]
    assert data["crop_bbox_original"][0] == 0
    assert data["crop_bbox_original"][2] == 1920
    assert data["status"] == "dry-run"
    assert len(data["chunks"]) >= 1


def test_native_vace_disabled_unless_opted_in(tmp_path, monkeypatch):
    fake_in = tmp_path / "in.mp4"; fake_in.write_bytes(b"\x00")
    fake_out = tmp_path / "out.mp4"
    monkeypatch.delenv("VACE_REPO_DIR", raising=False)
    monkeypatch.delenv("VACE_PYTHON_EXE", raising=False)
    monkeypatch.delenv("VACE_MODEL_DIR", raising=False)

    remover = VaceWindowsSubtitleRemover()
    import pytest
    with pytest.raises(ValueError, match="native_vace"):
        remover.remove_subtitles(
            input_video=fake_in, output_video=fake_out,
            mode="native_vace", dry_run=True,
        )


def test_proxy_720_not_implemented(tmp_path, monkeypatch):
    fake_in = tmp_path / "in.mp4"; fake_in.write_bytes(b"\x00")
    fake_out = tmp_path / "out.mp4"
    monkeypatch.delenv("VACE_REPO_DIR", raising=False)
    remover = VaceWindowsSubtitleRemover()
    import pytest
    with pytest.raises(NotImplementedError):
        remover.remove_subtitles(
            input_video=fake_in, output_video=fake_out,
            mode="proxy_720", dry_run=True,
        )


def test_input_missing_raises(tmp_path):
    remover = VaceWindowsSubtitleRemover()
    import pytest
    with pytest.raises(FileNotFoundError):
        remover.remove_subtitles(
            input_video=tmp_path / "nope.mp4",
            output_video=tmp_path / "x.mp4",
            dry_run=True,
        )
