"""VACE pipeline command construction tests (no subprocess actually launched)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from appcore.vace_subtitle.config import VaceEnv, get_profile
from appcore.vace_subtitle.vace_subprocess import (
    VaceSubprocessError,
    build_command,
    is_oom,
    run_invocation,
)


@pytest.fixture
def env_ok(tmp_path):
    repo = tmp_path / "VACE"
    (repo / "vace").mkdir(parents=True)
    (repo / "vace" / "vace_pipeline.py").write_text("# stub\n", encoding="utf-8")
    py = tmp_path / ".venv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")
    model_dir = tmp_path / "Wan2.1-VACE-1.3B"
    model_dir.mkdir()
    return VaceEnv(
        repo_dir=repo,
        python_exe=py,
        model_dir=model_dir,
        results_dir=None,
        timeout_sec=30,
        ffmpeg_path="ffmpeg",
        ffprobe_path="ffprobe",
    )


def test_build_command_shape(env_ok, tmp_path):
    profile = get_profile("rtx3060_safe")
    inv = build_command(
        env_ok, profile,
        input_video=tmp_path / "in.mp4",
        bbox_in_vace=(0, 100, 832, 200),
        prompt="clean natural video background",
        save_dir=tmp_path / "out",
        save_file=tmp_path / "out" / "result.mp4",
        pre_save_dir=tmp_path / "pre",
    )
    cmd = inv.command
    assert isinstance(cmd, list)
    assert all(isinstance(c, str) for c in cmd)
    assert cmd[0] == str(env_ok.python_exe)
    assert cmd[1].endswith("vace_pipeline.py")

    # Required canonical args
    for flag in [
        "--base", "wan",
        "--task", "inpainting",
        "--mode", "bbox",
        "--bbox", "0,100,832,200",
        "--video", str(tmp_path / "in.mp4"),
        "--prompt", "clean natural video background",
        "--model_name", profile.model_name,
        "--ckpt_dir", str(env_ok.model_dir),
        "--size", profile.size,
        "--frame_num", str(profile.frame_num),
        "--sample_steps", str(profile.sample_steps),
        "--save_dir", str(tmp_path / "out"),
        "--save_file", str(tmp_path / "out" / "result.mp4"),
        "--pre_save_dir", str(tmp_path / "pre"),
    ]:
        assert flag in cmd, f"missing argument {flag!r}"

    # offload_model True / t5_cpu flag (rtx3060_safe sets both)
    om_idx = cmd.index("--offload_model")
    assert cmd[om_idx + 1] == "True"
    assert "--t5_cpu" in cmd


def test_build_command_cwd_is_repo(env_ok, tmp_path):
    profile = get_profile("rtx3060_safe")
    inv = build_command(
        env_ok, profile,
        input_video=tmp_path / "in.mp4",
        bbox_in_vace=(0, 0, 100, 100),
        prompt="x",
        save_dir=tmp_path / "out",
        save_file=tmp_path / "out" / "r.mp4",
        pre_save_dir=tmp_path / "pre",
    )
    assert inv.cwd == env_ok.repo_dir


def test_build_command_handles_paths_with_spaces(tmp_path):
    repo = tmp_path / "VACE folder with spaces"
    (repo / "vace").mkdir(parents=True)
    (repo / "vace" / "vace_pipeline.py").write_text("", encoding="utf-8")
    py = tmp_path / "venv space" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")
    model = tmp_path / "model dir"
    model.mkdir()
    env = VaceEnv(repo_dir=repo, python_exe=py, model_dir=model,
                  results_dir=None, timeout_sec=30,
                  ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
    profile = get_profile("rtx3060_safe")
    inv = build_command(
        env, profile,
        input_video=tmp_path / "input file.mp4",
        bbox_in_vace=(0, 0, 100, 100),
        prompt="x",
        save_dir=tmp_path / "out folder",
        save_file=tmp_path / "out folder" / "r.mp4",
        pre_save_dir=tmp_path / "pre dir",
    )
    # No quoting / shell escaping should appear; list[str] is the contract.
    for arg in inv.command:
        assert isinstance(arg, str)
        assert not arg.startswith('"')


def test_run_invocation_uses_no_shell(env_ok, tmp_path):
    profile = get_profile("rtx3060_safe")
    inv = build_command(
        env_ok, profile,
        input_video=tmp_path / "in.mp4",
        bbox_in_vace=(0, 0, 100, 100),
        prompt="x",
        save_dir=tmp_path / "out",
        save_file=tmp_path / "out" / "r.mp4",
        pre_save_dir=tmp_path / "pre",
    )
    runner = MagicMock(return_value=subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""))
    run_invocation(inv, timeout_sec=30, runner=runner)
    kwargs = runner.call_args.kwargs
    # Must NOT use shell=True; must be passed list[str].
    assert kwargs.get("shell") in (None, False)
    assert isinstance(runner.call_args.args[0], list)


def test_run_invocation_oom_detection(env_ok, tmp_path):
    profile = get_profile("rtx3060_safe")
    inv = build_command(
        env_ok, profile,
        input_video=tmp_path / "in.mp4",
        bbox_in_vace=(0, 0, 100, 100),
        prompt="x",
        save_dir=tmp_path / "out",
        save_file=tmp_path / "out" / "r.mp4",
        pre_save_dir=tmp_path / "pre",
    )
    bad = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="",
        stderr="RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB",
    )
    runner = MagicMock(return_value=bad)
    with pytest.raises(VaceSubprocessError) as excinfo:
        run_invocation(inv, timeout_sec=30, runner=runner)
    assert excinfo.value.oom is True
    assert excinfo.value.returncode == 1


def test_run_invocation_picks_save_file_when_present(env_ok, tmp_path):
    profile = get_profile("rtx3060_safe")
    save_dir = tmp_path / "out"
    save_dir.mkdir()
    save_file = save_dir / "r.mp4"
    save_file.write_bytes(b"\x00")
    inv = build_command(
        env_ok, profile,
        input_video=tmp_path / "in.mp4",
        bbox_in_vace=(0, 0, 100, 100),
        prompt="x",
        save_dir=save_dir, save_file=save_file,
        pre_save_dir=tmp_path / "pre",
    )
    runner = MagicMock(return_value=subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""))
    result = run_invocation(inv, timeout_sec=30, runner=runner)
    assert result.output_video == save_file


def test_run_invocation_falls_back_to_newest_in_dir(env_ok, tmp_path):
    """When save_file isn't produced, pick newest .mp4 in save_dir."""
    profile = get_profile("rtx3060_safe")
    save_dir = tmp_path / "out"
    save_dir.mkdir()
    older = save_dir / "older.mp4"
    older.write_bytes(b"x")
    newer = save_dir / "newer.mp4"
    newer.write_bytes(b"x")
    # Force mtime ordering so the test is deterministic.
    import os
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    inv = build_command(
        env_ok, profile,
        input_video=tmp_path / "in.mp4",
        bbox_in_vace=(0, 0, 100, 100),
        prompt="x",
        save_dir=save_dir, save_file=save_dir / "missing.mp4",
        pre_save_dir=tmp_path / "pre",
    )
    runner = MagicMock(return_value=subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""))
    result = run_invocation(inv, timeout_sec=30, runner=runner)
    assert result.output_video == newer


@pytest.mark.parametrize("text,expected", [
    ("CUDA out of memory. Tried to allocate", True),
    ("Out of memory while alloc", True),
    ("CUBLAS_STATUS_ALLOC_FAILED at line 42", True),
    ("CUDNN_STATUS_NOT_SUPPORTED", True),
    ("RuntimeError: tensor shape mismatch", False),
    ("", False),
])
def test_is_oom_patterns(text, expected):
    assert is_oom(text) is expected
