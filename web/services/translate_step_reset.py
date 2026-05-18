"""Shared current-and-downstream reset helpers for translate workbenches."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any


_ASR_POST_STEPS = {"asr_clean", "asr_normalize"}

_PREVIEW_KEYS_BY_STEP: dict[str, set[str]] = {
    "extract": {"audio_extract"},
    "separate": {"separation_vocals", "separation_accompaniment"},
    "tts": {"tts_full_audio"},
    "subtitle": {"srt"},
    "compose": {"soft_video", "hard_video"},
}

_TOP_DEFAULTS_BY_STEP: dict[str, dict[str, Any]] = {
    "extract": {
        "audio_path": "",
        "separation_audio_path": "",
        "video_duration": None,
    },
    "asr": {
        "utterances": [],
        "source_full_text": "",
        "source_full_text_zh": "",
    },
    "separate": {
        "separation": None,
    },
    "asr_clean": {
        "utterances_raw": None,
    },
    "asr_normalize": {
        "utterances_en": None,
        "asr_normalize_artifact": None,
        "detected_source_language": None,
    },
    "voice_match": {
        "selected_voice_id": None,
        "selected_voice_name": None,
        "recommended_voice_id": None,
        "voice_id": None,
        "voice_match_candidates": [],
        "voice_match_fallback_voice_id": None,
        "voice_match_query_embedding": None,
    },
    "alignment": {
        "alignment": {},
        "script_segments": [],
        "segments": [],
        "_alignment_confirmed": False,
    },
    "shot_decompose": {
        "shots": [],
    },
    "translate": {
        "source_full_text_zh": "",
        "localized_translation": {},
        "translations": [],
        "_segments_confirmed": False,
        "_translate_pre_select": False,
        "evals_invalidated_at": None,
    },
    "tts": {
        "segments": [],
        "tts_script": {},
        "tts_audio_path": "",
        "timeline_manifest": {},
        "tts_duration_rounds": [],
        "tts_duration_status": None,
        "tts_final_round": None,
        "tts_final_reason": None,
        "tts_final_distance": None,
        "final_compose_summary": {},
        "speech_shot_alignment": {},
        "av_debug": {},
    },
    "av_sync_audit": {
        "av_debug": {},
    },
    "subtitle": {
        "english_asr_result": {},
        "corrected_subtitle": {},
        "srt_path": "",
    },
    "compose": {
        "result": {},
        "final_compose_summary": {},
    },
    "export": {
        "exports": {},
    },
}

_VARIANT_DEFAULTS_BY_STEP: dict[str, dict[str, Any]] = {
    "translate": {
        "localized_translation": {},
        "translations": [],
    },
    "tts": {
        "segments": [],
        "tts_script": {},
        "tts_result": {},
        "tts_audio_path": "",
        "timeline_manifest": {},
        "voice_id": None,
        "selected_voice_id": None,
        "final_compose_summary": {},
    },
    "av_sync_audit": {
        "av_sync_audit": {},
    },
    "subtitle": {
        "english_asr_result": {},
        "corrected_subtitle": {},
        "srt_path": "",
    },
    "compose": {
        "result": {},
        "final_compose_summary": {},
    },
    "export": {
        "exports": {},
    },
}


def reset_step_names(step_names: Sequence[str], start_step: str) -> list[str]:
    idx = list(step_names).index(start_step)
    steps = list(step_names)[idx:]
    if start_step in _ASR_POST_STEPS:
        steps = ["asr_clean", "asr_normalize"] + [
            step for step in steps
            if step not in _ASR_POST_STEPS
        ]
    return steps


def build_step_resume_reset_updates(
    task: Mapping[str, Any],
    step_names: Sequence[str],
    start_step: str,
) -> dict[str, Any]:
    steps_to_reset = reset_step_names(step_names, start_step)
    reset_set = set(steps_to_reset)
    preview_keys = _preview_keys_for_steps(reset_set)

    updates: dict[str, Any] = {
        "status": "running",
        "error": "",
        "current_review_step": "",
        "artifacts": _drop_keys(task.get("artifacts"), reset_set),
        "preview_files": _drop_keys(task.get("preview_files"), preview_keys),
        "llm_debug_refs": _drop_keys(task.get("llm_debug_refs"), reset_set),
        "step_model_tags": _drop_keys(task.get("step_model_tags"), reset_set),
        "variants": _reset_variants(task.get("variants"), reset_set, preview_keys),
    }

    for step in steps_to_reset:
        for key, default in _TOP_DEFAULTS_BY_STEP.get(step, {}).items():
            updates[key] = copy.deepcopy(default)

    if "asr_clean" in reset_set and task.get("utterances_raw"):
        updates["utterances"] = copy.deepcopy(task.get("utterances_raw"))

    if "alignment" not in reset_set and "translate" in reset_set:
        updates["segments"] = copy.deepcopy(task.get("script_segments") or [])

    if "loudness_match" in reset_set or "compose" in reset_set:
        separation = _reset_separation(task.get("separation"), loudness="loudness_match" in reset_set)
        if separation is not _UNCHANGED:
            updates["separation"] = separation

    return updates


def _preview_keys_for_steps(steps: set[str]) -> set[str]:
    keys: set[str] = set()
    for step in steps:
        keys.update(_PREVIEW_KEYS_BY_STEP.get(step, set()))
    return keys


def _drop_keys(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): copy.deepcopy(item)
        for key, item in value.items()
        if str(key) not in keys
    }


def _reset_variants(value: Any, reset_steps: set[str], preview_keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    variants: dict[str, Any] = {}
    for name, raw_state in value.items():
        if not isinstance(raw_state, Mapping):
            variants[str(name)] = copy.deepcopy(raw_state)
            continue
        state = copy.deepcopy(dict(raw_state))
        state["artifacts"] = _drop_keys(state.get("artifacts"), reset_steps)
        state["preview_files"] = _drop_keys(state.get("preview_files"), preview_keys)
        for step in reset_steps:
            for key, default in _VARIANT_DEFAULTS_BY_STEP.get(step, {}).items():
                state[key] = copy.deepcopy(default)
        variants[str(name)] = state
    return variants


class _Unchanged:
    pass


_UNCHANGED = _Unchanged()


def _reset_separation(value: Any, *, loudness: bool) -> Any:
    if not isinstance(value, Mapping):
        return _UNCHANGED
    separation = copy.deepcopy(dict(value))
    for key in (
        "composite_audio_path",
        "cleaned_accompaniment_path",
        "effective_background_volume",
    ):
        separation.pop(key, None)
    if loudness:
        for key in (
            "tts_loudness",
            "background_volume",
            "background_boost",
            "manual_boost",
            "background_suppression",
            "background_cleanup",
            "accompaniment_lufs",
        ):
            separation.pop(key, None)
    return separation
