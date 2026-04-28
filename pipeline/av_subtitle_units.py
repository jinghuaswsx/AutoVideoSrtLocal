from __future__ import annotations

from typing import Iterable


OK_STATUSES = {"ok", "speed_adjusted", "rewritten_ok"}
BOUNDARY_ROLES = {"hook", "demo", "proof", "cta"}


def _clean_text(value) -> str:
    return str(value or "").strip()


def _sentence_duration(sentence: dict, key: str) -> float:
    return float(sentence.get(key, 0.0) or 0.0)


def _sentence_role(sentence: dict) -> str:
    role = _clean_text(sentence.get("role_in_structure")).lower()
    return role if role else "unknown"


def _unit_status(sentences: Iterable[dict]) -> str:
    for sentence in sentences:
        if _clean_text(sentence.get("status")) not in OK_STATUSES:
            return "needs_review"
    return "ok"


def _unit_role(sentences: list[dict]) -> str:
    roles = [_sentence_role(sentence) for sentence in sentences]
    meaningful = [role for role in roles if role != "unknown"]
    if not meaningful:
        return "unknown"
    first = meaningful[0]
    if all(role == first for role in meaningful):
        return first
    return "mixed"


def _make_unit(unit_index: int, sentences: list[dict], start_time: float) -> dict:
    tts_duration = round(sum(_sentence_duration(sentence, "tts_duration") for sentence in sentences), 3)
    target_duration = round(sum(_sentence_duration(sentence, "target_duration") for sentence in sentences), 3)
    end_time = round(start_time + tts_duration, 3)
    return {
        "unit_index": unit_index,
        "sentence_indices": [int(sentence.get("_sentence_index", index)) for index, sentence in enumerate(sentences)],
        "asr_indices": [int(sentence.get("asr_index", index)) for index, sentence in enumerate(sentences)],
        "start_time": round(start_time, 3),
        "end_time": end_time,
        "target_duration": target_duration,
        "tts_duration": tts_duration,
        "text": " ".join(_clean_text(sentence.get("text")) for sentence in sentences if _clean_text(sentence.get("text"))).strip(),
        "source_text": " ".join(
            _clean_text(sentence.get("source_text") or sentence.get("original_text"))
            for sentence in sentences
            if _clean_text(sentence.get("source_text") or sentence.get("original_text"))
        ).strip(),
        "unit_role": _unit_role(sentences),
        "status": _unit_status(sentences),
    }


def _should_start_new_unit(
    current: list[dict],
    next_sentence: dict,
    *,
    max_unit_duration: float,
    max_unit_chars: int,
) -> bool:
    if not current:
        return False

    current_duration = sum(_sentence_duration(sentence, "tts_duration") for sentence in current)
    next_duration = _sentence_duration(next_sentence, "tts_duration")
    if current_duration + next_duration > max_unit_duration:
        return True

    current_text = " ".join(_clean_text(sentence.get("text")) for sentence in current if _clean_text(sentence.get("text")))
    next_text = _clean_text(next_sentence.get("text"))
    if len((current_text + " " + next_text).strip()) > max_unit_chars:
        return True

    previous_role = _sentence_role(current[-1])
    next_role = _sentence_role(next_sentence)
    if previous_role in BOUNDARY_ROLES and next_role in BOUNDARY_ROLES and previous_role != next_role:
        return True

    previous_end = _sentence_duration(current[-1], "end_time")
    next_start = _sentence_duration(next_sentence, "start_time")
    if previous_end > 0 and next_start > 0 and next_start - previous_end >= 0.45:
        return True

    return False


def build_subtitle_units_from_sentences(
    sentences: list[dict],
    *,
    mode: str = "hybrid",
    max_unit_duration: float = 3.2,
    max_unit_chars: int = 72,
) -> list[dict]:
    normalized_mode = mode if mode in {"sentence", "hybrid"} else "hybrid"
    ordered = [
        {**sentence, "_sentence_index": index}
        for index, sentence in enumerate(sentences or [])
        if isinstance(sentence, dict)
    ]
    units: list[dict] = []
    current: list[dict] = []
    current_start = 0.0

    for sentence in ordered:
        if normalized_mode == "sentence" or _should_start_new_unit(
            current,
            sentence,
            max_unit_duration=max_unit_duration,
            max_unit_chars=max_unit_chars,
        ):
            if current:
                unit = _make_unit(len(units), current, current_start)
                units.append(unit)
                current_start = unit["end_time"]
            current = []
        current.append(sentence)

    if current:
        units.append(_make_unit(len(units), current, current_start))

    return units
