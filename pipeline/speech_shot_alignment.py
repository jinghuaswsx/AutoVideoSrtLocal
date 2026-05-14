from __future__ import annotations

from collections import Counter
from typing import Any


DEFAULT_HARD_FINAL_GAP = 0.30
DEFAULT_HOOK_SECONDS = 3.0
DEFAULT_HOOK_EXTRA_CAP = 0.12
_DEDUP_TOLERANCE = 0.05


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round(value: float) -> float:
    return round(float(value), 3)


def normalize_shot_cut_anchors(
    *,
    shots: list[dict] | None,
    scene_cuts: list[Any] | None,
    video_duration: float | None,
) -> list[float]:
    duration = _float_value(video_duration, 0.0)
    raw: list[float] = []

    for shot in shots or []:
        if not isinstance(shot, dict):
            continue
        for key in ("start", "end"):
            value = _float_value(shot.get(key), 0.0)
            if value > 0:
                raw.append(value)

    for cut in scene_cuts or []:
        if isinstance(cut, dict):
            value = _float_value(cut.get("time", cut.get("at", cut.get("start"))), 0.0)
        else:
            value = _float_value(cut, 0.0)
        if value > 0:
            raw.append(value)

    out: list[float] = []
    upper = duration - 0.2 if duration > 0 else None
    for value in sorted(raw):
        if value <= 0.2:
            continue
        if upper is not None and value >= upper:
            continue
        if out and abs(value - out[-1]) <= _DEDUP_TOLERANCE:
            continue
        out.append(_round(value))
    return out


def _content_end(sentences: list[dict]) -> float:
    return max(
        (
            _float_value(sentence.get("audio_end_time"), 0.0)
            for sentence in sentences
            if isinstance(sentence, dict)
        ),
        default=0.0,
    )


def _shift_following(sentences: list[dict], start_index: int, delta: float) -> None:
    for index in range(start_index, len(sentences)):
        sentence = sentences[index]
        sentence["audio_start_time"] = _round(_float_value(sentence.get("audio_start_time"), 0.0) + delta)
        sentence["audio_end_time"] = _round(_float_value(sentence.get("audio_end_time"), 0.0) + delta)


def _base_summary(
    *,
    status: str,
    enabled: bool,
    anchors: list[float],
    boundary_count: int,
    budget: float,
    decisions: list[dict] | None = None,
    skip_reasons: Counter | None = None,
) -> dict:
    decisions = decisions or []
    applied = [row for row in decisions if row.get("decision") == "applied"]
    extra_total = sum(_float_value(row.get("extra_silence"), 0.0) for row in applied)
    return {
        "speech_shot_alignment_enabled": bool(enabled),
        "speech_shot_alignment_applied": bool(applied),
        "speech_shot_alignment_status": status,
        "speech_shot_alignment_analyzed_boundaries": int(boundary_count),
        "speech_shot_alignment_decisions": decisions,
        "shot_anchor_cut_count": len(anchors),
        "shot_anchor_extra_silence_total": _round(extra_total),
        "shot_anchor_aligned_boundary_count": len(applied),
        "shot_anchor_extra_silence_budget": _round(budget),
        "shot_anchor_skip_reasons": dict(skip_reasons or {}),
    }


def apply_speech_shot_alignment(
    sentences: list[dict],
    *,
    shots: list[dict] | None,
    scene_cuts: list[Any] | None,
    video_duration: float | None,
    hard_final_gap_cap: float = DEFAULT_HARD_FINAL_GAP,
    hook_seconds: float = DEFAULT_HOOK_SECONDS,
    hook_extra_cap: float = DEFAULT_HOOK_EXTRA_CAP,
) -> tuple[list[dict], dict]:
    scheduled = [dict(sentence) for sentence in sentences or [] if isinstance(sentence, dict)]
    boundary_count = max(0, len(scheduled) - 1)
    duration = _float_value(video_duration, 0.0)
    extra_budget = min(1.5, duration * 0.05) if duration > 0 else 1.5
    if duration > 0 and duration < 20.0:
        extra_budget = min(extra_budget, 1.0)

    anchors = normalize_shot_cut_anchors(
        shots=shots,
        scene_cuts=scene_cuts,
        video_duration=duration,
    )

    if not scheduled:
        return scheduled, _base_summary(
            status="skipped_no_sentences",
            enabled=False,
            anchors=anchors,
            boundary_count=0,
            budget=extra_budget,
        )
    if duration > 0 and _content_end(scheduled) > duration + 0.001:
        return scheduled, _base_summary(
            status="skipped_content_over_video",
            enabled=False,
            anchors=anchors,
            boundary_count=boundary_count,
            budget=extra_budget,
            skip_reasons=Counter({"final_speech_exceeds_video": 1}),
        )
    if not anchors:
        return scheduled, _base_summary(
            status="skipped_no_anchors",
            enabled=False,
            anchors=[],
            boundary_count=boundary_count,
            budget=extra_budget,
        )

    decisions: list[dict] = []
    skip_reasons: Counter = Counter()
    used_extra = 0.0

    for index in range(1, len(scheduled)):
        prev = scheduled[index - 1]
        sentence = scheduled[index]
        prev_end = _float_value(prev.get("audio_end_time"), 0.0)
        current_start = _float_value(sentence.get("audio_start_time"), 0.0)
        base_gap = max(0.0, _float_value(sentence.get("audio_gap_before"), 0.0))
        candidates = [
            cut for cut in anchors
            if current_start < cut <= current_start + hard_final_gap_cap
        ]
        if not candidates:
            skip_reasons["too_far_from_cut"] += 1
            decisions.append({
                "sentence_index": index,
                "asr_index": sentence.get("asr_index", sentence.get("index", index)),
                "decision": "skipped",
                "reason": "too_far_from_cut",
                "base_compact_gap": _round(base_gap),
                "final_gap": _round(base_gap),
            })
            continue

        cut = min(candidates, key=lambda value: (value - current_start, abs(value - current_start)))
        target_gap = max(0.0, cut - prev_end)
        extra = target_gap - base_gap
        if extra <= 0.001:
            skip_reasons["no_extra_needed"] += 1
            continue
        if target_gap > hard_final_gap_cap + 0.0005:
            skip_reasons["would_exceed_final_gap_cap"] += 1
            decisions.append({
                "sentence_index": index,
                "asr_index": sentence.get("asr_index", sentence.get("index", index)),
                "decision": "skipped",
                "reason": "would_exceed_final_gap_cap",
                "cut_time": _round(cut),
                "base_compact_gap": _round(base_gap),
                "required_final_gap": _round(target_gap),
            })
            continue
        if current_start < hook_seconds and extra > hook_extra_cap + 0.0005:
            skip_reasons["hook_protection"] += 1
            decisions.append({
                "sentence_index": index,
                "asr_index": sentence.get("asr_index", sentence.get("index", index)),
                "decision": "skipped",
                "reason": "hook_protection",
                "cut_time": _round(cut),
                "base_compact_gap": _round(base_gap),
                "required_final_gap": _round(target_gap),
                "extra_silence": _round(extra),
            })
            continue
        if used_extra + extra > extra_budget + 0.0005:
            skip_reasons["over_budget"] += 1
            continue
        if duration > 0 and _content_end(scheduled) + extra > duration + 0.001:
            skip_reasons["would_overrun_video"] += 1
            continue

        before_start = current_start
        sentence["base_compact_gap"] = _round(base_gap)
        sentence["audio_gap_before"] = _round(target_gap)
        sentence["shot_anchor_final_gap"] = _round(target_gap)
        sentence["shot_anchor_extra_silence"] = _round(extra)
        sentence["shot_anchor_cut_time"] = _round(cut)
        sentence["shot_anchor_before_start"] = _round(before_start)
        sentence["shot_anchor_after_start"] = _round(cut)
        sentence["shot_anchor_reason"] = "nearby_cut_soft_snap"
        _shift_following(scheduled, index, extra)
        used_extra += extra
        decisions.append({
            "sentence_index": index,
            "asr_index": sentence.get("asr_index", sentence.get("index", index)),
            "decision": "applied",
            "reason": "nearby_cut_soft_snap",
            "cut_time": _round(cut),
            "base_compact_gap": _round(base_gap),
            "final_gap": _round(target_gap),
            "extra_silence": _round(extra),
            "before_start": _round(before_start),
            "after_start": _round(cut),
        })

    status = "optimized" if any(row.get("decision") == "applied" for row in decisions) else "no_op"
    summary = _base_summary(
        status=status,
        enabled=True,
        anchors=anchors,
        boundary_count=boundary_count,
        budget=extra_budget,
        decisions=decisions,
        skip_reasons=skip_reasons,
    )
    summary["speech_shot_alignment_max_final_gap"] = _round(
        max((_float_value(row.get("audio_gap_before"), 0.0) for row in scheduled), default=0.0)
    )
    return scheduled, summary
