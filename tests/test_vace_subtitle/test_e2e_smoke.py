"""Optional end-to-end smoke test against a real VACE install.

Skipped unless VACE_REPO_DIR + VACE_PYTHON_EXE + VACE_MODEL_DIR are all set
AND ``nvidia-smi`` is on PATH. CI without GPU stays green via skip.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REQUIRED_ENV = ("VACE_REPO_DIR", "VACE_PYTHON_EXE", "VACE_MODEL_DIR")


def _all_env_set() -> bool:
    return all(os.environ.get(k) for k in REQUIRED_ENV)


def _has_nvidia_smi() -> bool:
    return shutil.which("nvidia-smi") is not None


def _has_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None or os.environ.get("FFPROBE_PATH")


pytestmark = pytest.mark.skipif(
    not (_all_env_set() and _has_nvidia_smi() and _has_ffprobe()),
    reason="VACE env / nvidia-smi / ffprobe not available; skipping e2e smoke",
)


def test_dry_run_against_real_env(tmp_path):
    """Even when VACE is installed, run dry to confirm the wiring is sound.

    Real pipeline launches are NOT performed here — they take minutes to
    hours and consume GPU. Use a manual run for that.
    """
    from appcore.vace_subtitle.remover import VaceWindowsSubtitleRemover

    # Tiny synthetic input via ffmpeg — 1s of black 1280x720 @ 30fps.
    input_video = tmp_path / "smoke_in.mp4"
    cmd = [
        os.environ.get("FFMPEG_PATH", "ffmpeg"),
        "-y", "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=1:r=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(input_video),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=30)

    output = tmp_path / "smoke_out.mp4"
    remover = VaceWindowsSubtitleRemover()
    out = remover.remove_subtitles(
        input_video=input_video,
        output_video=output,
        dry_run=True,
    )
    assert Path(out) == output.resolve()
    manifest = Path(str(out) + ".vace.json")
    assert manifest.is_file()
