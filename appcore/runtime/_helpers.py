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
import uuid
from datetime import datetime

import config

import appcore.task_state as task_state
from appcore.api_keys import resolve_jianying_project_root
from appcore import ai_billing
from appcore import tts_generation_stats
from appcore.events import (
    EVT_ALIGNMENT_READY,
    EVT_ASR_RESULT,
    EVT_CAPCUT_READY,
    EVT_ENGLISH_ASR_RESULT,
    EVT_PIPELINE_DONE,
    EVT_PIPELINE_ERROR,
    EVT_STEP_UPDATE,
    EVT_SUBTITLE_READY,
    EVT_TRANSLATE_RESULT,
    EVT_TTS_SCRIPT_READY,
    EVT_VOICE_MATCH_READY,
    Event,
    EventBus,
)
from appcore.preview_artifacts import (
    build_alignment_artifact,
    build_analysis_artifact,
    build_asr_artifact,
    build_compose_artifact,
    build_export_artifact,
    build_extract_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
    build_tts_artifact,
)
from appcore.tts_language_guard import (
    TtsLanguageValidationError,
    extract_tts_script_text,
    validate_tts_script_language_or_raise,
)


log = logging.getLogger(__name__)
logger = logging.getLogger(__name__)


def _skip_legacy_artifact_upload(task: dict, task_id: str) -> None:
    """Compatibility shim for legacy object-storage metadata.

    New tasks keep generated artifacts in local storage. Historical metadata
    remains readable through download routes, but runtime no longer uploads
    final outputs to object storage by default.
    """
    return


logger = logging.getLogger(__name__)


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
        return "gemini_vertex_adc"
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


def _in_speedup_window(*, audio_duration: float, video_duration: float) -> bool:
    """判断音频时长是否落入"变速短路"触发窗口：
    在 stage-1 区间 [0.9v, 1.1v] 内，但不在最终收敛区间 [v-1, v+2] 内。

    满足条件时，duration loop 应跳过下一轮 rewrite，改用 ElevenLabs voice_settings.speed
    重生成一遍音频试图直接收敛到 [v-1, v+2]。
    """
    if not (audio_duration > 0 and video_duration > 0):
        return False
    final_lo, final_hi = _tts_final_target_range(video_duration)
    stage1_lo = video_duration * 0.9
    stage1_hi = video_duration * 1.1
    in_stage1 = stage1_lo <= audio_duration <= stage1_hi
    in_final = final_lo <= audio_duration <= final_hi
    return in_stage1 and not in_final


def _speedup_ratio(audio_duration: float, video_duration: float) -> float:
    """计算 ElevenLabs voice_settings.speed 取值。

    ratio = audio_duration / video_duration：
    - >1 时音频过长，需要变快、变短 → speed > 1
    - <1 时音频过短，需要变慢、变长 → speed < 1
    Clamp 到 ElevenLabs 合法范围 [0.7, 1.2]，超出窗口的极端值由调用方在
    _in_speedup_window 阶段已经过滤掉，这里 clamp 只是兜底。
    """
    raw = audio_duration / video_duration
    return max(0.7, min(1.2, raw))


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
