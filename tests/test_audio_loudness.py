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
    build_ducking_volume_expression,
    clean_electronic_background,
    is_likely_silence,
    measure_integrated_lufs,
    measure_window_lufs,
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


def test_mix_with_background_dynamic_ducking_restores_background_between_speech(tmp_path):
    main = _gen_silence(tmp_path / "main.wav", duration=10.0)
    bg = _gen_sine(tmp_path / "bg.wav", gain_db=-6.0, duration=10.0, freq=220)
    expression = build_ducking_volume_expression(
        segments=[{"start": 5.0, "end": 6.0}],
        background_volume=0.1,
        standard_volume=1.0,
        attack=0.0,
        release=0.0,
    )

    out = mix_with_background(
        str(main),
        str(bg),
        str(tmp_path / "ducked.wav"),
        background_volume=expression,
        duration="longest",
    )

    before_lufs = measure_window_lufs(out, 1.0, 2.0)
    speech_lufs = measure_window_lufs(out, 5.0, 6.0)
    after_lufs = measure_window_lufs(out, 8.0, 9.0)

    assert speech_lufs <= before_lufs - 15.0
    assert abs(after_lufs - before_lufs) <= 1.0


def test_clean_electronic_background_outputs_file_with_expected_duration(tmp_path):
    src = _gen_sine(tmp_path / "electric.wav", gain_db=-18.0, duration=4.0, freq=3200)

    out = clean_electronic_background(str(src), str(tmp_path / "clean.wav"))

    assert os.path.isfile(out)
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", out],
        check=True, capture_output=True, text=True,
    )
    duration = float(proc.stdout.strip())
    assert 3.8 <= duration <= 4.2


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


def test_build_ducking_volume_expression():
    # Case 1: No segments
    assert build_ducking_volume_expression(
        segments=[], background_volume=0.3, standard_volume=0.6,
    ) == "0.6000"

    # Case 2: background_volume >= standard_volume
    assert build_ducking_volume_expression(
        segments=[{"start": 1.0, "end": 2.0}],
        background_volume=0.6, standard_volume=0.6,
    ) == "0.6000"

    # Case 3: Standard single speech window [5.0, 10.0] -> [4.8, 10.4]
    expr = build_ducking_volume_expression(
        segments=[{"audio_start_time": 5.0, "audio_end_time": 10.0}],
        background_volume=0.2, standard_volume=0.8,
        attack=0.2, release=0.4,
    )
    assert "0.8000-0.6000*(" in expr
    assert "between(t,4.800,10.400)" in expr
    assert "lt(t,5.000)" in expr
    assert "gt(t,10.000)" in expr

    # Case 4: Overlapping windows merged
    # [1.0, 3.0] -> extended [0.8, 3.4]
    # [2.8, 4.0] -> extended [2.6, 4.4]
    # Merged -> [0.8, 4.4]
    expr_merge = build_ducking_volume_expression(
        segments=[
            {"start": 1.0, "end": 3.0},
            {"start": 2.8, "end": 4.0},
        ],
        background_volume=0.3, standard_volume=0.6,
        attack=0.2, release=0.4,
    )
    # L = 0.8 + 0.2 = 1.0
    # R = 4.4 - 0.4 = 4.0
    assert "between(t,0.800,4.400)" in expr_merge
    assert "lt(t,1.000)" in expr_merge
    assert "gt(t,4.000)" in expr_merge
    # verify only 1 term is generated (no addition plus sign)
    assert "+" not in expr_merge.split("*(")[1]

    # Case 5: Extremely short interval (clipped at 0) -> mid point crossover
    # segment [0.1, 0.3] -> duration=0.2 (>= 0.15s)
    # attack=0.4, release=0.6 -> extended [0.0, 0.9] -> duration = 0.9 (<= attack + release)
    expr_short = build_ducking_volume_expression(
        segments=[{"start": 0.1, "end": 0.3}],
        background_volume=0.3, standard_volume=0.6,
        attack=0.4, release=0.6,
    )
    # duration = 0.9. L = R = 0.0 + 0.9 * (0.4 / 1.0) = 0.36
    assert "between(t,0.000,0.900)" in expr_short
    assert "lt(t,0.360)" in expr_short
    assert "gt(t,0.360)" in expr_short

