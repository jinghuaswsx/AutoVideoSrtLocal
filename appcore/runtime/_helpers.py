"""Pure helper functions for ``appcore.runtime``.

由 ``appcore.runtime`` package 在 PR 3.2 抽出；函数体逐字符保留，行为不变。
``__init__.py`` 通过显式 re-export 让 ``runtime_de/fr/ja/multi/omni/v2`` 等
子类仍能 ``from appcore.runtime import _av_target_lang, _resolve_translate_provider, ...``。
"""
from __future__ import annotations

import json
import logging
import math
import os
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from appcore import ai_billing


def _skip_legacy_artifact_upload(task: dict, task_id: str) -> None:
    """Compatibility shim for legacy object-storage metadata.

    New tasks keep generated artifacts in local storage. Historical metadata
    remains readable through download routes, but runtime no longer uploads
    final outputs to object storage by default.
    """
    return


def _save_json(task_dir: str, filename: str, data) -> None:
    path = os.path.join(task_dir, filename)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def _count_visible_chars(text: str) -> int:
    return sum(1 for ch in str(text or "") if not ch.isspace())


_SHORT_ASR_PASSTHROUGH_CHAR_THRESHOLD = 50


def _join_utterance_text(utterances: list[dict]) -> str:
    return " ".join(
        str(item.get("text") or "").strip()
        for item in (utterances or [])
        if str(item.get("text") or "").strip()
    ).strip()


def _resolve_original_video_passthrough(utterances: list[dict]) -> dict:
    source_full_text = _join_utterance_text(utterances)
    source_chars = _count_visible_chars(source_full_text)
    if not utterances:
        return {
            "enabled": True,
            "reason": "no_asr",
            "source_full_text": source_full_text,
            "source_chars": source_chars,
        }
    if source_chars < _SHORT_ASR_PASSTHROUGH_CHAR_THRESHOLD:
        return {
            "enabled": True,
            "reason": "short_asr",
            "source_full_text": source_full_text,
            "source_chars": source_chars,
        }
    return {
        "enabled": False,
        "reason": "",
        "source_full_text": source_full_text,
        "source_chars": source_chars,
    }


def _is_original_video_passthrough(task: dict | None) -> bool:
    return str((task or {}).get("media_passthrough_mode") or "") == "original_video"


def _build_review_segments(script_segments: list[dict], localized_translation: dict) -> list[dict]:
    review_segments: list[dict] = []
    sentences = localized_translation.get("sentences", []) or []

    for fallback_index, sentence in enumerate(sentences):
        indices = sentence.get("source_segment_indices") or [fallback_index]
        source_segments = [
            script_segments[index]
            for index in indices
            if 0 <= index < len(script_segments)
        ]
        base_segment = source_segments[0] if source_segments else (
            script_segments[fallback_index] if fallback_index < len(script_segments) else {}
        )
        review_segments.append(
            {
                "index": sentence.get("index", fallback_index),
                "text": " ".join(
                    segment.get("text", "").strip()
                    for segment in source_segments
                    if segment.get("text")
                ).strip() or base_segment.get("text", ""),
                "translated": sentence.get("text", ""),
                "start_time": source_segments[0].get("start_time") if source_segments else base_segment.get("start_time"),
                "end_time": source_segments[-1].get("end_time") if source_segments else base_segment.get("end_time"),
                "source_segment_indices": indices,
            }
        )

    return review_segments


def _translate_billing_provider(provider: str) -> str:
    if "." in provider:
        try:
            from appcore import llm_bindings

            binding = llm_bindings.resolve(provider)
            return binding.get("provider") or provider
        except Exception:
            try:
                from appcore.llm_use_cases import get_use_case

                return get_use_case(provider)["default_provider"]
            except Exception:
                return provider
    if provider in {"openrouter", "doubao", "gemini_vertex", "gemini_vertex_adc", "gemini_aistudio"}:
        return provider
    if provider == "doubao":
        return "doubao"
    if provider.startswith("vertex_adc_"):
        return "gemini_aistudio"
    if provider.startswith("vertex_"):
        return "gemini_vertex"
    return "openrouter"


def _translate_billing_model(provider: str, user_id: int | None) -> str:
    if "." in provider:
        try:
            from appcore import llm_bindings

            binding = llm_bindings.resolve(provider)
            return binding.get("model") or provider
        except Exception:
            try:
                from appcore.llm_use_cases import get_use_case

                return get_use_case(provider)["default_model"]
            except Exception:
                return provider
    from pipeline.translate import get_model_display_name

    return get_model_display_name(provider, user_id)


def _log_translate_billing(
    *,
    user_id: int | None,
    project_id: str,
    use_case_code: str,
    provider: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    success: bool = True,
    extra: dict | None = None,
    request_payload: dict | None = None,
    response_payload: dict | None = None,
) -> None:
    ai_billing.log_request(
        use_case_code=use_case_code,
        user_id=user_id,
        project_id=project_id,
        provider=_translate_billing_provider(provider),
        model=_translate_billing_model(provider, user_id),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        units_type="tokens",
        success=success,
        extra=extra,
        request_payload=request_payload,
        response_payload=response_payload,
    )


def _llm_request_payload(
    result: dict | None,
    provider: str,
    use_case_code: str,
    messages: list[dict] | None = None,
) -> dict | None:
    messages = messages if messages is not None else (result or {}).get("_messages")
    if not messages:
        return None
    return {
        "type": "chat",
        "use_case_code": use_case_code,
        "provider": provider,
        "messages": messages,
    }


def _llm_response_payload(result: dict | None) -> dict | None:
    if not isinstance(result, dict):
        return None
    return {k: v for k, v in result.items() if not str(k).startswith("_")}


def _seconds_to_request_units(audio_duration_seconds: float | None) -> int | None:
    if audio_duration_seconds is None:
        return None
    if audio_duration_seconds <= 0:
        return 0
    return int(math.ceil(audio_duration_seconds))


_VALID_TRANSLATE_PREFS = (
    # Vertex AI（Google Cloud Express Mode，凭据来自 llm_provider_configs.gemini_cloud_text）
    "vertex_gemini_31_flash_lite",   # gemini-3.1-flash-lite-preview（默认）
    "vertex_gemini_3_flash",         # gemini-3-flash-preview
    "vertex_gemini_31_pro",          # gemini-3.1-pro-preview
    # Vertex AI ADC（凭据来自服务器 Application Default Credentials）
    "vertex_adc_gemini_31_flash_lite",
    "vertex_adc_gemini_3_flash",
    "vertex_adc_gemini_31_pro",
    # OpenRouter
    "gemini_31_flash",               # google/gemini-3.1-flash-lite-preview via openrouter
    "gemini_31_pro",                 # google/gemini-3.1-pro-preview via openrouter
    "gemini_3_flash",                # google/gemini-3-flash-preview via openrouter
    "gpt_5_mini",                    # openai/gpt-5-mini via openrouter
    "gpt_5_5",                       # openai/gpt-5.5 via openrouter
    "claude_sonnet",                 # anthropic/claude-sonnet-4.6 via openrouter
    "openrouter",                    # legacy（= claude_sonnet）
    # 火山引擎
    "doubao",
)


def _resolve_translate_provider(user_id: int | None) -> str:
    """Return the user's preferred translate provider.
    默认走 OpenRouter + Claude Sonnet 4.6。之前默认 Vertex Flash-Lite，
    但 google/gemini-3-flash-preview 在内网 region 出现 403、长 prompt 漏字段，
    在那之前先用 Claude 兜底配合分段 + source_segment_indices 派生修复。"""
    from appcore.api_keys import get_key
    default = "claude_sonnet"
    if user_id is None:
        return default
    pref = get_key(user_id, "translate_pref")
    return pref if pref in _VALID_TRANSLATE_PREFS else default


def _resolve_task_translate_provider(user_id: int | None, task: dict | None) -> str:
    provider = str((task or {}).get("custom_translate_provider") or "").strip()
    if provider in _VALID_TRANSLATE_PREFS:
        return provider
    return _resolve_translate_provider(user_id)


def _lang_display(label: str) -> str:
    """Convert language label (en/de/fr) to Chinese display name for step messages."""
    return {
        "en": "英语",
        "de": "德语",
        "fr": "法语",
        "es": "西班牙语",
        "it": "意大利语",
        "pt": "葡萄牙语",
        "ja": "日语",
        "nl": "荷兰语",
        "sv": "瑞典语",
        "fi": "芬兰语",
    }.get(label, label)


def _is_av_pipeline_task(task: dict | None) -> bool:
    task = task or {}
    task_type = str(task.get("type") or "").strip()
    pipeline_version = str(task.get("pipeline_version") or "").strip()
    return task_type == "av_translate" or pipeline_version == "av"


def _av_target_lang(task: dict | None) -> str:
    task = task or {}
    av_inputs = task.get("av_translate_inputs") or {}
    return str(task.get("target_lang") or av_inputs.get("target_language") or "en").strip().lower() or "en"


# Default words-per-second by target language (fallback when no measured data).
_DEFAULT_WPS = {
    "en": 2.5,
    "de": 2.0,
    "fr": 2.8,
    "es": 2.7,
    "it": 2.6,
    "pt": 2.6,
    "ja": 2.2,
    "nl": 2.4,
    "sv": 2.5,
    "fi": 2.1,
}


def _tts_final_target_range(video_duration: float) -> tuple[float, float]:
    """Return the accepted final TTS duration range: [video-1s, video+2s]."""
    return max(0.0, video_duration - 1.0), video_duration + 2.0


def _in_speedup_window(
    *,
    audio_duration: float,
    video_duration: float,
    window_ratio: tuple[float, float] | None = None,
) -> bool:
    """判断音频时长是否落入"变速短路"触发窗口：
    在 stage-1 区间 [0.9v, 1.1v] 内，但不在最终收敛区间 [v-1, v+2] 内。

    满足条件时，duration loop 应跳过下一轮 rewrite，改用 ElevenLabs voice_settings.speed
    重生成一遍音频试图直接收敛到 [v-1, v+2]。
    """
    if not (audio_duration > 0 and video_duration > 0):
        return False
    lo_ratio, hi_ratio = window_ratio or (0.9, 1.1)
    if not (lo_ratio > 0 and hi_ratio > 0 and lo_ratio <= hi_ratio):
        lo_ratio, hi_ratio = 0.9, 1.1
    final_lo, final_hi = _tts_final_target_range(video_duration)
    stage1_lo = video_duration * lo_ratio
    stage1_hi = video_duration * hi_ratio
    in_stage1 = stage1_lo <= audio_duration <= stage1_hi
    in_final = final_lo <= audio_duration <= final_hi
    return in_stage1 and not in_final


def _speedup_ratio(audio_duration: float, video_duration: float) -> float:
    """计算 ElevenLabs voice_settings.speed 取值。

    ratio = audio_duration / video_duration：
    - >1 时音频过长，需要变快、变短 → speed > 1
    - <1 时音频过短，需要变慢、变长 → speed < 1
    Clamp 到温和变速范围 [0.94, 1.06]，再按两位小数向上取整。
    """
    # Use a gentle quality range and always round upward to two decimals:
    # 1.0012 -> 1.01, 1.0071 -> 1.01.
    raw = Decimal(str(audio_duration)) / Decimal(str(video_duration))
    clamped = max(Decimal("0.94"), min(Decimal("1.06"), raw))
    rounded = clamped.quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    return float(rounded)


_TTS_SPEED_MIN = Decimal("0.94")
_TTS_SPEED_MAX = Decimal("1.06")
_TTS_SPEED_STEP = Decimal("0.01")


def _clamp_tts_speed(value: Decimal) -> Decimal:
    return max(_TTS_SPEED_MIN, min(_TTS_SPEED_MAX, value))


def _speed_grid() -> list[Decimal]:
    values: list[Decimal] = []
    current = _TTS_SPEED_MIN
    while current <= _TTS_SPEED_MAX:
        values.append(current.quantize(Decimal("0.01")))
        current += _TTS_SPEED_STEP
    return values


def _speed_key(value) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:
        return None


def _adaptive_speed_candidate(
    *,
    base_duration: float,
    video_duration: float,
    previous_candidates: list[dict] | None,
    max_candidates: int = 3,
    target_floor_margin: float = 1.0,
) -> float | None:
    """Pick the next native TTS speed using feedback from prior attempts.

    The provider speed value is always constrained to [0.94, 1.06].  The
    feedback step is 0.01, which is 10% of the total allowed range.
    """
    if not (base_duration > 0 and video_duration > 0):
        return None
    previous = list(previous_candidates or [])
    if max_candidates <= 0 or len(previous) >= max_candidates:
        return None

    target_lo = max(0.0, float(video_duration) - float(target_floor_margin))
    target_hi = float(video_duration)
    used = {
        key for key in (_speed_key(c.get("speed")) for c in previous)
        if key is not None
    }

    if not previous:
        if target_lo <= float(base_duration) <= target_hi:
            return None
        raw = Decimal(str(base_duration)) / Decimal(str(video_duration))
        rounding = ROUND_CEILING if raw >= Decimal("1.0") else ROUND_FLOOR
        speed = raw.quantize(Decimal("0.01"), rounding=rounding)
        speed = _clamp_tts_speed(speed)
        if speed == Decimal("1.00"):
            speed += _TTS_SPEED_STEP if base_duration > target_hi else -_TTS_SPEED_STEP
            speed = _clamp_tts_speed(speed)
        return float(speed)

    last = previous[-1]
    last_speed = _speed_key(last.get("speed"))
    if last_speed is None:
        return None
    try:
        last_duration = float(last.get("duration") or 0.0)
    except (TypeError, ValueError):
        last_duration = 0.0
    if target_lo <= last_duration <= target_hi:
        return None

    direction = 1 if last_duration > target_hi else -1
    preferred = _clamp_tts_speed(
        last_speed + (_TTS_SPEED_STEP if direction > 0 else -_TTS_SPEED_STEP)
    )
    if preferred not in used:
        return float(preferred)

    grid = _speed_grid()
    if direction > 0:
        primary = [s for s in grid if s > last_speed]
        secondary = [s for s in reversed(grid) if s < last_speed]
    else:
        primary = [s for s in reversed(grid) if s < last_speed]
        secondary = [s for s in grid if s > last_speed]
    for speed in primary + secondary:
        if speed not in used:
            return float(speed)
    return None


def _speedup_sampling_plan(
    *,
    base_duration: float,
    video_duration: float,
    previous_candidates: list[dict] | None,
    max_candidates: int = 3,
) -> list[dict]:
    """Return native-speed sample specs for the current speed assembly phase."""
    if not (base_duration > 0 and video_duration > 0):
        return []
    previous = list(previous_candidates or [])
    remaining = max(0, int(max_candidates) - len(previous))
    if remaining <= 0:
        return []

    if float(base_duration) > float(video_duration):
        start = len(previous) + 1
        return [
            {
                "attempt": attempt,
                "sample_index": attempt,
                "speed": float(_TTS_SPEED_MAX),
            }
            for attempt in range(start, start + remaining)
        ]

    speed = _adaptive_speed_candidate(
        base_duration=base_duration,
        video_duration=video_duration,
        previous_candidates=previous,
        max_candidates=max_candidates,
    )
    if speed is None:
        return []
    attempt = len(previous) + 1
    return [{
        "attempt": attempt,
        "sample_index": attempt,
        "speed": speed,
    }]


def _speedup_voice_settings_for_attempt(attempt: int) -> dict:
    """Return conservative ElevenLabs voice setting overrides per speed attempt."""
    if attempt == 2:
        return {
            "profile": "balanced_variation",
            "stability": 0.50,
            "similarity_boost": 0.80,
        }
    if attempt >= 3:
        return {
            "profile": "duration_variation",
            "stability": 0.35,
            "similarity_boost": 0.72,
        }
    return {"profile": "speed_only"}


def _speedup_candidate_speeds(
    *,
    audio_duration: float,
    video_duration: float,
    max_candidates: int = 3,
) -> list[float]:
    """Return native TTS speed values for shortening over-video audio.

    Speedup is only used to shorten audio, so audio that is already within the
    source video duration does not get slow-down candidates. The range is kept
    deliberately gentle for voice quality.
    """
    if not (audio_duration > 0 and video_duration > 0):
        return []
    if audio_duration <= video_duration:
        return []
    if max_candidates <= 0:
        return []

    raw = Decimal(str(audio_duration)) / Decimal(str(video_duration))
    start = raw.quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    start = max(Decimal("1.01"), min(_TTS_SPEED_MAX, start))
    speeds: list[float] = []
    current = start
    while len(speeds) < max_candidates and current <= _TTS_SPEED_MAX:
        value = float(current)
        if value not in speeds:
            speeds.append(value)
        current += Decimal("0.01")
    return speeds


def _select_segment_candidate_assembly(
    candidate_groups: list[list[dict]],
    *,
    video_duration: float,
    target_floor_margin: float = 1.0,
    beam_size: int = 512,
) -> dict:
    """Pick one audio candidate per segment for a video-capped assembly.

    The optimizer only reports a hit when the selected total lands inside
    ``[video_duration - target_floor_margin, video_duration]``. It still tracks
    the best under-video duration for diagnostics when every valid combination
    is too short.
    """
    target_lo = max(0.0, float(video_duration) - float(target_floor_margin))
    target_hi = float(video_duration)
    def _rounded_duration(value: float | None) -> float | None:
        return round(value, 6) if value is not None else None

    def _empty_diagnostics() -> dict:
        return {
            "candidate_combination_count": 0,
            "best_under_duration": None,
            "best_under_selected": [],
            "closest_over_duration": None,
            "closest_over_selected": [],
            "min_duration": None,
            "min_selected": [],
        }

    if not (target_hi > 0) or not candidate_groups:
        empty = _empty_diagnostics()
        empty.update({
            "hit": False,
            "selected": [],
            "total_duration": None,
            "target_lo": target_lo,
            "target_hi": target_hi,
            "gap": None,
        })
        return empty

    def _miss(diagnostics: dict | None = None) -> dict:
        response = _empty_diagnostics()
        if diagnostics:
            response.update(diagnostics)
        response.update({
            "hit": False,
            "selected": [],
            "total_duration": None,
            "target_lo": target_lo,
            "target_hi": target_hi,
            "gap": None,
        })
        return response

    prepared_groups: list[list[dict]] = []
    for raw_group in candidate_groups:
        if not raw_group:
            return _miss()

        baseline_hash = next(
            (c.get("tts_text_hash") for c in raw_group if c.get("tts_text_hash")),
            None,
        )
        group = [
            dict(c) for c in raw_group
            if float(c.get("duration") or 0.0) > 0
            and (
                baseline_hash is None
                or not c.get("tts_text_hash")
                or c.get("tts_text_hash") == baseline_hash
            )
        ]
        if not group:
            return _miss()
        prepared_groups.append(group)

    candidate_combination_count = 1
    for group in prepared_groups:
        candidate_combination_count *= len(group)

    def _candidate_metrics(candidate: dict) -> tuple[float, int, float]:
        duration = float(candidate.get("duration") or 0.0)
        speed = float(candidate.get("speed") or 1.0)
        source = str(candidate.get("source") or "")
        is_modified = source != "round" or abs(speed - 1.0) > 0.001
        return duration, 1 if is_modified else 0, abs(speed - 1.0)

    def _selection_response(
        total: float,
        modified_count: int,
        speed_penalty: float,
        selected: list[dict],
        diagnostics: dict,
    ) -> dict:
        response = dict(diagnostics)
        response.update({
            "hit": True,
            "selected": selected,
            "total_duration": _rounded_duration(total),
            "target_lo": target_lo,
            "target_hi": target_hi,
            "gap": _rounded_duration(target_hi - total),
            "modified_segments": modified_count,
            "speed_penalty": _rounded_duration(speed_penalty),
        })
        return response

    def _diagnostics_from_states(final_states: list[tuple]) -> dict:
        def _selection_from_state(state: tuple | None) -> list[dict]:
            if state is None:
                return []
            selected: list[dict] = []
            cursor = state
            while cursor[3] is not None:
                selected.append(cursor[3])
                cursor = cursor[4]
            selected.reverse()
            return selected

        diagnostics = _empty_diagnostics()
        diagnostics["candidate_combination_count"] = candidate_combination_count
        if not final_states:
            return diagnostics

        under_states = [state for state in final_states if state[0] <= target_hi]
        over_states = [state for state in final_states if state[0] > target_hi]
        best_under_state = (
            sorted(
                under_states,
                key=lambda state: (-state[0], state[1], state[2]),
            )[0]
            if under_states else None
        )
        closest_over_state = (
            sorted(
                over_states,
                key=lambda state: (state[0] - target_hi, state[1], state[2]),
            )[0]
            if over_states else None
        )
        min_state = sorted(
            final_states,
            key=lambda state: (state[0], state[1], state[2]),
        )[0]

        diagnostics.update({
            "best_under_duration": _rounded_duration(
                best_under_state[0] if best_under_state else None
            ),
            "best_under_selected": _selection_from_state(best_under_state),
            "closest_over_duration": _rounded_duration(
                closest_over_state[0] if closest_over_state else None
            ),
            "closest_over_selected": _selection_from_state(closest_over_state),
            "min_duration": _rounded_duration(min_state[0]),
            "min_selected": _selection_from_state(min_state),
        })
        return diagnostics

    def _beam_select(groups: list[list[dict]]) -> dict:
        beams: list[tuple[float, int, float, list[dict]]] = [(0.0, 0, 0.0, [])]
        for group in groups:
            next_beams: list[tuple[float, int, float, list[dict]]] = []
            for total, modified_count, speed_penalty, selected in beams:
                for candidate in group:
                    duration, modified_delta, speed_delta = _candidate_metrics(candidate)
                    next_beams.append((
                        total + duration,
                        modified_count + modified_delta,
                        speed_penalty + speed_delta,
                        selected + [candidate],
                    ))

            def _beam_rank(item: tuple[float, int, float, list[dict]]) -> tuple:
                total, modified_count, speed_penalty, _ = item
                over_video = total > target_hi
                if over_video:
                    return (1, total - target_hi, modified_count, speed_penalty)
                return (0, -(total), modified_count, speed_penalty)

            beams = sorted(next_beams, key=_beam_rank)[:max(1, beam_size)]

        under = [item for item in beams if item[0] <= target_hi]
        hit = [item for item in under if item[0] >= target_lo]
        over = [item for item in beams if item[0] > target_hi]
        best_under = max(under, key=lambda item: item[0]) if under else None
        closest_over = min(over, key=lambda item: item[0] - target_hi) if over else None
        min_item = min(beams, key=lambda item: item[0]) if beams else None
        diagnostics = _empty_diagnostics()
        diagnostics.update({
            "candidate_combination_count": candidate_combination_count,
            "best_under_duration": _rounded_duration(
                best_under[0] if best_under is not None else None
            ),
            "best_under_selected": best_under[3] if best_under is not None else [],
            "closest_over_duration": _rounded_duration(
                closest_over[0] if closest_over is not None else None
            ),
            "closest_over_selected": (
                closest_over[3] if closest_over is not None else []
            ),
            "min_duration": _rounded_duration(min_item[0] if min_item else None),
            "min_selected": min_item[3] if min_item is not None else [],
        })
        if not hit:
            return _miss(diagnostics)

        best = sorted(hit, key=lambda item: (-item[0], item[1], item[2]))[0]
        total, modified_count, speed_penalty, selected = best
        return _selection_response(
            total, modified_count, speed_penalty, selected, diagnostics,
        )

    state_budget = 1_000_000

    # Exact DP: one best partial assembly per millisecond duration bucket.
    # State tuple: (actual_total, modified_count, speed_penalty, candidate, parent_state)
    initial_state = (0.0, 0, 0.0, None, None)
    dp: dict[int, tuple] = {0: initial_state}

    def _state_rank(state: tuple) -> tuple:
        total, modified_count, speed_penalty, _, _ = state
        return (-total, modified_count, speed_penalty)

    def _is_better_state(candidate_state: tuple, current_state: tuple | None) -> bool:
        if current_state is None:
            return True
        return _state_rank(candidate_state) < _state_rank(current_state)

    for group in prepared_groups:
        next_dp: dict[int, tuple] = {}
        for state in dp.values():
            total, modified_count, speed_penalty, _, _ = state
            for candidate in group:
                duration, modified_delta, speed_delta = _candidate_metrics(candidate)
                new_total = total + duration
                new_total_ms = max(1, int(round(new_total * 1000.0)))
                new_state = (
                    new_total,
                    modified_count + modified_delta,
                    speed_penalty + speed_delta,
                    candidate,
                    state,
                )
                if _is_better_state(new_state, next_dp.get(new_total_ms)):
                    next_dp[new_total_ms] = new_state
        if not next_dp:
            return _miss()
        if len(next_dp) > state_budget:
            return _beam_select(prepared_groups)
        dp = next_dp

    final_states = list(dp.values())
    diagnostics = _diagnostics_from_states(final_states)
    hit_states = [state for state in final_states if target_lo <= state[0] <= target_hi]
    if not hit_states:
        return _miss(diagnostics)

    best_state = sorted(hit_states, key=_state_rank)[0]
    selected: list[dict] = []
    cursor = best_state
    while cursor[3] is not None:
        selected.append(cursor[3])
        cursor = cursor[4]
    selected.reverse()

    return _selection_response(
        best_state[0], best_state[1], best_state[2], selected, diagnostics,
    )


def _compute_next_target(
    round_index: int,
    last_audio_duration: float,
    wps: float,
    video_duration: float,
) -> tuple[float, int, str]:
    """Compute (target_duration, target_words, direction) for rewrite rounds 2+.

    Round 2 aims directly at video_duration (center of the [0.9v, 1.1v] range).
    Round 3+ uses adaptive over-correction: reverse half of the previous error,
    clamped to the range.

    Args:
        round_index: 2 or higher.
        last_audio_duration: audio length from the previous round (seconds).
        wps: words-per-second rate for this voice×language (measured or default).
        video_duration: original video duration (seconds).

    Returns:
        (target_duration_seconds, target_word_count, direction)
        direction ∈ {"shrink", "expand"}
    """
    duration_lo = video_duration * 0.9
    duration_hi = video_duration * 1.1
    center = video_duration

    if round_index == 2:
        target_duration = video_duration
        direction = "shrink" if last_audio_duration > center else "expand"
    else:  # round 3+
        raw = center - 0.5 * (last_audio_duration - center)
        target_duration = max(duration_lo, min(duration_hi, raw))
        direction = "shrink" if last_audio_duration > center else "expand"

    target_words = max(3, round(target_duration * wps))
    return target_duration, target_words, direction


def _distance_to_duration_range(duration: float, lower: float, upper: float) -> float:
    """Return the distance from duration to the inclusive [lower, upper] range."""
    if lower <= duration <= upper:
        return 0.0
    if duration > upper:
        return duration - upper
    return lower - duration


def _apply_audio_tempo_fallback(
    *,
    audio_path: str,
    audio_duration: float,
    video_duration: float,
    output_path: str,
    max_error_ratio: float = 0.05,
    min_delta_seconds: float = 0.10,
) -> dict | None:
    """Last-mile fallback：当生成音频与视频长度误差在 ±max_error_ratio 之内
    （默认 5%），用 ffmpeg atempo 把音频精确拉伸/压缩到等于 video_duration。

    返回 None 表示不需要变速（误差太大或太小）；返回 dict 表示已生效，包含：
      ratio / pre_duration / post_duration / new_delta / new_audio_path

    设计：
    - atempo 合法范围 0.5-2.0，5% 内 ratio ∈ [0.95, 1.05] 完全在范围里
    - 误差 <0.1s 跳过——本身就是对齐的，没必要再过 ffmpeg 浪费一次重编码
    - 失败不抛异常，返回 None 让上层 fallback 到原音频
    """
    import os
    import subprocess

    if not audio_path or not os.path.isfile(audio_path):
        return None
    if not audio_duration or not video_duration:
        return None
    delta = audio_duration - video_duration
    abs_delta = abs(delta)
    if abs_delta < min_delta_seconds:
        return None
    if abs_delta / video_duration > max_error_ratio:
        return None

    ratio = audio_duration / video_duration  # >1 时变快、变短；<1 时变慢、变长
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-filter:a", f"atempo={ratio:.4f}",
        "-vn", "-acodec", "libmp3lame", "-q:a", "3",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return None
    except Exception:
        return None

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", output_path],
        capture_output=True, text=True,
    )
    try:
        post_duration = float(probe.stdout.strip())
    except (ValueError, AttributeError):
        return None
    if post_duration <= 0:
        return None

    return {
        "ratio": round(ratio, 4),
        "pre_duration": round(audio_duration, 3),
        "post_duration": round(post_duration, 3),
        "new_delta": round(post_duration - video_duration, 3),
        "new_audio_path": output_path,
    }


def _fit_tts_segments_to_duration(tts_segments: list[dict], target_duration: float) -> list[dict]:
    """Keep only the audible prefix of TTS segments within target_duration."""
    kept: list[dict] = []
    elapsed = 0.0
    target_duration = max(0.0, float(target_duration or 0.0))

    for segment in tts_segments:
        seg_duration = float(segment.get("tts_duration", 0.0) or 0.0)
        remaining = target_duration - elapsed
        if remaining <= 1e-6:
            break

        seg_copy = dict(segment)
        if seg_duration <= remaining + 1e-6:
            seg_copy["tts_duration"] = seg_duration
            kept.append(seg_copy)
            elapsed += seg_duration
            continue

        seg_copy["tts_duration"] = round(remaining, 3)
        kept.append(seg_copy)
        break

    return kept


def _trim_tts_metadata_to_segments(
    tts_script: dict,
    localized_translation: dict,
    tts_segments: list[dict],
) -> tuple[dict, dict]:
    """Trim script/localized metadata to the kept TTS segment indices."""
    kept_block_ids = {
        int(segment["index"])
        for segment in tts_segments
        if segment.get("index") is not None
    }
    new_blocks = [block for block in tts_script.get("blocks", []) if block.get("index") in kept_block_ids]
    new_subtitle_chunks = [
        chunk for chunk in tts_script.get("subtitle_chunks", [])
        if chunk.get("block_indices")
        and all(block_index in kept_block_ids for block_index in chunk["block_indices"])
    ]
    new_tts_script = {
        "full_text": " ".join(block.get("text", "") for block in new_blocks).strip(),
        "blocks": new_blocks,
        "subtitle_chunks": new_subtitle_chunks,
    }

    kept_sentence_ids: set[int] = set()
    for block in new_blocks:
        kept_sentence_ids.update(block.get("sentence_indices", []))
    new_sentences = [
        sentence for sentence in localized_translation.get("sentences", [])
        if sentence.get("index") in kept_sentence_ids
    ]
    new_localized_translation = {
        "full_text": " ".join(sentence.get("text", "") for sentence in new_sentences).strip(),
        "sentences": new_sentences,
    }
    return new_tts_script, new_localized_translation


# ===== TTS 并发进度回调 helper =====
#
# 5 个 TTS 调用方（多语言视频翻译 / 全能翻译 / 视频翻译音画同步 / 日语 / 文案配音）
# 都把 generate_full_audio(on_progress=make_tts_progress_emitter(...)) 接在一起，
# 共享同一份"排队中 / 进度 / 完成"中文文案，前端跨模块体验一致。

from typing import Callable as _Callable

_progress_log = logging.getLogger(__name__)


def make_tts_progress_emitter(
    runner,
    task_id: str,
    *,
    lang_label: str,
    round_label: str = "",
    extra_state_update: _Callable[[dict], None] | None = None,
) -> _Callable[[dict], None]:
    """生成 generate_full_audio(on_progress=...) 用的标准回调，把 snapshot
    转成统一中文 substep 文案推到前端。

    Args:
        runner: 任何提供 ``_emit_substep_msg(task_id, step, msg)`` 的 runtime 实例。
        task_id: 任务 ID，用于 substep 路由。
        lang_label: 语言显示名（例如 "西班牙语"），拼进文案前缀。
        round_label: 可选轮次标签（例如 "第 2 轮"），拼进文案前缀。
        extra_state_update: 可选回调，每次 emit 时同步给一份 snapshot
            （用于 ``_pipeline_runner`` 同步更新 ``round_record["audio_segments_done"]``）。
            抛出的异常会被吞掉，不影响主流程。
    """
    def _emit(snapshot: dict) -> None:
        active = snapshot.get("active", 0)
        done = snapshot.get("done", 0)
        total = snapshot.get("total", 0)
        queued = snapshot.get("queued", 0)

        prefix = f"正在生成{lang_label}配音" if lang_label else "正在生成配音"
        if round_label:
            prefix = f"{prefix} · {round_label}"

        if active == 0 and done == 0 and total > 0:
            msg = f"{prefix} · 排队中等待 ElevenLabs 并发槽位（{queued} 段待派发）"
        else:
            msg = f"{prefix} · {done}/{total}（活跃 {active} 路）"

        runner._emit_substep_msg(task_id, "tts", msg)
        if extra_state_update is not None:
            try:
                extra_state_update(snapshot)
            except Exception:
                _progress_log.exception(
                    "extra_state_update raised in tts progress emitter; ignoring"
                )

    return _emit
