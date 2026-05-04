"""appcore.audio_loudness 真实 ffmpeg 子进程测试（不 mock）。

用 ffmpeg lavfi sine source 生成不同响度的 wav 文件做基准比对，
确保 measure / normalize / mix 三个核心函数在真实 ffmpeg 6.x 上工作正常。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from appcore.audio_loudness import (
    EBUR128_FLOOR,
    SILENCE_LUFS_THRESHOLD,
    is_likely_silence,
    measure_integrated_lufs,
    mix_with_background,
    normalize_to_lufs,
)


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


pytestmark = pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg required")


def _gen_sine(out_path: Path, *, duration: float = 6.0,
              freq: int = 440, gain_db: float = -18.0) -> Path:
    """用 ffmpeg lavfi sine 生成指定响度的立体声 wav。"""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency={freq}:duration={duration}:sample_rate=44100",
        "-af", f"volume={gain_db}dB",
        "-ac", "2",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def _gen_silence(out_path: Path, *, duration: float = 5.0) -> Path:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100:duration={duration}",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def test_measure_quieter_signal_returns_lower_lufs(tmp_path):
    quiet = _gen_sine(tmp_path / "quiet.wav", gain_db=-30.0)
    loud = _gen_sine(tmp_path / "loud.wav", gain_db=-12.0)

    quiet_lufs = measure_integrated_lufs(str(quiet))
    loud_lufs = measure_integrated_lufs(str(loud))

    # 响度差应该 ≈ 18 dB（一些误差由 sine 短时长的 gating 引入，留 ±3 LU 容差）
    assert loud_lufs > quiet_lufs
    assert 14.0 <= (loud_lufs - quiet_lufs) <= 22.0


def test_measure_pure_silence_returns_floor(tmp_path):
    silence = _gen_silence(tmp_path / "silence.wav")
    lufs = measure_integrated_lufs(str(silence))
    # 纯 anullsrc，ebur128 应该返回 -inf → 折成 -70 LUFS
    assert lufs == EBUR128_FLOOR


def test_measure_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        measure_integrated_lufs(str(tmp_path / "does_not_exist.wav"))


def test_normalize_converges_within_3pct(tmp_path):
    """归一化后的偏差必须落在 ±3% 内（对应 -23 LUFS 时约 ±0.69 LU）。"""
    src = _gen_sine(tmp_path / "src.wav", gain_db=-30.0, duration=8.0)
    target_lufs = -23.0

    result = normalize_to_lufs(
        str(src), str(tmp_path / "out.wav"),
        target_lufs=target_lufs,
    )
    assert result.target_lufs == target_lufs
    assert abs(result.deviation_pct) <= 3.0, (
        f"loudnorm 二阶段未收敛到 ±3%: 实际偏差 {result.deviation_pct:.2f}% "
        f"(input={result.input_lufs:.2f}, output={result.output_lufs:.2f})"
    )
    assert result.converged is True
    assert os.path.isfile(result.output_path)


def test_normalize_to_target_actually_changes_loudness(tmp_path):
    """归一化必须把声音真的拉到目标附近。"""
    src = _gen_sine(tmp_path / "src.wav", gain_db=-9.0, duration=8.0)
    src_lufs = measure_integrated_lufs(str(src))

    result = normalize_to_lufs(
        str(src), str(tmp_path / "out.wav"),
        target_lufs=-23.0,
    )
    # 输出响度跟 -23 LUFS 应该比输入响度跟 -23 LUFS 更接近
    assert abs(result.output_lufs - (-23.0)) < abs(src_lufs - (-23.0))


def test_mix_with_background_outputs_file_with_expected_duration(tmp_path):
    main = _gen_sine(tmp_path / "main.wav", gain_db=-18.0, duration=4.0, freq=440)
    bg = _gen_sine(tmp_path / "bg.wav", gain_db=-18.0, duration=6.0, freq=220)

    out = mix_with_background(
        str(main), str(bg), str(tmp_path / "mix.wav"),
        background_volume=0.5, duration="longest",
    )
    assert os.path.isfile(out)
    # 用 ffprobe 拿时长
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", out],
        check=True, capture_output=True, text=True,
    )
    duration = float(proc.stdout.strip())
    # longest = 6s（容许 ffmpeg amix 的 dropout_transition 余量）
    assert 5.5 <= duration <= 6.5


def test_mix_with_background_first_duration_truncates_to_main(tmp_path):
    main = _gen_sine(tmp_path / "main.wav", gain_db=-18.0, duration=3.0)
    bg = _gen_sine(tmp_path / "bg.wav", gain_db=-18.0, duration=10.0)

    out = mix_with_background(
        str(main), str(bg), str(tmp_path / "mix.wav"),
        duration="first",
    )
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", out],
        check=True, capture_output=True, text=True,
    )
    duration = float(proc.stdout.strip())
    assert 2.8 <= duration <= 3.3


def test_mix_with_missing_main_raises(tmp_path):
    bg = _gen_silence(tmp_path / "bg.wav")
    with pytest.raises(FileNotFoundError):
        mix_with_background(
            str(tmp_path / "missing.wav"),
            str(bg),
            str(tmp_path / "out.wav"),
        )


def test_is_likely_silence_threshold():
    assert is_likely_silence(EBUR128_FLOOR) is True
    assert is_likely_silence(SILENCE_LUFS_THRESHOLD - 0.1) is True
    assert is_likely_silence(SILENCE_LUFS_THRESHOLD + 0.1) is False
    assert is_likely_silence(-23.0) is False
