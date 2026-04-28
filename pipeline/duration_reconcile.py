from __future__ import annotations

from typing import Any

from pipeline import av_translate, tts

MIN_DURATION_RATIO = 0.95
MAX_DURATION_RATIO = 1.05
MIN_TTS_SPEED = 0.95
MAX_TTS_SPEED = 1.05


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
    if status == "needs_rewrite":
        return "above_duration_ratio"
    if status == "needs_expand":
        return "below_duration_ratio"
    return status


def _preserve_sentence_fields(current: dict, av_sentence: dict) -> None:
    for key, value in av_sentence.items():
        if key in current:
            continue
        if key.startswith("source") or key.startswith("localization"):
            current[key] = value


def _regenerate_segment(
    *,
    sentence: dict,
    voice_id: str,
    target_language: str,
    speed: float | None = None,
) -> tuple[str, float]:
    output_path = sentence.get("tts_path") or f"av_seg_{sentence['asr_index']}.mp3"
    tts.generate_segment_audio(
        text=sentence["text"],
        voice_id=voice_id,
        output_path=output_path,
        language_code=target_language,
        speed=speed,
    )
    return output_path, tts.get_audio_duration(output_path)


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
    max_rewrite_rounds: int = 2,
) -> list[dict]:
    tts_by_index = _tts_segment_map(tts_output)
    final_sentences = []

    for position, av_sentence in enumerate((av_output or {}).get("sentences") or []):
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
            "status": "ok",
            "duration_ratio": duration_ratio(
                float(av_sentence.get("target_duration", 0.0) or 0.0),
                float(tts_segment.get("tts_duration", 0.0) or 0.0),
            ),
            "attempts": [],
        }
        _preserve_sentence_fields(current, av_sentence)

        status, speed = classify_overshoot(current["target_duration"], current["tts_duration"])
        current["status"] = status
        current["speed"] = speed
        current["duration_ratio"] = duration_ratio(current["target_duration"], current["tts_duration"])

        if status == "ok":
            speed_for_target = compute_speed_for_target(current["target_duration"], current["tts_duration"])
            if speed_for_target is not None and speed_for_target != 1.0:
                current["tts_path"], current["tts_duration"] = _regenerate_segment(
                    sentence=current,
                    voice_id=voice_id,
                    target_language=target_language,
                    speed=speed_for_target,
                )
                current["speed"] = speed_for_target
                current["status"] = "speed_adjusted"
                current["duration_ratio"] = duration_ratio(current["target_duration"], current["tts_duration"])
        elif status in {"needs_rewrite", "needs_expand"}:
            current_duration = current["tts_duration"]
            action = "shorten" if status == "needs_rewrite" else "expand"
            for rewrite_round in range(1, max_rewrite_rounds + 1):
                before_text = current["text"]
                new_range = _scaled_target_chars_range(
                    current["target_chars_range"],
                    current["target_duration"],
                    current_duration,
                )
                new_text = av_translate.rewrite_one(
                    asr_index=asr_index,
                    prev_text=before_text,
                    overshoot_sec=max(0.0, current_duration - current["target_duration"]),
                    new_target_chars_range=new_range,
                    script_segments=script_segments,
                    shot_notes=shot_notes,
                    av_inputs=av_inputs,
                    voice_id=voice_id,
                    user_id=user_id,
                    project_id=project_id,
                )
                current["text"] = new_text
                current["est_chars"] = len(new_text)
                current["rewrite_rounds"] = rewrite_round
                current["target_chars_range"] = new_range
                current["tts_path"], current_duration = _regenerate_segment(
                    sentence=current,
                    voice_id=voice_id,
                    target_language=target_language,
                )
                current["tts_duration"] = current_duration
                status, speed = classify_overshoot(current["target_duration"], current_duration)
                current["status"] = status
                current["speed"] = speed
                current["duration_ratio"] = duration_ratio(current["target_duration"], current_duration)
                current["attempts"].append(
                    {
                        "round": rewrite_round,
                        "action": action,
                        "before_text": before_text,
                        "after_text": new_text,
                        "target_duration": current["target_duration"],
                        "tts_duration": current_duration,
                        "duration_ratio": round(current["duration_ratio"], 4),
                        "status": status,
                        "reason": _duration_reason(status),
                    }
                )

                if status == "ok":
                    speed_for_target = compute_speed_for_target(current["target_duration"], current_duration)
                    if speed_for_target is not None and speed_for_target != 1.0:
                        current["tts_path"], current["tts_duration"] = _regenerate_segment(
                            sentence=current,
                            voice_id=voice_id,
                            target_language=target_language,
                            speed=speed_for_target,
                        )
                        current["speed"] = speed_for_target
                        current["status"] = "speed_adjusted"
                        current["duration_ratio"] = duration_ratio(
                            current["target_duration"], current["tts_duration"]
                        )
                    break

            if current["status"] in {"needs_rewrite", "needs_expand"}:
                current["status"] = "warning_long" if action == "shorten" else "warning_short"
                current["speed"] = 1.0

        final_sentences.append(current)

    return final_sentences
