from __future__ import annotations

from collections import defaultdict
import math
from typing import Iterable

REVIEW_LOW_CONFIDENCE = "low_speaker_confidence"
REVIEW_OVERLAP = "speaker_overlap"
REVIEW_EXTRA_SPEAKER = "unsupported_extra_speaker"

MIN_PROVIDER_COVERAGE = 0.90
MIN_JOIN_OVERLAP_RATIO = 0.60
MIN_SPEAKER_CONFIDENCE = 0.70

_SPEAKER_KEYS = ("speaker_id", "speaker", "speaker_label", "channel_tag")
_CONFIDENCE_KEYS = ("speaker_confidence", "confidence", "speaker_score")


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _safe_index(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _duration(item: dict) -> float:
    return max(0.0, _safe_float(item.get("end_time")) - _safe_float(item.get("start_time")))


def _speaker_label(item: dict) -> str:
    for key in _SPEAKER_KEYS:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _confidence(item: dict, default: float = 1.0) -> float:
    for key in _CONFIDENCE_KEYS:
        if item.get(key) is None:
            continue
        try:
            value = float(item[key])
        except (TypeError, ValueError):
            return 0.0
        return value if math.isfinite(value) else 0.0
    return default


def _speaker_rank(labels_by_segment: list[str], utterances: list[dict]) -> list[str]:
    durations: dict[str, float] = defaultdict(float)
    first_seen: dict[str, int] = {}
    for index, label in enumerate(labels_by_segment):
        if not label:
            continue
        durations[label] += _duration(utterances[index])
        first_seen.setdefault(label, index)
    return sorted(durations.keys(), key=lambda label: (-durations[label], first_seen[label]))


def _speaker_map(labels_by_segment: list[str], utterances: list[dict]) -> dict[str, str]:
    ranked = _speaker_rank(labels_by_segment, utterances)
    mapping: dict[str, str] = {}
    if ranked:
        mapping[ranked[0]] = "A"
    if len(ranked) >= 2:
        mapping[ranked[1]] = "B"
    for label in ranked[2:]:
        mapping[label] = "B"
    return mapping


def _review_reason(*reasons: str) -> str:
    return ",".join(reason for reason in reasons if reason)


def _empty_summary() -> dict:
    return {
        "A": {"segment_count": 0, "duration": 0.0},
        "B": {"segment_count": 0, "duration": 0.0},
    }


def _summary(segments: Iterable[dict]) -> dict:
    summary = _empty_summary()
    for segment in segments:
        speaker = segment.get("speaker_id")
        if speaker not in summary:
            continue
        summary[speaker]["segment_count"] += 1
        summary[speaker]["duration"] = round(summary[speaker]["duration"] + _duration(segment), 3)
    return summary


def _review_index_payload(segments: list[dict]) -> list[dict]:
    return [
        {"index": segment["index"], "reason": segment["review_reason"]}
        for segment in segments
        if segment.get("review_required")
    ]


def build_dialogue_segments(utterances: list[dict]) -> dict:
    labels = [_speaker_label(item) for item in utterances]
    coverage = (sum(1 for label in labels if label) / max(1, len(labels))) if utterances else 0.0
    if coverage < MIN_PROVIDER_COVERAGE:
        return {
            "speaker_strategy": "needs_diarization",
            "dialogue_segments": [],
            "speaker_summary": _empty_summary(),
            "review_required_segments": [],
            "dialogue_warnings": ["asr_provider_speaker_coverage_below_threshold"],
        }

    mapping = _speaker_map(labels, utterances)
    primary_labels = set(_speaker_rank(labels, utterances)[:2])
    segments: list[dict] = []
    for index, utterance in enumerate(utterances):
        raw_label = labels[index]
        confidence = _confidence(utterance)
        extra_speaker = bool(raw_label) and raw_label not in primary_labels
        low_confidence = not raw_label or confidence < MIN_SPEAKER_CONFIDENCE
        reasons = []
        if extra_speaker:
            reasons.append(REVIEW_EXTRA_SPEAKER)
        if low_confidence:
            reasons.append(REVIEW_LOW_CONFIDENCE)
        segment = {
            "index": _safe_index(utterance.get("index"), index),
            "text": utterance.get("text", ""),
            "start_time": _safe_float(utterance.get("start_time")),
            "end_time": _safe_float(utterance.get("end_time")),
            "speaker_id": mapping.get(raw_label, "A"),
            "raw_speaker_id": raw_label,
            "speaker_confidence": confidence,
            "speaker_source": "asr_provider",
            "overlap": False,
            "review_required": bool(reasons),
            "review_reason": _review_reason(*reasons),
        }
        segments.append(segment)

    return {
        "speaker_strategy": "asr_provider",
        "dialogue_segments": segments,
        "speaker_summary": _summary(segments),
        "review_required_segments": _review_index_payload(segments),
        "dialogue_warnings": ["asr_provider_more_than_two_speakers"] if len({label for label in labels if label}) > 2 else [],
    }


def _overlap_seconds(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def join_diarization_to_utterances(utterances: list[dict], diarization_segments: list[dict]) -> dict:
    diar_labels = [_speaker_label(item) for item in diarization_segments]
    mapping = _speaker_map(diar_labels, diarization_segments)
    primary_labels = set(_speaker_rank(diar_labels, diarization_segments)[:2])
    segments: list[dict] = []
    for index, utterance in enumerate(utterances):
        u_start = _safe_float(utterance.get("start_time"))
        u_end = _safe_float(utterance.get("end_time"))
        u_duration = max(0.001, u_end - u_start)
        by_label: dict[str, float] = defaultdict(float)
        confidence_by_label: dict[str, float] = defaultdict(float)
        for diar in diarization_segments:
            label = _speaker_label(diar)
            overlap = _overlap_seconds(
                u_start,
                u_end,
                _safe_float(diar.get("start_time")),
                _safe_float(diar.get("end_time")),
            )
            if overlap <= 0:
                continue
            by_label[label] += overlap
            confidence_by_label[label] = max(confidence_by_label[label], _confidence(diar, default=0.0))
        ranked = sorted(by_label.items(), key=lambda item: item[1], reverse=True)
        raw_label = ranked[0][0] if ranked else ""
        overlap_ratio = (ranked[0][1] / u_duration) if ranked else 0.0
        assigned_confidence = confidence_by_label[raw_label] if raw_label else 0.0
        overlap = len([item for item in ranked if item[1] > 0]) > 1
        extra_speaker = bool(raw_label) and raw_label not in primary_labels
        reasons = []
        if extra_speaker:
            reasons.append(REVIEW_EXTRA_SPEAKER)
        if overlap_ratio < MIN_JOIN_OVERLAP_RATIO or assigned_confidence < MIN_SPEAKER_CONFIDENCE:
            reasons.append(REVIEW_LOW_CONFIDENCE)
        if overlap:
            reasons.append(REVIEW_OVERLAP)
        segment = {
            "index": _safe_index(utterance.get("index"), index),
            "text": utterance.get("text", ""),
            "start_time": u_start,
            "end_time": u_end,
            "speaker_id": mapping.get(raw_label, "A"),
            "raw_speaker_id": raw_label,
            "speaker_confidence": round(max(0.0, min(1.0, overlap_ratio * assigned_confidence)), 4),
            "speaker_source": "diarization",
            "overlap": overlap,
            "review_required": bool(reasons),
            "review_reason": _review_reason(*reasons),
        }
        segments.append(segment)
    return {
        "speaker_strategy": "diarization",
        "dialogue_segments": segments,
        "speaker_summary": _summary(segments),
        "review_required_segments": _review_index_payload(segments),
        "dialogue_warnings": [],
    }
