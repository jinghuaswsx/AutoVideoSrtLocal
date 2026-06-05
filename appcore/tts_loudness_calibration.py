"""Shared TTS segment loudness calibration helpers."""
from __future__ import annotations

import logging
import math
import os
import subprocess

from appcore.audio_loudness import measure_integrated_lufs

log = logging.getLogger(__name__)

POST_GAIN_DEVIATION_THRESHOLD_LU = 2.0
POST_GAIN_MAX_GAIN_DB = 8.0
POST_GAIN_LIMIT = 0.891
SENTENCE_CLOSE_ENOUGH_LU = 1.5


def _finite_float(value) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _round_finite(value, digits: int = 3) -> float | None:
    numeric = _finite_float(value)
    if numeric is None:
        return None
    return round(numeric, digits)


def _deviation_pct(deviation_lu: float | None, target_lufs: float | None) -> float | None:
    deviation = _finite_float(deviation_lu)
    target = _finite_float(target_lufs)
    if deviation is None or target is None or abs(target) <= 1e-6:
        return None
    return abs(deviation / target) * 100.0


def _sentence_close_enough(deviation_lu: float | None) -> bool:
    deviation = _finite_float(deviation_lu)
    return deviation is not None and abs(deviation) <= SENTENCE_CLOSE_ENOUGH_LU


def _segment_index(row: dict, fallback: int) -> int:
    value = row.get("asr_index", row.get("index", fallback))
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def sentence_tts_loudness_enabled(task: dict) -> bool:
    cfg = task.get("plugin_config") if isinstance(task, dict) else None
    if not isinstance(cfg, dict):
        return False
    try:
        from appcore.omni_plugin_config import validate_plugin_config

        return bool(validate_plugin_config(cfg).get("sentence_tts_loudness_calibration"))
    except Exception:
        log.warning(
            "[tts_loudness_calibration] invalid plugin_config for sentence TTS loudness calibration",
            exc_info=True,
        )
        return False


def sentence_tts_target_lufs(task: dict) -> float | None:
    separation = task.get("separation") if isinstance(task, dict) else None
    if not isinstance(separation, dict):
        return None
    return _finite_float(separation.get("vocals_lufs"))


def _normalization_record(
    *,
    segment: dict,
    index: int,
    input_path: str,
    output_path: str | None,
    target_lufs: float | None,
) -> dict:
    return {
        "index": int(segment.get("index", index) or 0),
        "asr_index": _segment_index(segment, index),
        "input_path": input_path,
        "output_path": output_path or "",
        "target_lufs": _round_finite(target_lufs),
    }


def _apply_post_gain_correction(
    *,
    output_path: str,
    target_lufs: float,
    output_lufs: float | None,
) -> tuple[str, float | None, float | None, dict | None]:
    current_lufs = _finite_float(output_lufs)
    target = _finite_float(target_lufs)
    if current_lufs is None or target is None:
        return output_path, current_lufs, None, None

    deviation_lu = current_lufs - target
    if abs(deviation_lu) < POST_GAIN_DEVIATION_THRESHOLD_LU:
        return output_path, current_lufs, deviation_lu, None

    gain_db = max(
        -POST_GAIN_MAX_GAIN_DB,
        min(POST_GAIN_MAX_GAIN_DB, target - current_lufs),
    )
    correction = {
        "applied": False,
        "threshold_lu": POST_GAIN_DEVIATION_THRESHOLD_LU,
        "gain_db": _round_finite(gain_db),
        "before_output_lufs": _round_finite(current_lufs),
        "before_deviation_lu": _round_finite(deviation_lu),
    }
    tmp_path = f"{output_path}.postgain.tmp.mp3"
    audio_filter = f"volume={gain_db:.6f}dB,alimiter=limit={POST_GAIN_LIMIT}"
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-y",
        "-i", output_path,
        "-af", audio_filter,
        "-ar", "44100", "-ac", "2",
        tmp_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            correction["error"] = f"ffmpeg rc={proc.returncode}: {proc.stderr[-300:]}"
            return output_path, current_lufs, deviation_lu, correction

        corrected_lufs = _finite_float(measure_integrated_lufs(tmp_path))
        corrected_deviation = (
            corrected_lufs - target if corrected_lufs is not None else None
        )
        correction.update(
            {
                "corrected_output_lufs": _round_finite(corrected_lufs),
                "corrected_deviation_lu": _round_finite(corrected_deviation),
            }
        )
        if (
            corrected_deviation is not None
            and abs(corrected_deviation) < abs(deviation_lu)
        ):
            os.replace(tmp_path, output_path)
            correction["applied"] = True
            return output_path, corrected_lufs, corrected_deviation, correction

        correction["reason"] = "no_improvement"
        return output_path, current_lufs, deviation_lu, correction
    except Exception as exc:  # noqa: BLE001
        correction["error"] = str(exc)[:300]
        return output_path, current_lufs, deviation_lu, correction
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def apply_sentence_tts_loudness_calibration(
    *,
    task: dict,
    task_dir: str,
    final_tts_segments: list[dict],
    variant: str = "av",
) -> tuple[list[dict], dict]:
    """Normalize per-segment TTS loudness to separated vocals LUFS.

    The function is intentionally strategy-agnostic: callers pass the final
    segment list immediately before rebuilding or concatenating the full TTS
    track, and receive a copied segment list with calibrated ``tts_path`` values.
    """
    segments = [
        dict(segment) if isinstance(segment, dict) else segment
        for segment in (final_tts_segments or [])
    ]
    segment_count = sum(1 for segment in segments if isinstance(segment, dict))
    enabled = sentence_tts_loudness_enabled(task)
    summary = {
        "enabled": enabled,
        "status": "disabled",
        "target_lufs": None,
        "total_segment_count": segment_count,
        "normalized_segment_count": 0,
        "skipped_segment_count": 0,
        "failed_segment_count": 0,
        "segments": [],
    }
    if not enabled:
        summary["skipped_segment_count"] = segment_count
        return segments, summary

    target_lufs = sentence_tts_target_lufs(task)
    summary["target_lufs"] = _round_finite(target_lufs)
    if target_lufs is None:
        summary["status"] = "skipped_missing_vocals_lufs"
        summary["skipped_segment_count"] = segment_count
        return segments, summary
    if segment_count <= 0:
        summary["status"] = "skipped_no_segments"
        return segments, summary

    from appcore.audio_loudness import normalize_to_lufs

    normalized_dir = os.path.join(task_dir, "tts_loudness_segments", variant or "default")
    os.makedirs(normalized_dir, exist_ok=True)
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        input_path = str(segment.get("tts_path") or "").strip()
        output_path = os.path.join(normalized_dir, f"seg_{index:04d}.mp3")
        record = _normalization_record(
            segment=segment,
            index=index,
            input_path=input_path,
            output_path=output_path,
            target_lufs=target_lufs,
        )
        if not input_path or not os.path.isfile(input_path):
            record["status"] = "skipped_missing_audio"
            summary["skipped_segment_count"] += 1
            segment["sentence_tts_loudness_calibration"] = record
            summary["segments"].append(record)
            continue
        try:
            result = normalize_to_lufs(input_path, output_path, target_lufs=target_lufs)
            normalized_path = str(getattr(result, "output_path", "") or output_path)
            output_lufs = _finite_float(getattr(result, "output_lufs", None))
            deviation_lu = _finite_float(getattr(result, "deviation_lu", None))
            normalized_path, output_lufs, deviation_lu, post_gain_correction = _apply_post_gain_correction(
                output_path=normalized_path,
                target_lufs=target_lufs,
                output_lufs=output_lufs,
            )
            deviation_pct = _deviation_pct(deviation_lu, target_lufs)
            converged = bool(getattr(result, "converged", False)) or _sentence_close_enough(deviation_lu)
            record.update(
                {
                    "status": "done" if converged else "warning_not_converged",
                    "output_path": normalized_path,
                    "input_lufs": _round_finite(getattr(result, "input_lufs", None)),
                    "output_lufs": _round_finite(output_lufs),
                    "deviation_lu": _round_finite(deviation_lu),
                    "deviation_pct": _round_finite(deviation_pct),
                    "converged": converged,
                }
            )
            if post_gain_correction is not None:
                record["post_gain_correction"] = post_gain_correction
            segment["tts_path"] = normalized_path
            summary["normalized_segment_count"] += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "[tts_loudness_calibration] sentence TTS loudness calibration failed task=%s segment=%s: %s",
                task.get("id") if isinstance(task, dict) else "?",
                record["asr_index"],
                exc,
                exc_info=True,
            )
            record["status"] = "failed"
            record["error"] = str(exc)[:300]
            summary["failed_segment_count"] += 1
        segment["sentence_tts_loudness_calibration"] = record
        summary["segments"].append(record)

    if summary["normalized_segment_count"] > 0:
        if summary["failed_segment_count"] or summary["skipped_segment_count"]:
            summary["status"] = "partial"
        else:
            summary["status"] = "done"
    elif summary["failed_segment_count"] > 0:
        summary["status"] = "failed"
    else:
        summary["status"] = "skipped_no_audio"
    return segments, summary
