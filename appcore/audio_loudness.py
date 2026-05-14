"""音频响度测量与归一化（基于 ffmpeg EBU R128）。

为多语种 / 全能视频翻译的"原人声响度"基准对齐设计：

- :func:`measure_integrated_lufs` 用 ``ebur128`` filter 测一段音频的
  integrated loudness（自带 -70/-10 LU 双重门控，自动忽略静音段，
  返回的就是"开口说话时的平均感知响度"）。
- :func:`normalize_to_lufs` 用 ``loudnorm`` filter 二阶段归一化把
  TTS 主轨对齐到测得的人声基准 L₀。
- :func:`mix_with_background` 用 ``amix`` 把归一化后的 TTS 与原视频
  人声分离出的伴奏轨道并行合成。

所有函数都是 ffmpeg 子进程包装，不引新 Python 依赖。
"""
from __future__ import annotations

import json
import logging
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_TRUE_PEAK = -1.0
DEFAULT_LRA = 11.0
SILENCE_LUFS_THRESHOLD = -50.0
EBUR128_FLOOR = -70.0
LOUDNESS_PROFILE_STANDARD = "standard"
LOUDNESS_PROFILE_AUTO_BOOST = "bg_boost"
LOUDNESS_PROFILE_MANUAL_BOOST = "manual_boost"
LOUDNESS_PROFILES = {
    LOUDNESS_PROFILE_STANDARD,
    LOUDNESS_PROFILE_AUTO_BOOST,
    LOUDNESS_PROFILE_MANUAL_BOOST,
}

BOOST_TARGET_GAP_LU = 10.0
BOOST_MAX_BACKGROUND_VOLUME = 1.8
DEFAULT_MANUAL_BOOST_PCT = 50


@dataclass
class LoudnessNormalizationResult:
    """归一化操作的结果数据。"""

    input_lufs: float
    target_lufs: float
    output_lufs: float
    deviation_lu: float
    deviation_pct: float
    output_path: str
    converged: bool


_INTEGRATED_LOUDNESS_RE = re.compile(
    r"Integrated loudness:.*?I:\s*(-?\d+(?:\.\d+)?|-inf)\s*LUFS",
    re.DOTALL,
)


def validate_loudness_profile(
    profile: str | None,
    manual_boost_pct: int | None = None,
) -> tuple[str, int | None]:
    """Normalize and validate a loudness profile selection."""
    normalized_profile = profile or LOUDNESS_PROFILE_STANDARD
    if normalized_profile not in LOUDNESS_PROFILES:
        raise ValueError(f"unsupported loudness profile: {normalized_profile}")

    if normalized_profile != LOUDNESS_PROFILE_MANUAL_BOOST:
        return normalized_profile, None

    if (
        isinstance(manual_boost_pct, bool)
        or not isinstance(manual_boost_pct, int)
        or manual_boost_pct < 10
        or manual_boost_pct > 100
        or manual_boost_pct % 10 != 0
    ):
        raise ValueError(
            "manual_boost_pct must be an integer multiple of 10 from 10 to 100"
        )
    return normalized_profile, manual_boost_pct


def _empty_boost_summary() -> dict:
    return {
        "background_boost": {
            "enabled": False,
            "target_gap_lu": BOOST_TARGET_GAP_LU,
            "capped": False,
        },
        "manual_boost": {
            "enabled": False,
            "capped": False,
        },
    }


def _is_available_lufs(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value))


def resolve_background_volume_profile(
    profile: str | None,
    *,
    standard_volume: float,
    accompaniment_lufs: float | None = None,
    tts_reference_lufs: float | None = None,
    manual_boost_pct: int | None = None,
) -> dict:
    """Resolve a loudness profile into the background volume used for mixing."""
    normalized_profile, validated_pct = validate_loudness_profile(
        profile, manual_boost_pct
    )
    standard_volume = float(standard_volume)
    result = {
        "profile": normalized_profile,
        "manual_boost_pct": validated_pct,
        "background_volume": standard_volume,
        "effective_background_volume": standard_volume,
        **_empty_boost_summary(),
    }

    if normalized_profile == LOUDNESS_PROFILE_STANDARD:
        return result

    if normalized_profile == LOUDNESS_PROFILE_MANUAL_BOOST:
        raw_volume = standard_volume * (1 + validated_pct / 100)
        effective_volume = min(BOOST_MAX_BACKGROUND_VOLUME, raw_volume)
        result["effective_background_volume"] = effective_volume
        result["manual_boost"] = {
            "enabled": True,
            "boost_pct": validated_pct,
            "standard_volume": standard_volume,
            "raw_volume": raw_volume,
            "effective_volume": effective_volume,
            "max_volume": BOOST_MAX_BACKGROUND_VOLUME,
            "capped": effective_volume < raw_volume,
        }
        return result

    if not _is_available_lufs(accompaniment_lufs):
        result["background_boost"]["fallback_reason"] = "accompaniment_lufs_unavailable"
        return result
    if float(accompaniment_lufs) < SILENCE_LUFS_THRESHOLD:
        result["background_boost"]["fallback_reason"] = "accompaniment_near_silence"
        return result
    if not _is_available_lufs(tts_reference_lufs):
        result["background_boost"]["fallback_reason"] = "tts_reference_lufs_unavailable"
        return result

    accompaniment_lufs = float(accompaniment_lufs)
    tts_reference_lufs = float(tts_reference_lufs)
    target_bg_lufs = tts_reference_lufs - BOOST_TARGET_GAP_LU
    needed_gain_lu = target_bg_lufs - accompaniment_lufs
    raw_volume = standard_volume * (10 ** (needed_gain_lu / 20))
    uncapped_volume = max(standard_volume, raw_volume)
    effective_volume = min(BOOST_MAX_BACKGROUND_VOLUME, uncapped_volume)
    result["effective_background_volume"] = effective_volume
    result["background_boost"] = {
        "enabled": True,
        "target_gap_lu": BOOST_TARGET_GAP_LU,
        "standard_volume": standard_volume,
        "max_volume": BOOST_MAX_BACKGROUND_VOLUME,
        "accompaniment_lufs": accompaniment_lufs,
        "tts_reference_lufs": tts_reference_lufs,
        "fallback_reason": None,
        "target_background_lufs": target_bg_lufs,
        "needed_gain_lu": needed_gain_lu,
        "raw_volume": raw_volume,
        "effective_volume": effective_volume,
        "capped": effective_volume < uncapped_volume,
    }
    return result


def measure_integrated_lufs(audio_path: str) -> float:
    """返回 audio_path 的 integrated loudness（LUFS）。

    ebur128 在几乎全静音时会输出 ``-inf``，本函数把它折成
    :data:`EBUR128_FLOOR` (-70 LUFS)，便于上层做"分离失败"判定。
    """
    p = Path(audio_path)
    if not p.is_file():
        raise FileNotFoundError(f"audio not found: {audio_path}")

    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(p),
        "-af", "ebur128=peak=true",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg ebur128 failed (rc={proc.returncode}): {proc.stderr[-500:]}"
        )

    match = _INTEGRATED_LOUDNESS_RE.search(proc.stderr)
    if not match:
        raise RuntimeError(
            f"failed to parse integrated loudness: {proc.stderr[-500:]}"
        )

    raw = match.group(1)
    if raw == "-inf":
        return EBUR128_FLOOR
    return float(raw)


_LOUDNORM_JSON_RE = re.compile(
    r'\{\s*"input_i".*?"target_offset"\s*:\s*"[^"]*"\s*\}',
    re.DOTALL,
)


def _parse_loudnorm_json(stderr: str) -> dict:
    """ffmpeg loudnorm 一阶段在 stderr 末尾输出 JSON 测量数据。"""
    matches = _LOUDNORM_JSON_RE.findall(stderr)
    if not matches:
        raise RuntimeError(
            f"loudnorm pass-1 missing JSON output: {stderr[-500:]}"
        )
    return json.loads(matches[-1])


def normalize_to_lufs(
    input_path: str,
    output_path: str,
    *,
    target_lufs: float,
    target_tp: float = DEFAULT_TRUE_PEAK,
    target_lra: float = DEFAULT_LRA,
    sample_rate: int = 44100,
    channels: int = 2,
    convergence_pct: float = 3.0,
) -> LoudnessNormalizationResult:
    """二阶段 loudnorm 把 input 归一到 target_lufs ±convergence_pct%。

    第二阶段使用一阶段测得的 measured_* 参数和 ``linear=true``，精度
    一般可达 ±0.5 LU（对应 LUFS 数值约 ±2.2%，可在 ±3% 内收敛）。
    """
    in_p = Path(input_path)
    out_p = Path(output_path)
    if not in_p.is_file():
        raise FileNotFoundError(f"input not found: {input_path}")
    out_p.parent.mkdir(parents=True, exist_ok=True)

    measure_filter = (
        f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}"
        f":print_format=json"
    )
    measure_cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(in_p),
        "-af", measure_filter,
        "-f", "null", "-",
    ]
    proc = subprocess.run(measure_cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"loudnorm pass-1 failed (rc={proc.returncode}): {proc.stderr[-500:]}"
        )
    m = _parse_loudnorm_json(proc.stderr)

    apply_filter = (
        f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}"
        f":measured_I={m['input_i']}"
        f":measured_LRA={m['input_lra']}"
        f":measured_TP={m['input_tp']}"
        f":measured_thresh={m['input_thresh']}"
        f":offset={m['target_offset']}"
        f":linear=true:print_format=summary"
    )
    apply_cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-y",
        "-i", str(in_p),
        "-af", apply_filter,
        "-ar", str(sample_rate), "-ac", str(channels),
        str(out_p),
    ]
    proc2 = subprocess.run(apply_cmd, capture_output=True, text=True)
    if proc2.returncode != 0:
        raise RuntimeError(
            f"loudnorm pass-2 failed (rc={proc2.returncode}): {proc2.stderr[-500:]}"
        )

    output_lufs = measure_integrated_lufs(str(out_p))
    deviation_lu = output_lufs - target_lufs
    deviation_pct = (
        abs(deviation_lu / target_lufs) * 100.0
        if abs(target_lufs) > 1e-6
        else 0.0
    )

    return LoudnessNormalizationResult(
        input_lufs=float(m["input_i"]),
        target_lufs=target_lufs,
        output_lufs=output_lufs,
        deviation_lu=deviation_lu,
        deviation_pct=deviation_pct,
        output_path=str(out_p),
        converged=deviation_pct <= convergence_pct,
    )


def mix_with_background(
    main_path: str,
    background_path: str,
    output_path: str,
    *,
    background_volume: float = 0.6,
    main_volume: float = 1.0,
    duration: str = "longest",
    sample_rate: int = 44100,
    channels: int = 2,
    bitrate: str = "192k",
) -> str:
    """两路音频 amix：main 主轨（TTS）+ 衰减后的 background。

    ``background_volume`` 是 background 相对原幅度的乘数（线性，非 dB）：
    - 1.0 = 原音量
    - 0.6 = 约 -4.4 dB（默认，BGM 不抢戏但可闻）
    - 0.0 = 静音

    ``duration`` 取值 ``"longest"`` / ``"first"`` / ``"shortest"``。
    一般 background 时长等于原视频，TTS 时长接近原视频，``"longest"``
    可以保留 BGM 自然尾巴。
    """
    if not Path(main_path).is_file():
        raise FileNotFoundError(f"main not found: {main_path}")
    if not Path(background_path).is_file():
        raise FileNotFoundError(f"background not found: {background_path}")
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    # ffmpeg amix 默认 normalize=1 会把 N 路输出再除以 N（求平均），即使其中
    # 一路几乎静音也会让输出比单路输入响度低 ~6 dB——这个对 B 算法（"反推 TTS
    # 让 mp4 整体 ≈ 原视频整体"）破坏巨大：测得的 pre_amix_lufs 偏低 6 dB →
    # delta 偏大 → 反推 TTS target 偏高 → 触发 ffmpeg loudnorm 上限报错。
    # 加 normalize=0 让 amix 直接相加（保留真实响度），与人感知 mix 一致。
    filter_graph = (
        f"[0:a]volume={main_volume}[m];"
        f"[1:a]volume={background_volume}[b];"
        f"[m][b]amix=inputs=2:duration={duration}:dropout_transition=0:normalize=0[out]"
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-y",
        "-i", str(main_path),
        "-i", str(background_path),
        "-filter_complex", filter_graph,
        "-map", "[out]",
        "-ar", str(sample_rate), "-ac", str(channels),
        "-b:a", bitrate,
        str(out_p),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg amix failed (rc={proc.returncode}): {proc.stderr[-500:]}"
        )
    return str(out_p)


def is_likely_silence(lufs: float) -> bool:
    """判断响度值是否表示"几乎全静音"（分离失败兜底判定）。"""
    return lufs < SILENCE_LUFS_THRESHOLD
