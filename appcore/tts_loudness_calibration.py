"""Shared TTS segment loudness calibration helpers."""
from __future__ import annotations

import logging
import math
import os

log = logging.getLogger(__name__)


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
            record.update(
                {
                    "status": "done" if bool(getattr(result, "converged", False)) else "warning_not_converged",
                    "output_path": normalized_path,
                    "input_lufs": _round_finite(getattr(result, "input_lufs", None)),
                    "output_lufs": _round_finite(getattr(result, "output_lufs", None)),
                    "deviation_lu": _round_finite(getattr(result, "deviation_lu", None)),
                    "deviation_pct": _round_finite(getattr(result, "deviation_pct", None)),
                    "converged": bool(getattr(result, "converged", False)),
                }
            )
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
