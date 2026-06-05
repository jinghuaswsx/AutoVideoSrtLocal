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
LOUDNESS_PROFILE_VOICE_ONLY = "voice_only"
LOUDNESS_PROFILE_CLEAN_BACKGROUND = "clean_background"
LOUDNESS_PROFILES = {
    LOUDNESS_PROFILE_STANDARD,
    LOUDNESS_PROFILE_AUTO_BOOST,
    LOUDNESS_PROFILE_MANUAL_BOOST,
    LOUDNESS_PROFILE_VOICE_ONLY,
    LOUDNESS_PROFILE_CLEAN_BACKGROUND,
}

BOOST_TARGET_GAP_LU = 7.0
BOOST_MAX_BACKGROUND_VOLUME = 2.4
DEFAULT_MANUAL_BOOST_PCT = 100
VOICE_PRIORITY_TARGET_GAP_LU = 12.0
VOICE_PRIORITY_MAX_WINDOWS = 80
VOICE_PRIORITY_MIN_WINDOW_SECONDS = 0.15
VOICE_PRIORITY_DOMINANT_WINDOW_LIMIT = 5
BACKGROUND_CLEANUP_MODE_DE_ELECTRIC = "de_electric"
DE_ELECTRIC_BACKGROUND_FILTER = (
    "highpass=f=80,"
    "equalizer=f=3000:t=q:w=1.4:g=-6,"
    "equalizer=f=6000:t=q:w=1.2:g=-10,"
    "lowpass=f=8000"
)


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
    normalized_profile = (
        LOUDNESS_PROFILE_STANDARD if profile is None else profile
    )
    if normalized_profile not in LOUDNESS_PROFILES:
        raise ValueError(f"unsupported loudness profile: {normalized_profile}")

    if normalized_profile != LOUDNESS_PROFILE_MANUAL_BOOST:
        return normalized_profile, None

    if (
        isinstance(manual_boost_pct, bool)
        or not isinstance(manual_boost_pct, int)
        or manual_boost_pct < 10
        or manual_boost_pct > 200
        or manual_boost_pct % 10 != 0
    ):
        raise ValueError(
            "manual_boost_pct must be an integer multiple of 10 from 10 to 200"
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
        "background_suppression": {
            "enabled": False,
        },
        "background_cleanup": {
            "enabled": False,
            "mode": None,
        },
    }


def _is_available_lufs(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value))


def _finite_float(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _round_audio_metric(value: float | None, digits: int = 2) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _background_volume_gain_db(background_volume: float) -> float | None:
    volume = _finite_float(background_volume)
    if volume is None or volume <= 0:
        return None
    return 20.0 * math.log10(volume)


def resolve_voice_priority_background_volume(
    *,
    background_volume: float,
    window_loudness: list[dict],
    target_gap_lu: float = VOICE_PRIORITY_TARGET_GAP_LU,
) -> dict:
    """Cap background volume so it stays below TTS in speech windows.

    ``window_loudness`` contains LUFS measured on matching TTS/background
    windows. If any background window would be louder than
    ``voice_lufs - target_gap_lu`` after the current volume is applied, the
    function returns a lower effective background volume.
    """
    standard_volume = max(0.0, float(background_volume))
    summary = {
        "enabled": False,
        "mode": "voice_priority_sentence_windows",
        "target_gap_lu": float(target_gap_lu),
        "standard_volume": standard_volume,
        "effective_volume": standard_volume,
        "fallback_reason": None,
        "window_count": len(window_loudness or []),
        "valid_window_count": 0,
        "risky_window_count": 0,
        "max_background_minus_voice_lu": None,
        "required_attenuation_lu": 0.0,
        "scale": 1.0,
        "dominant_windows": [],
    }
    gain_db = _background_volume_gain_db(standard_volume)
    if gain_db is None:
        summary["fallback_reason"] = "background_muted"
        return summary

    valid_windows: list[dict] = []
    for fallback_index, row in enumerate(window_loudness or []):
        if not isinstance(row, dict):
            continue
        voice_lufs = _finite_float(row.get("voice_lufs"))
        background_lufs = _finite_float(row.get("background_lufs"))
        if voice_lufs is None or background_lufs is None:
            continue
        if voice_lufs <= SILENCE_LUFS_THRESHOLD:
            continue
        adjusted_bg_lufs = background_lufs + gain_db
        delta = adjusted_bg_lufs - voice_lufs
        if not math.isfinite(delta):
            continue
        valid_windows.append({
            "index": row.get("index", fallback_index),
            "start": _round_audio_metric(row.get("start")),
            "end": _round_audio_metric(row.get("end")),
            "voice_lufs": _round_audio_metric(voice_lufs, 1),
            "background_lufs": _round_audio_metric(background_lufs, 1),
            "background_lufs_at_volume": _round_audio_metric(adjusted_bg_lufs, 1),
            "background_minus_voice_lu": _round_audio_metric(delta, 2),
        })

    summary["valid_window_count"] = len(valid_windows)
    if not valid_windows:
        summary["fallback_reason"] = "no_valid_windows"
        return summary

    target_delta = -float(target_gap_lu)
    dominant_windows = sorted(
        valid_windows,
        key=lambda item: float(item["background_minus_voice_lu"]),
        reverse=True,
    )
    risky_windows = [
        row for row in valid_windows
        if float(row["background_minus_voice_lu"]) > target_delta
    ]
    max_delta = float(dominant_windows[0]["background_minus_voice_lu"])
    summary["max_background_minus_voice_lu"] = _round_audio_metric(max_delta, 2)
    summary["risky_window_count"] = len(risky_windows)
    summary["dominant_windows"] = dominant_windows[:VOICE_PRIORITY_DOMINANT_WINDOW_LIMIT]

    if not risky_windows:
        summary["fallback_reason"] = "already_below_target_gap"
        return summary

    raw_required_attenuation = _round_audio_metric(target_delta - max_delta, 2)
    required_attenuation = raw_required_attenuation
    scale = 10 ** (float(required_attenuation) / 20.0)
    effective_volume = standard_volume * scale
    summary.update({
        "enabled": True,
        "fallback_reason": None,
        "effective_volume": effective_volume,
        "raw_required_attenuation_lu": raw_required_attenuation,
        "required_attenuation_lu": required_attenuation,
        "attenuation_capped": False,
        "scale": scale,
    })
    return summary


def build_ducking_volume_expression(
    *,
    segments: list[dict],
    background_volume: float,
    standard_volume: float,
    attack: float = 0.2,
    release: float = 0.4,
) -> str:
    """Build a dynamic ffmpeg volume expression to duck background music during speech.

    Ducks background to `background_volume` during speech segments plus `attack` lead-in
    and `release` tail-out times, and keeps it at `standard_volume` when no voice is playing.
    Transitions are linear. Overlapping intervals are merged to prevent multiple fades.
    """
    background_volume = float(background_volume)
    standard_volume = float(standard_volume)

    if background_volume >= standard_volume:
        return f"{standard_volume:.4f}"

    raw_windows = []
    for fallback_index, segment in enumerate(segments or []):
        res = _segment_audio_window(segment, fallback_index)
        if res is not None:
            _, start, end = res
            raw_windows.append((start, end))

    if not raw_windows:
        return f"{standard_volume:.4f}"

    # Merge overlapping extended windows [start - attack, end + release]
    extended = [[max(0.0, s - attack), e + release] for s, e in raw_windows]
    extended.sort(key=lambda x: x[0])

    merged = []
    for item in extended:
        if not merged:
            merged.append(item)
        else:
            prev = merged[-1]
            if item[0] <= prev[1]:
                prev[1] = max(prev[1], item[1])
            else:
                merged.append(item)

    # Build piecewise expression F_i(t) for each merged interval
    terms = []
    for S, E in merged:
        duration = E - S
        if duration <= 0:
            continue
        if duration <= attack + release:
            L = S + duration * (attack / (attack + release))
            R = L
        else:
            L = S + attack
            R = E - release

        L_diff = max(0.001, L - S)
        R_diff = max(0.001, E - R)

        term = (
            f"if(between(t,{S:.3f},{E:.3f}),"
            f"if(lt(t,{L:.3f}),(t-{S:.3f})/{L_diff:.3f},"
            f"if(gt(t,{R:.3f}),({E:.3f}-t)/{R_diff:.3f},1)),0)"
        )
        terms.append(term)

    if not terms:
        return f"{standard_volume:.4f}"

    diff = standard_volume - background_volume
    sum_terms = "+".join(terms)
    return f"{standard_volume:.4f}-{diff:.4f}*({sum_terms})"


def _segment_audio_window(segment: dict, fallback_index: int) -> tuple[int, float, float] | None:
    start = None
    for key in ("audio_start_time", "start_time", "source_start_time", "start"):
        start = _finite_float(segment.get(key))
        if start is not None:
            break
    if start is None:
        return None

    end = None
    for key in ("audio_end_time", "end_time", "source_end_time", "end"):
        end = _finite_float(segment.get(key))
        if end is not None:
            break
    if end is None:
        for key in ("tts_duration", "audio_duration", "duration"):
            duration = _finite_float(segment.get(key))
            if duration is not None:
                end = start + duration
                break
    if end is None:
        return None
    if end - start < VOICE_PRIORITY_MIN_WINDOW_SECONDS:
        return None
    index = segment.get("asr_index", segment.get("index", fallback_index))
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = fallback_index
    return index, max(0.0, start), max(0.0, end)


def _select_voice_priority_windows(
    segments: list[dict],
    *,
    max_windows: int = VOICE_PRIORITY_MAX_WINDOWS,
) -> list[tuple[int, float, float, dict]]:
    windows = [
        (window[0], window[1], window[2], segment)
        for fallback_index, segment in enumerate(segments or [])
        if isinstance(segment, dict)
        for window in [_segment_audio_window(segment, fallback_index)]
        if window is not None
    ]
    if len(windows) <= max_windows:
        return windows
    selected: list[tuple[int, float, float, dict]] = []
    used_positions: set[int] = set()
    for sample_index in range(max_windows):
        pos = int(sample_index * len(windows) / max_windows)
        if pos in used_positions:
            continue
        used_positions.add(pos)
        selected.append(windows[pos])
    return selected


def measure_window_lufs(audio_path: str, start_time: float, end_time: float) -> float:
    """Measure integrated LUFS for a short audio window."""
    p = Path(audio_path)
    if not p.is_file():
        raise FileNotFoundError(f"audio not found: {audio_path}")
    duration = max(0.0, float(end_time) - float(start_time))
    if duration < VOICE_PRIORITY_MIN_WINDOW_SECONDS:
        raise ValueError("audio window is too short for loudness measurement")
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-ss", f"{max(0.0, float(start_time)):.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(p),
        "-af", "ebur128=peak=true",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg ebur128 window measure failed (rc={proc.returncode}): {proc.stderr[-500:]}"
        )
    match = _INTEGRATED_LOUDNESS_RE.search(proc.stderr or "")
    if not match:
        raise RuntimeError(f"unable to parse ebur128 window output: {proc.stderr[-500:]}")
    value = match.group(1)
    return EBUR128_FLOOR if value == "-inf" else float(value)


def measure_voice_priority_background_windows(
    *,
    tts_audio_path: str,
    background_path: str,
    segments: list[dict],
    max_windows: int = VOICE_PRIORITY_MAX_WINDOWS,
) -> list[dict]:
    """Measure matching TTS/background LUFS windows from TTS segment timings."""
    records: list[dict] = []
    for index, start, end, segment in _select_voice_priority_windows(segments, max_windows=max_windows):
        try:
            segment_tts_path = str(segment.get("tts_path") or "").strip()
            if segment_tts_path and Path(segment_tts_path).is_file():
                voice_duration = _finite_float(segment.get("tts_duration"))
                if voice_duration is None:
                    voice_duration = _finite_float(segment.get("duration"))
                if voice_duration is None:
                    voice_duration = max(0.0, end - start)
                voice_lufs = measure_window_lufs(
                    segment_tts_path,
                    0.0,
                    max(VOICE_PRIORITY_MIN_WINDOW_SECONDS, voice_duration),
                )
            else:
                voice_lufs = measure_window_lufs(tts_audio_path, start, end)
            background_lufs = measure_window_lufs(background_path, start, end)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "[voice_priority_background] failed to measure window index=%s %.3f-%.3f: %s",
                index, start, end, exc,
            )
            continue
        records.append({
            "index": index,
            "start": start,
            "end": end,
            "voice_lufs": voice_lufs,
            "background_lufs": background_lufs,
        })
    return records


def calibrate_voice_priority_background_volume(
    *,
    tts_audio_path: str,
    background_path: str,
    segments: list[dict],
    background_volume: float,
    target_gap_lu: float = VOICE_PRIORITY_TARGET_GAP_LU,
    max_windows: int = VOICE_PRIORITY_MAX_WINDOWS,
) -> dict:
    """Measure sentence windows and return a voice-first background volume cap."""
    window_loudness = measure_voice_priority_background_windows(
        tts_audio_path=tts_audio_path,
        background_path=background_path,
        segments=segments,
        max_windows=max_windows,
    )
    return resolve_voice_priority_background_volume(
        background_volume=background_volume,
        window_loudness=window_loudness,
        target_gap_lu=target_gap_lu,
    )


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

    if normalized_profile == LOUDNESS_PROFILE_VOICE_ONLY:
        result["effective_background_volume"] = 0.0
        result["background_suppression"] = {
            "enabled": True,
            "standard_volume": standard_volume,
            "effective_volume": 0.0,
        }
        return result

    if normalized_profile == LOUDNESS_PROFILE_CLEAN_BACKGROUND:
        result["background_cleanup"] = {
            "enabled": True,
            "mode": BACKGROUND_CLEANUP_MODE_DE_ELECTRIC,
            "filter": DE_ELECTRIC_BACKGROUND_FILTER,
        }
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
    if isinstance(background_volume, str):
        bg_volume_filter = f"volume='{background_volume}':eval=frame"
    else:
        bg_volume_filter = f"volume={background_volume}"
    filter_graph = (
        f"[0:a]volume={main_volume}[m];"
        f"[1:a]{bg_volume_filter}[b];"
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


def clean_electronic_background(
    input_path: str,
    output_path: str,
    *,
    sample_rate: int = 44100,
    channels: int = 2,
) -> str:
    """Render an accompaniment copy with harsh electronic bands attenuated."""
    in_p = Path(input_path)
    if not in_p.is_file():
        raise FileNotFoundError(f"background not found: {input_path}")
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-y",
        "-i", str(in_p),
        "-af", DE_ELECTRIC_BACKGROUND_FILTER,
        "-ar", str(sample_rate), "-ac", str(channels),
        str(out_p),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg background cleanup failed (rc={proc.returncode}): {proc.stderr[-500:]}"
        )
    return str(out_p)


def is_likely_silence(lufs: float) -> bool:
    """判断响度值是否表示"几乎全静音"（分离失败兜底判定）。"""
    return lufs < SILENCE_LUFS_THRESHOLD
