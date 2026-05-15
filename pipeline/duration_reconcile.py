from __future__ import annotations

import os
import subprocess
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from queue import Empty, Queue
from typing import Any, Callable

from appcore import omni_ffmpeg_tempo_config
from pipeline import av_translate, tts

MIN_DURATION_RATIO = 0.95
MAX_DURATION_RATIO = 1.05
MIN_FFMPEG_TEMPO_RATIO = 0.9
MAX_FFMPEG_TEMPO_RATIO = 1.1
MIN_TTS_SPEED = 0.95
MAX_TTS_SPEED = 1.05
MAX_TEXT_REWRITE_ATTEMPTS = 10
MAX_TTS_REGENERATE_ATTEMPTS = 10
DEFAULT_SENTENCE_RECONCILE_WORKERS = 5


def duration_ratio(target_duration: float, tts_duration: float) -> float:
    if target_duration <= 0:
        return 1.0
    return tts_duration / target_duration


def compute_speed_for_target(target_duration: float, tts_duration: float) -> float | None:
    if target_duration <= 0 or tts_duration <= 0:
        return 1.0
    speed = tts_duration / target_duration
    if MIN_TTS_SPEED <= speed <= MAX_TTS_SPEED:
        return round(speed, 4)
    return None


def classify_overshoot(target_duration: float, tts_duration: float) -> tuple[str, float]:
    """Return (status, speed) using the v2 duration reconciliation thresholds."""
    ratio = duration_ratio(target_duration, tts_duration)
    if MIN_DURATION_RATIO <= ratio <= MAX_DURATION_RATIO:
        return ("ok", 1.0)
    if ratio > MAX_DURATION_RATIO:
        return ("needs_rewrite", 1.0)
    return ("needs_expand", 1.0)


def _tts_segment_map(tts_output: dict) -> dict[int, dict]:
    mapped = {}
    for position, segment in enumerate((tts_output or {}).get("segments") or []):
        asr_index = int(segment.get("asr_index", segment.get("index", position)))
        mapped[asr_index] = segment
    return mapped


def _scaled_target_chars_range(old_range: Any, target_duration: float, tts_duration: float) -> tuple[int, int]:
    if not old_range or len(old_range) != 2 or tts_duration <= 0:
        return (1, 2)
    scale = target_duration / tts_duration
    lo = max(1, int(old_range[0] * scale))
    hi = max(lo + 1, int(old_range[1] * scale + 0.5))
    return (lo, hi)


def _duration_reason(status: str) -> str:
    if status == "ok":
        return "within_duration_ratio"
    if status == "needs_semantic_repair":
        return "semantic_coverage_missing"
    if status == "needs_rewrite":
        return "above_duration_ratio"
    if status == "needs_expand":
        return "below_duration_ratio"
    return status


def _duration_distance(target_duration: float, tts_duration: float) -> float:
    return abs(duration_ratio(target_duration, tts_duration) - 1.0)


def _delta_pct(target_duration: float, tts_duration: float) -> float:
    if target_duration <= 0:
        return 0.0
    return round(((tts_duration - target_duration) / target_duration) * 100, 2)


def _candidate_from_current(current: dict, *, round_number: int) -> dict:
    return {
        "round": round_number,
        "text": current["text"],
        "tts_path": current.get("tts_path"),
        "tts_duration": float(current.get("tts_duration", 0.0) or 0.0),
        "duration_ratio": duration_ratio(
            float(current.get("target_duration", 0.0) or 0.0),
            float(current.get("tts_duration", 0.0) or 0.0),
        ),
        "target_duration": float(current.get("target_duration", 0.0) or 0.0),
        "target_chars_range": tuple(current.get("target_chars_range") or (1, 2)),
        "status": current.get("status", "ok"),
        "speed": current.get("speed", 1.0),
        "must_keep_terms": list(current.get("must_keep_terms") or []),
        "covered_source_terms": list(current.get("covered_source_terms") or []),
        "omitted_source_terms": list(current.get("omitted_source_terms") or []),
        "coverage_ok": current.get("coverage_ok", True),
        "semantic_repair_attempts": int(current.get("semantic_repair_attempts", 0) or 0),
    }


def _error_text(exc: Exception) -> str:
    return str(exc)[:500]


def _apply_candidate(current: dict, candidate: dict) -> None:
    current["text"] = candidate["text"]
    current["est_chars"] = len(candidate["text"])
    current["tts_path"] = candidate.get("tts_path")
    current["tts_duration"] = float(candidate.get("tts_duration", 0.0) or 0.0)
    current["target_chars_range"] = tuple(candidate.get("target_chars_range") or current["target_chars_range"])
    current["duration_ratio"] = duration_ratio(current["target_duration"], current["tts_duration"])
    current["speed"] = candidate.get("speed", 1.0)
    current["selected_attempt_round"] = int(candidate.get("round", 0) or 0)
    for key in ("must_keep_terms", "covered_source_terms", "omitted_source_terms"):
        if key in candidate:
            current[key] = list(candidate.get(key) or [])
    if "coverage_ok" in candidate:
        current["coverage_ok"] = bool(candidate.get("coverage_ok"))
    if "semantic_repair_attempts" in candidate:
        current["semantic_repair_attempts"] = int(candidate.get("semantic_repair_attempts") or 0)


def _final_extra_expand_attempt_record(
    *,
    current: dict,
    before_text: str,
    after_text: str,
    status: str,
    selected: bool,
) -> dict:
    target_duration = float(current.get("target_duration", 0.0) or 0.0)
    tts_duration = float(current.get("tts_duration", 0.0) or 0.0)
    ratio = duration_ratio(target_duration, tts_duration)
    return {
        "round": 999,
        "text_attempt": current.get("text_rewrite_attempts"),
        "tts_attempt": current.get("tts_regenerate_attempts"),
        "temperature": current.get("active_temperature"),
        "action": "expand",
        "before_text": before_text,
        "after_text": after_text,
        "target_duration": target_duration,
        "tts_duration": tts_duration,
        "duration_ratio": round(ratio, 4),
        "delta_pct": _delta_pct(target_duration, tts_duration),
        "status": status,
        "reason": _duration_reason(status),
        "coverage_ok": current.get("coverage_ok", True),
        "omitted_source_terms": list(current.get("omitted_source_terms") or []),
        "selected": selected,
        "final_extra_expand": True,
    }


def _record_final_extra_expand_candidate(
    *,
    current: dict,
    before_text: str,
    after_text: str,
    status: str,
    selected: bool,
) -> None:
    target_duration = float(current.get("target_duration", 0.0) or 0.0)
    tts_duration = float(current.get("tts_duration", 0.0) or 0.0)
    ratio = duration_ratio(target_duration, tts_duration)
    current["final_extra_expand_after_text"] = after_text
    current["final_extra_expand_tts_duration"] = tts_duration
    current["final_extra_expand_target_duration"] = target_duration
    current["final_extra_expand_duration_ratio"] = ratio
    current["final_extra_expand_delta_pct"] = _delta_pct(target_duration, tts_duration)
    current["final_extra_expand_status"] = status
    current["final_extra_expand_reason"] = _duration_reason(status)
    current["final_extra_expand_selected"] = selected
    current["final_extra_expand_attempt"] = _final_extra_expand_attempt_record(
        current=current,
        before_text=before_text,
        after_text=after_text,
        status=status,
        selected=selected,
    )


def _candidate_suffix(kind: str, round_number: int, attempt_number: int | None = None) -> str:
    if attempt_number is None:
        return f"{kind}_r{round_number}"
    return f"{kind}_r{round_number}_a{attempt_number}"


def _ffmpeg_tempo_output_path(current: dict, *, round_number: int, attempt_number: int) -> str:
    output_path = current.get("tts_path") or f"av_seg_{current['asr_index']}.mp3"
    base, ext = os.path.splitext(output_path)
    return f"{base}.ffmpeg_tempo_r{round_number}_a{attempt_number}{ext or '.mp3'}"


def _apply_ffmpeg_tempo_alignment(
    *,
    audio_path: str,
    audio_duration: float,
    target_duration: float,
    output_path: str,
) -> dict | None:
    if not audio_path or audio_duration <= 0 or target_duration <= 0:
        return None
    ratio = audio_duration / target_duration
    if not (MIN_FFMPEG_TEMPO_RATIO <= ratio <= MAX_FFMPEG_TEMPO_RATIO):
        return None
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-filter:a", f"atempo={ratio:.4f}",
        "-vn", "-acodec", "libmp3lame", "-q:a", "3",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except Exception as exc:
        return {"failed_reason": _error_text(exc)}
    if result.returncode != 0:
        return {"failed_reason": (result.stderr or "ffmpeg tempo alignment failed")[:500]}
    post_duration = tts.get_audio_duration(output_path)
    if post_duration <= 0:
        return {"failed_reason": "ffprobe returned empty duration"}
    return {
        "ratio": round(ratio, 4),
        "pre_duration": round(audio_duration, 3),
        "post_duration": round(post_duration, 3),
        "new_audio_path": output_path,
    }


def _mark_selected_attempt(attempts: list[dict], selected_round: int) -> None:
    for attempt in attempts:
        attempt["selected"] = int(attempt.get("round", -1)) == selected_round


def _text_rewrite_enabled_for_task(task: dict | None) -> bool:
    return True


def _warning_status_for_ratio(ratio: float) -> str:
    return "warning_long" if ratio > MAX_DURATION_RATIO else "warning_short"


def _semantic_coverage_issue(sentence: dict) -> bool:
    omitted = [str(term).strip() for term in (sentence.get("omitted_source_terms") or []) if str(term).strip()]
    return sentence.get("coverage_ok") is False or bool(omitted)


def _candidate_rank(candidate: dict) -> tuple[int, float]:
    return (
        1 if _semantic_coverage_issue(candidate) else 0,
        _duration_distance(
            float(candidate.get("target_duration", 0.0) or 0.0),
            float(candidate.get("tts_duration", 0.0) or 0.0),
        ),
    )


def _warning_status_for_current(current: dict) -> str:
    if _semantic_coverage_issue(current):
        return "warning_semantic"
    return _warning_status_for_ratio(float(current.get("duration_ratio", 1.0) or 1.0))


def _sentence_progress_payload(position: int, current: dict, phase: str) -> dict:
    return {
        "mode": "sentence_reconcile",
        "round": position + 1,
        "sentence_position": position,
        "asr_index": current.get("asr_index"),
        "phase": phase,
        "target_duration": current.get("target_duration"),
        "target_chars": list(current.get("target_chars_range") or []),
        "audio_duration": current.get("tts_duration"),
        "duration_ratio": round(float(current.get("duration_ratio", 0.0) or 0.0), 4),
        "delta_pct": _delta_pct(
            float(current.get("target_duration", 0.0) or 0.0),
            float(current.get("tts_duration", 0.0) or 0.0),
        ),
        "text": current.get("text", ""),
        "source_text": current.get("source_text") or current.get("original_source_text") or "",
        "status": current.get("status", ""),
        "speed": current.get("speed", 1.0),
        "active_attempt": current.get("active_attempt"),
        "active_action": current.get("active_action", ""),
        "active_temperature": current.get("active_temperature"),
        "active_tts_attempt": current.get("active_tts_attempt"),
        "pending_tts_text": current.get("pending_tts_text", ""),
        "text_rewrite_attempts": current.get("text_rewrite_attempts", 0),
        "tts_regenerate_attempts": current.get("tts_regenerate_attempts", 0),
        "speed_adjustment_attempts": current.get("speed_adjustment_attempts", 0),
        "semantic_repair_attempts": current.get("semantic_repair_attempts", 0),
        "max_text_rewrite_attempts": current.get("max_text_rewrite_attempts"),
        "max_tts_regenerate_attempts": current.get("max_tts_regenerate_attempts"),
        "must_keep_terms": list(current.get("must_keep_terms") or []),
        "omitted_source_terms": list(current.get("omitted_source_terms") or []),
        "coverage_ok": current.get("coverage_ok", True),
        "rewrite_skip_reason": current.get("rewrite_skip_reason", ""),
        "best_effort": bool(current.get("best_effort")),
        "best_effort_reason": current.get("best_effort_reason", ""),
        "final_fallback_action": current.get("final_fallback_action", ""),
        "final_fallback_reason": current.get("final_fallback_reason", ""),
        "ffmpeg_tempo_applied": bool(current.get("ffmpeg_tempo_applied")),
        "ffmpeg_tempo_ratio": current.get("ffmpeg_tempo_ratio"),
        "ffmpeg_tempo_pre_duration": current.get("ffmpeg_tempo_pre_duration"),
        "ffmpeg_tempo_post_duration": current.get("ffmpeg_tempo_post_duration"),
        "ffmpeg_tempo_audio_path": current.get("ffmpeg_tempo_audio_path"),
        "ffmpeg_tempo_failed_reason": current.get("ffmpeg_tempo_failed_reason", ""),
        "ffmpeg_tempo_skipped_reason": current.get("ffmpeg_tempo_skipped_reason", ""),
        "final_extra_expand_attempted": bool(current.get("final_extra_expand_attempted")),
        "final_extra_expand_result": current.get("final_extra_expand_result", ""),
        "final_extra_expand_before_text": current.get("final_extra_expand_before_text", ""),
        "final_extra_expand_after_text": current.get("final_extra_expand_after_text", ""),
        "final_extra_expand_selected": current.get("final_extra_expand_selected"),
        "final_extra_expand_tts_duration": current.get("final_extra_expand_tts_duration"),
        "final_extra_expand_target_duration": current.get("final_extra_expand_target_duration"),
        "final_extra_expand_duration_ratio": current.get("final_extra_expand_duration_ratio"),
        "final_extra_expand_delta_pct": current.get("final_extra_expand_delta_pct"),
        "final_extra_expand_status": current.get("final_extra_expand_status", ""),
        "final_extra_expand_reason": current.get("final_extra_expand_reason", ""),
        "final_extra_expand_attempt": current.get("final_extra_expand_attempt"),
        "attempts": list(current.get("attempts") or []),
    }


def _emit_sentence_progress(
    callback: Callable[[dict], None] | None,
    *,
    position: int,
    current: dict,
    phase: str,
) -> None:
    if callback is None:
        return
    callback(_sentence_progress_payload(position, current, phase))


def _preserve_sentence_fields(current: dict, av_sentence: dict) -> None:
    for key, value in av_sentence.items():
        if key in current:
            continue
        if (
            key.startswith("source")
            or key.startswith("original_source")
            or key.startswith("localization")
            or key.startswith("shot")
            or key in {"must_keep_terms", "covered_source_terms", "omitted_source_terms", "coverage_ok"}
        ):
            current[key] = value


def _regenerate_segment(
    *,
    sentence: dict,
    voice_id: str,
    target_language: str,
    speed: float | None = None,
    suffix: str | None = None,
) -> tuple[str, float]:
    output_path = sentence.get("tts_path") or f"av_seg_{sentence['asr_index']}.mp3"
    if suffix:
        base, ext = os.path.splitext(output_path)
        output_path = f"{base}.{suffix}{ext or '.mp3'}"
    tts.generate_segment_audio(
        text=sentence["text"],
        voice_id=voice_id,
        output_path=output_path,
        language_code=target_language,
        speed=speed,
    )
    return output_path, tts.get_audio_duration(output_path)


def _try_ffmpeg_tempo_alignment(
    *,
    current: dict,
    position: int,
    on_progress: Callable[[dict], None] | None,
    reason: str,
) -> bool:
    target_duration = float(current.get("target_duration", 0.0) or 0.0)
    audio_duration = float(current.get("tts_duration", 0.0) or 0.0)
    ratio = duration_ratio(target_duration, audio_duration)
    if not (MIN_FFMPEG_TEMPO_RATIO <= ratio <= MAX_FFMPEG_TEMPO_RATIO):
        return False
    if abs(audio_duration - target_duration) < 0.001:
        return False

    current["speed_adjustment_attempts"] += 1
    round_number = int(current.get("selected_attempt_round", 0) or 0)
    output_path = _ffmpeg_tempo_output_path(
        current,
        round_number=round_number,
        attempt_number=current["speed_adjustment_attempts"],
    )
    result = _apply_ffmpeg_tempo_alignment(
        audio_path=str(current.get("tts_path") or ""),
        audio_duration=audio_duration,
        target_duration=target_duration,
        output_path=output_path,
    )
    current["final_fallback_action"] = "ffmpeg_tempo_align"
    current["final_fallback_reason"] = reason
    if not result or result.get("failed_reason"):
        current["ffmpeg_tempo_applied"] = False
        current["ffmpeg_tempo_failed_reason"] = (
            (result or {}).get("failed_reason") or "ffmpeg tempo alignment skipped"
        )
        _emit_sentence_progress(on_progress, position=position, current=current, phase="ffmpeg_tempo_align")
        return False

    current["tts_path"] = result["new_audio_path"]
    current["tts_duration"] = float(result["post_duration"])
    current["duration_ratio"] = duration_ratio(target_duration, current["tts_duration"])
    current["speed"] = result["ratio"]
    current["status"] = "speed_adjusted"
    current["ffmpeg_tempo_applied"] = True
    current["ffmpeg_tempo_ratio"] = result["ratio"]
    current["ffmpeg_tempo_pre_duration"] = result["pre_duration"]
    current["ffmpeg_tempo_post_duration"] = result["post_duration"]
    current["ffmpeg_tempo_audio_path"] = result["new_audio_path"]
    if not _semantic_coverage_issue(current):
        current["best_effort"] = False
        current.pop("best_effort_reason", None)
    _emit_sentence_progress(on_progress, position=position, current=current, phase="ffmpeg_tempo_align")
    return True


def _resolve_final_overlong_with_ffmpeg(
    *,
    current: dict,
    position: int,
    on_progress: Callable[[dict], None] | None,
    reason: str,
    ffmpeg_tempo_enabled: bool,
) -> bool:
    if _semantic_coverage_issue(current):
        return False
    ratio = duration_ratio(
        float(current.get("target_duration", 0.0) or 0.0),
        float(current.get("tts_duration", 0.0) or 0.0),
    )
    if not (MAX_DURATION_RATIO < ratio <= MAX_FFMPEG_TEMPO_RATIO):
        return False
    if not ffmpeg_tempo_enabled:
        current["status"] = "warning_long"
        current["speed"] = 1.0
        current["ffmpeg_tempo_applied"] = False
        current["ffmpeg_tempo_skipped_reason"] = "disabled"
        current["final_fallback_reason"] = reason
        current["best_effort"] = True
        current["best_effort_reason"] = "ffmpeg_tempo_disabled"
        _emit_sentence_progress(
            on_progress,
            position=position,
            current=current,
            phase="ffmpeg_tempo_skipped",
        )
        return True

    applied = _try_ffmpeg_tempo_alignment(
        current=current,
        position=position,
        on_progress=on_progress,
        reason=reason,
    )
    if applied:
        return True
    if current.get("status") != "ok":
        current["status"] = "warning_long"
        current["best_effort"] = True
        current["best_effort_reason"] = (
            "ffmpeg_tempo_failed" if current.get("ffmpeg_tempo_failed_reason")
            else "final_overlong_without_alignment"
        )
    return True


def _mark_clip_overlong_fallback(
    *,
    current: dict,
    position: int,
    on_progress: Callable[[dict], None] | None,
) -> None:
    current["final_fallback_action"] = "clip_overlong"
    current["final_fallback_reason"] = "overlong_after_attempts"
    _emit_sentence_progress(on_progress, position=position, current=current, phase="final_clip_fallback")


def _run_final_extra_expand(
    *,
    current: dict,
    position: int,
    voice_id: str,
    target_language: str,
    av_inputs: dict,
    shot_notes: dict,
    script_segments: list[dict],
    user_id: int | None,
    project_id: str | None,
    on_progress: Callable[[dict], None] | None,
    ffmpeg_tempo_enabled: bool,
) -> None:
    before_text = current.get("text", "")
    before_candidate = _candidate_from_current(
        current,
        round_number=int(current.get("selected_attempt_round", 0) or 0),
    )
    current["final_extra_expand_attempted"] = True
    current["final_fallback_action"] = "extra_expand"
    current["final_fallback_reason"] = "short_after_attempts"
    current["final_extra_expand_before_text"] = before_text
    current["active_attempt"] = 999
    current["active_action"] = "expand"
    current["active_temperature"] = av_translate.rewrite_temperature_for_attempt(999)
    current["active_tts_attempt"] = current.get("tts_regenerate_attempts", 0) + 1
    _emit_sentence_progress(on_progress, position=position, current=current, phase="final_extra_expand_start")
    try:
        rewrite_result = av_translate.rewrite_one(
            asr_index=int(current.get("asr_index", position)),
            prev_text=before_text,
            overshoot_sec=0.0,
            direction="expand",
            new_target_chars_range=tuple(current.get("target_chars_range") or (1, 2)),
            script_segments=script_segments,
            shot_notes=shot_notes,
            av_inputs=av_inputs,
            voice_id=voice_id,
            user_id=user_id,
            project_id=project_id,
            attempt_number=999,
            previous_attempts=list(current.get("attempts") or []),
            temperature=current["active_temperature"],
            required_terms=list(current.get("must_keep_terms") or []),
            omitted_terms=list(current.get("omitted_source_terms") or []),
            return_sentence=True,
        )
    except Exception as exc:
        current["final_fallback_action"] = "extra_expand_failed"
        current["final_extra_expand_result"] = "rewrite_failed"
        current["final_extra_expand_error"] = _error_text(exc)
        _emit_sentence_progress(on_progress, position=position, current=current, phase="final_extra_expand_result")
        return

    if isinstance(rewrite_result, dict):
        new_text = str(rewrite_result.get("text") or "")
        if "covered_source_terms" in rewrite_result:
            current["covered_source_terms"] = list(rewrite_result.get("covered_source_terms") or [])
        if "omitted_source_terms" in rewrite_result:
            current["omitted_source_terms"] = list(rewrite_result.get("omitted_source_terms") or [])
        if "coverage_ok" in rewrite_result:
            current["coverage_ok"] = bool(rewrite_result.get("coverage_ok"))
    else:
        new_text = str(rewrite_result or "")

    current["text"] = new_text
    current["est_chars"] = len(new_text)
    current["tts_path"], current_duration = _regenerate_segment(
        sentence=current,
        voice_id=voice_id,
        target_language=target_language,
        suffix=_candidate_suffix("final_expand", 999),
    )
    current["tts_regenerate_attempts"] += 1
    current["tts_duration"] = current_duration
    current["duration_ratio"] = duration_ratio(current["target_duration"], current_duration)
    status, speed = classify_overshoot(current["target_duration"], current_duration)
    if _semantic_coverage_issue(current):
        status = "needs_semantic_repair"
    current["status"] = status
    current["speed"] = speed
    extra_candidate = _candidate_from_current(current, round_number=999)

    select_extra_candidate = _candidate_rank(extra_candidate) <= _candidate_rank(before_candidate)
    _record_final_extra_expand_candidate(
        current=current,
        before_text=before_text,
        after_text=new_text,
        status=status,
        selected=select_extra_candidate,
    )

    if not select_extra_candidate:
        _apply_candidate(current, before_candidate)
        _mark_selected_attempt(current["attempts"], current["selected_attempt_round"])
        current["status"] = _warning_status_for_current(current)
        current["speed"] = 1.0
        current["final_fallback_action"] = "extra_expand_failed"
        current["final_fallback_reason"] = "short_after_attempts"
        current["final_extra_expand_result"] = "not_selected"
        current["best_effort"] = True
        current["best_effort_reason"] = "final_extra_expand_candidate_not_selected"
        _emit_sentence_progress(on_progress, position=position, current=current, phase="final_extra_expand_result")
        return

    if current["status"] == "ok" and not _semantic_coverage_issue(current):
        current["final_extra_expand_result"] = "accepted"
        current["best_effort"] = False
        current.pop("best_effort_reason", None)
        _emit_sentence_progress(on_progress, position=position, current=current, phase="final_extra_expand_result")
        return

    if _resolve_final_overlong_with_ffmpeg(
        current=current,
        position=position,
        on_progress=on_progress,
        reason="overlong_after_extra_expand",
        ffmpeg_tempo_enabled=ffmpeg_tempo_enabled,
    ):
        aligned = current.get("ffmpeg_tempo_applied") or current.get("status") == "speed_adjusted"
        current["final_extra_expand_result"] = "aligned" if aligned else "still_long"
        if not aligned:
            current["final_fallback_action"] = "extra_expand_failed"
        _emit_sentence_progress(on_progress, position=position, current=current, phase="final_extra_expand_result")
        return

    current["final_fallback_action"] = "extra_expand_failed"
    if current["duration_ratio"] < MIN_DURATION_RATIO:
        current["final_extra_expand_result"] = "still_short"
        current["status"] = "warning_short"
    else:
        current["final_extra_expand_result"] = "still_long"
        current["status"] = _warning_status_for_ratio(current["duration_ratio"])
    current["best_effort"] = True
    current["best_effort_reason"] = "final_extra_expand_missed"
    _emit_sentence_progress(on_progress, position=position, current=current, phase="final_extra_expand_result")


def _initial_sentence_state(
    *,
    position: int,
    av_sentence: dict,
    tts_by_index: dict[int, dict],
    max_rewrite_rounds: int,
    max_tts_regenerate_attempts: int,
) -> dict:
    asr_index = int(av_sentence.get("asr_index", position))
    tts_segment = dict(tts_by_index.get(asr_index, {}))
    current = {
        "asr_index": asr_index,
        "start_time": av_sentence.get("start_time"),
        "end_time": av_sentence.get("end_time"),
        "target_duration": float(av_sentence.get("target_duration", 0.0) or 0.0),
        "target_chars_range": tuple(av_sentence.get("target_chars_range") or (1, 2)),
        "text": av_sentence.get("text", ""),
        "est_chars": int(av_sentence.get("est_chars", len(av_sentence.get("text", ""))) or 0),
        "tts_path": tts_segment.get("tts_path"),
        "tts_duration": float(tts_segment.get("tts_duration", 0.0) or 0.0),
        "speed": 1.0,
        "rewrite_rounds": 0,
        "text_rewrite_attempts": 0,
        "tts_regenerate_attempts": 0,
        "speed_adjustment_attempts": 0,
        "semantic_repair_attempts": 0,
        "max_text_rewrite_attempts": max_rewrite_rounds,
        "max_tts_regenerate_attempts": max_tts_regenerate_attempts,
        "selected_attempt_round": 0,
        "best_effort": False,
        "status": "ok",
        "duration_ratio": duration_ratio(
            float(av_sentence.get("target_duration", 0.0) or 0.0),
            float(tts_segment.get("tts_duration", 0.0) or 0.0),
        ),
        "must_keep_terms": list(av_sentence.get("must_keep_terms") or []),
        "covered_source_terms": list(av_sentence.get("covered_source_terms") or []),
        "omitted_source_terms": list(av_sentence.get("omitted_source_terms") or []),
        "coverage_ok": (
            bool(av_sentence.get("coverage_ok"))
            if av_sentence.get("coverage_ok") is not None
            else not bool(av_sentence.get("omitted_source_terms") or [])
        ),
        "attempts": [],
    }
    _preserve_sentence_fields(current, av_sentence)

    status, speed = classify_overshoot(current["target_duration"], current["tts_duration"])
    if _semantic_coverage_issue(current):
        status = "needs_semantic_repair"
    current["status"] = status
    current["speed"] = speed
    current["duration_ratio"] = duration_ratio(current["target_duration"], current["tts_duration"])
    return current


def _reconcile_one_sentence(
    *,
    position: int,
    current: dict,
    text_rewrite_enabled: bool,
    voice_id: str,
    target_language: str,
    av_inputs: dict,
    shot_notes: dict,
    script_segments: list[dict],
    user_id: int | None,
    project_id: str | None,
    max_rewrite_rounds: int,
    max_tts_regenerate_attempts: int,
    ffmpeg_tempo_enabled: bool,
    on_progress: Callable[[dict], None] | None,
) -> dict:
    _emit_sentence_progress(on_progress, position=position, current=current, phase="initial_measure")
    status = current["status"]
    asr_index = int(current.get("asr_index", position))

    if status in {"needs_rewrite", "needs_expand", "needs_semantic_repair"}:
        if not text_rewrite_enabled and status != "needs_semantic_repair":
            current["text_rewrite_disabled"] = True
            current["rewrite_skip_reason"] = "shot_char_limit_preserves_initial_translation"
            current["status"] = _warning_status_for_ratio(current["duration_ratio"])
            current["speed"] = 1.0
            current["best_effort"] = True
            current["best_effort_reason"] = "shot_char_limit_rewrite_disabled"
            _emit_sentence_progress(on_progress, position=position, current=current, phase="rewrite_skipped")
        else:
            current_duration = current["tts_duration"]
            best_candidate = _candidate_from_current(current, round_number=0)
            round_limit = min(max_rewrite_rounds, max_tts_regenerate_attempts)
            for rewrite_round in range(1, round_limit + 1):
                before_text = current["text"]
                if _semantic_coverage_issue(current):
                    action = "repair_coverage"
                    current["semantic_repair_attempts"] += 1
                elif current["status"] == "needs_rewrite":
                    action = "shorten"
                else:
                    action = "expand"
                if action == "repair_coverage" and duration_ratio(current["target_duration"], current_duration) >= MIN_DURATION_RATIO:
                    new_range = tuple(current["target_chars_range"])
                else:
                    new_range = _scaled_target_chars_range(
                        current["target_chars_range"],
                        current["target_duration"],
                        current_duration,
                    )
                rewrite_temperature = av_translate.rewrite_temperature_for_attempt(rewrite_round)
                current["text_rewrite_attempts"] += 1
                current["active_attempt"] = rewrite_round
                current["active_action"] = action
                current["active_temperature"] = rewrite_temperature
                current["active_tts_attempt"] = current["tts_regenerate_attempts"] + 1
                current["pending_tts_text"] = ""
                _emit_sentence_progress(on_progress, position=position, current=current, phase="rewrite_start")
                try:
                    rewrite_result = av_translate.rewrite_one(
                        asr_index=asr_index,
                        prev_text=before_text,
                        overshoot_sec=max(0.0, current_duration - current["target_duration"]),
                        direction=action,
                        new_target_chars_range=new_range,
                        script_segments=script_segments,
                        shot_notes=shot_notes,
                        av_inputs=av_inputs,
                        voice_id=voice_id,
                        user_id=user_id,
                        project_id=project_id,
                        attempt_number=rewrite_round,
                        previous_attempts=list(current["attempts"]),
                        temperature=rewrite_temperature,
                        required_terms=list(current.get("must_keep_terms") or []),
                        omitted_terms=list(current.get("omitted_source_terms") or []),
                        return_sentence=True,
                    )
                except Exception as exc:
                    current["rewrite_rounds"] = rewrite_round
                    current["pending_tts_text"] = ""
                    current["attempts"].append(
                        {
                            "round": rewrite_round,
                            "text_attempt": current["text_rewrite_attempts"],
                            "tts_attempt": current["tts_regenerate_attempts"],
                            "temperature": rewrite_temperature,
                            "action": action,
                            "before_text": before_text,
                            "after_text": "",
                            "target_duration": current["target_duration"],
                            "tts_duration": current_duration,
                            "duration_ratio": round(current["duration_ratio"], 4),
                            "delta_pct": _delta_pct(current["target_duration"], current_duration),
                            "status": "rewrite_error",
                            "reason": "rewrite_failed",
                            "error": _error_text(exc),
                            "selected": False,
                        }
                    )
                    _emit_sentence_progress(on_progress, position=position, current=current, phase="rewrite_error")
                    continue
                if isinstance(rewrite_result, dict):
                    debug_calls = rewrite_result.pop("_llm_debug_calls", [])
                    if debug_calls:
                        current.setdefault("_llm_debug_calls", []).extend(debug_calls)
                    new_text = str(rewrite_result.get("text") or "")
                    if "covered_source_terms" in rewrite_result:
                        current["covered_source_terms"] = list(rewrite_result.get("covered_source_terms") or [])
                    if "omitted_source_terms" in rewrite_result:
                        current["omitted_source_terms"] = list(rewrite_result.get("omitted_source_terms") or [])
                    elif action == "repair_coverage":
                        current["omitted_source_terms"] = []
                    if "coverage_ok" in rewrite_result:
                        current["coverage_ok"] = bool(rewrite_result.get("coverage_ok"))
                    elif action == "repair_coverage":
                        current["coverage_ok"] = True
                else:
                    new_text = str(rewrite_result or "")
                    if action == "repair_coverage":
                        current["covered_source_terms"] = list(current.get("must_keep_terms") or [])
                        current["omitted_source_terms"] = []
                        current["coverage_ok"] = True
                current["text"] = new_text
                current["est_chars"] = len(new_text)
                current["rewrite_rounds"] = rewrite_round
                current["target_chars_range"] = new_range
                current["pending_tts_text"] = new_text
                current["active_tts_attempt"] = current["tts_regenerate_attempts"] + 1
                _emit_sentence_progress(on_progress, position=position, current=current, phase="tts_regen_start")
                current["tts_path"], current_duration = _regenerate_segment(
                    sentence=current,
                    voice_id=voice_id,
                    target_language=target_language,
                    suffix=_candidate_suffix("rewrite", rewrite_round),
                )
                current["tts_regenerate_attempts"] += 1
                current["tts_duration"] = current_duration
                status, speed = classify_overshoot(current["target_duration"], current_duration)
                if _semantic_coverage_issue(current):
                    status = "needs_semantic_repair"
                current["status"] = status
                current["speed"] = speed
                current["duration_ratio"] = duration_ratio(current["target_duration"], current_duration)
                attempt = {
                    "round": rewrite_round,
                    "text_attempt": current["text_rewrite_attempts"],
                    "tts_attempt": current["tts_regenerate_attempts"],
                    "temperature": rewrite_temperature,
                    "action": action,
                    "before_text": before_text,
                    "after_text": new_text,
                    "target_duration": current["target_duration"],
                    "tts_duration": current_duration,
                    "duration_ratio": round(current["duration_ratio"], 4),
                    "delta_pct": _delta_pct(current["target_duration"], current_duration),
                    "status": status,
                    "reason": _duration_reason(status),
                    "coverage_ok": current.get("coverage_ok", True),
                    "omitted_source_terms": list(current.get("omitted_source_terms") or []),
                    "selected": False,
                }
                current["attempts"].append(attempt)
                _emit_sentence_progress(on_progress, position=position, current=current, phase="rewrite_attempt")

                candidate = _candidate_from_current(current, round_number=rewrite_round)
                if _candidate_rank(candidate) <= _candidate_rank(best_candidate):
                    best_candidate = candidate

                if status == "ok":
                    current["selected_attempt_round"] = rewrite_round
                    _mark_selected_attempt(current["attempts"], rewrite_round)
                    break

            if current["status"] in {"needs_rewrite", "needs_expand", "needs_semantic_repair"}:
                _apply_candidate(current, best_candidate)
                _mark_selected_attempt(current["attempts"], current["selected_attempt_round"])
                current["status"] = _warning_status_for_current(current)
                current["speed"] = 1.0
                current["best_effort"] = True
                current["best_effort_reason"] = "max_attempts_exhausted"
                final_ratio = duration_ratio(current["target_duration"], current["tts_duration"])
                if MAX_DURATION_RATIO < final_ratio <= MAX_FFMPEG_TEMPO_RATIO:
                    _resolve_final_overlong_with_ffmpeg(
                        current=current,
                        position=position,
                        on_progress=on_progress,
                        reason="overlong_after_attempts",
                        ffmpeg_tempo_enabled=ffmpeg_tempo_enabled,
                    )
                elif final_ratio > MAX_FFMPEG_TEMPO_RATIO:
                    _mark_clip_overlong_fallback(
                        current=current,
                        position=position,
                        on_progress=on_progress,
                    )
                else:
                    _run_final_extra_expand(
                        current=current,
                        position=position,
                        voice_id=voice_id,
                        target_language=target_language,
                        av_inputs=av_inputs,
                        shot_notes=shot_notes,
                        script_segments=script_segments,
                        user_id=user_id,
                        project_id=project_id,
                        on_progress=on_progress,
                        ffmpeg_tempo_enabled=ffmpeg_tempo_enabled,
                    )

    _emit_sentence_progress(on_progress, position=position, current=current, phase="sentence_done")
    return current


def _sentence_worker_count(max_sentence_workers: int, sentence_count: int) -> int:
    try:
        requested = int(max_sentence_workers)
    except (TypeError, ValueError):
        requested = DEFAULT_SENTENCE_RECONCILE_WORKERS
    return max(1, min(requested, max(sentence_count, 1)))


def reconcile_duration(
    *,
    task,
    av_output: dict,
    tts_output: dict,
    voice_id: str,
    target_language: str,
    av_inputs: dict,
    shot_notes: dict,
    script_segments: list[dict],
    user_id: int | None = None,
    project_id: str | None = None,
    max_rewrite_rounds: int = MAX_TEXT_REWRITE_ATTEMPTS,
    max_tts_regenerate_attempts: int = MAX_TTS_REGENERATE_ATTEMPTS,
    on_progress: Callable[[dict], None] | None = None,
    max_sentence_workers: int = DEFAULT_SENTENCE_RECONCILE_WORKERS,
) -> list[dict]:
    tts_by_index = _tts_segment_map(tts_output)
    av_sentences = list((av_output or {}).get("sentences") or [])
    text_rewrite_enabled = _text_rewrite_enabled_for_task(task)
    ffmpeg_tempo_enabled = omni_ffmpeg_tempo_config.is_enabled()
    initial_states = [
        _initial_sentence_state(
            position=position,
            av_sentence=av_sentence,
            tts_by_index=tts_by_index,
            max_rewrite_rounds=max_rewrite_rounds,
            max_tts_regenerate_attempts=max_tts_regenerate_attempts,
        )
        for position, av_sentence in enumerate(av_sentences)
    ]

    for position, current in enumerate(initial_states):
        queued = dict(current)
        queued["status"] = "queued"
        _emit_sentence_progress(on_progress, position=position, current=queued, phase="queued")

    if not initial_states:
        return []

    worker_count = _sentence_worker_count(max_sentence_workers, len(initial_states))
    if worker_count == 1:
        return [
            _reconcile_one_sentence(
                position=position,
                current=current,
                text_rewrite_enabled=text_rewrite_enabled,
                voice_id=voice_id,
                target_language=target_language,
                av_inputs=av_inputs,
                shot_notes=shot_notes,
                script_segments=script_segments,
                user_id=user_id,
                project_id=project_id,
                max_rewrite_rounds=max_rewrite_rounds,
                max_tts_regenerate_attempts=max_tts_regenerate_attempts,
                ffmpeg_tempo_enabled=ffmpeg_tempo_enabled,
                on_progress=on_progress,
            )
            for position, current in enumerate(initial_states)
        ]

    progress_queue: Queue[dict] = Queue()

    def _queue_progress(record: dict) -> None:
        progress_queue.put(record)

    def _drain_progress() -> None:
        while True:
            try:
                record = progress_queue.get_nowait()
            except Empty:
                break
            if on_progress is not None:
                on_progress(record)

    final_by_position: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="sentence-reconcile") as executor:
        futures = {
            executor.submit(
                _reconcile_one_sentence,
                position=position,
                current=current,
                text_rewrite_enabled=text_rewrite_enabled,
                voice_id=voice_id,
                target_language=target_language,
                av_inputs=av_inputs,
                shot_notes=shot_notes,
                script_segments=script_segments,
                user_id=user_id,
                project_id=project_id,
                max_rewrite_rounds=max_rewrite_rounds,
                max_tts_regenerate_attempts=max_tts_regenerate_attempts,
                ffmpeg_tempo_enabled=ffmpeg_tempo_enabled,
                on_progress=_queue_progress,
            ): position
            for position, current in enumerate(initial_states)
        }
        pending = set(futures)
        while pending:
            _drain_progress()
            done, pending = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
            for future in done:
                position = futures[future]
                try:
                    final_by_position[position] = future.result()
                except Exception:
                    for pending_future in pending:
                        pending_future.cancel()
                    _drain_progress()
                    raise
        _drain_progress()

    return [final_by_position[position] for position in range(len(initial_states))]
