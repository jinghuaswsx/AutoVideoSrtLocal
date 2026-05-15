"""Omni AV sync audit and bounded sentence-level fixes."""
from __future__ import annotations

import json
import logging
import math
from copy import deepcopy
from typing import Any

from appcore import llm_client, task_state
from appcore.llm_debug_payloads import (
    build_chat_request_payload,
    build_generate_request_payload,
    prompt_file_payload,
)
from appcore.llm_media_optimizer import (
    REVIEW_480P_AUDIO,
    cleanup_optimized_media,
    media_debug_snapshot,
    prepare_video_for_llm,
)
from appcore.omni_plugin_config import validate_plugin_config
from appcore.preview_artifacts import build_tts_artifact
from appcore.runtime import (
    _build_av_localized_translation,
    _build_av_tts_segments,
    _ensure_variant_state,
    _normalize_av_sentences,
    _rebuild_tts_full_audio_from_segments,
    _save_json,
)

log = logging.getLogger(__name__)

_SAFE_AUTO_ACTIONS = {"shorten_text", "expand_text", "regenerate_tts"}
_APPLY_SEVERITIES = {"medium", "high"}
_MIN_SAFE_RATIO = 0.95
_MAX_SAFE_RATIO = 1.05
_MIN_SAFE_SPEED = 0.95
_MAX_SAFE_SPEED = 1.05
_MAX_SUBTITLE_CONTEXT_CHARS = 12000
_ASSESS_PROVIDER = "openrouter"
_ASSESS_MODEL = "google/gemini-3-flash-preview"

_SEVERITY_LABELS = {
    "low": "低风险",
    "medium": "中风险",
    "high": "高风险",
}

_PROBLEM_TYPE_LABELS = {
    "visual_mismatch": "文案与画面动作不匹配",
    "speech_early": "配音提前结束",
    "speech_late": "配音进入下一个画面",
    "duration_risk": "TTS 时长不匹配",
    "audio_too_long": "音频太长导致画面对不上",
    "audio_too_short": "音频太短导致画面留白",
    "subtitle_risk": "字幕节奏风险",
    "tts_quality_risk": "TTS 质量风险",
}


_DIAGNOSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "issues": {"type": "array"},
        "timeline": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "asr_index": {"type": "integer"},
                    "asr_text_zh": {"type": "string"},
                    "visual_observation": {"type": "string"},
                    "sync_score": {"type": "number"},
                    "diagnosis": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
            },
        },
        "summary": {"type": "string"},
    },
}

_SCORECARD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["timeline"],
    "properties": {
        "timeline": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "asr_index",
                    "visual_observation",
                    "sync_score",
                    "diagnosis",
                    "recommendation",
                ],
                "properties": {
                    "asr_index": {"type": "integer"},
                    "asr_text_zh": {"type": "string"},
                    "visual_observation": {"type": "string"},
                    "sync_score": {"type": "number", "minimum": 0, "maximum": 100},
                    "diagnosis": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
            },
        },
    },
}

_VERIFY_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "accepted_issues": {"type": "array"},
        "rejected_count": {"type": "integer"},
        "summary": {"type": "string"},
    },
}


def _is_multi_translate_report(cfg: dict) -> bool:
    return str((cfg or {}).get("project_type") or "") == "multi_translate"


def _is_report_only_scorecard(cfg: dict) -> bool:
    return str((cfg or {}).get("av_sync_audit") or "") == "report_only"


def _is_chinese_source_language(task: dict | None) -> bool:
    raw = str((task or {}).get("source_language") or "").strip().lower()
    return raw == "zh" or raw.startswith("zh-") or raw.startswith("zh_")


def _needs_asr_chinese_reference(task: dict | None) -> bool:
    raw = str((task or {}).get("source_language") or "").strip()
    return bool(raw) and not _is_chinese_source_language(task)


def _json_from_result(result: dict | None, default: dict) -> dict:
    if not isinstance(result, dict):
        return deepcopy(default)
    payload = result.get("json")
    if isinstance(payload, dict):
        return payload
    text = result.get("text")
    if isinstance(text, str) and text.strip():
        content = text.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        try:
            parsed = json.loads(content.strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return deepcopy(default)
    return deepcopy(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ratio(target_duration: float, tts_duration: float) -> float:
    if target_duration <= 0 or tts_duration <= 0:
        return 1.0
    return round(tts_duration / target_duration, 4)


def _distance_from_one(value: float) -> float:
    return abs(_safe_float(value, 1.0) - 1.0)


def _is_sentence_chain(cfg: dict) -> bool:
    return (
        cfg.get("translate_algo") == "av_sentence"
        and cfg.get("tts_strategy") == "sentence_reconcile"
        and cfg.get("subtitle") == "sentence_units"
    )


def _max_auto_fix_count(sentence_count: int) -> int:
    if sentence_count <= 0:
        return 0
    return max(1, min(5, math.ceil(sentence_count * 0.2)))


def _compact_sentences(sentences: list[dict]) -> list[dict]:
    compact: list[dict] = []
    for sentence in sentences:
        compact.append({
            "asr_index": sentence.get("asr_index"),
            "source_segment_indices": sentence.get("source_segment_indices"),
            "start_time": sentence.get("start_time"),
            "end_time": sentence.get("end_time"),
            "target_duration": sentence.get("target_duration"),
            "source_text": sentence.get("source_text"),
            "text": sentence.get("text"),
            "translated": sentence.get("translated"),
            "tts_duration": sentence.get("tts_duration"),
            "duration_ratio": sentence.get("duration_ratio"),
            "speed": sentence.get("speed"),
            "status": sentence.get("status"),
            "subtitle_text": sentence.get("subtitle_text"),
            "subtitle_start_time": sentence.get("subtitle_start_time"),
            "subtitle_end_time": sentence.get("subtitle_end_time"),
        })
    return compact


def _scorecard_rows(sentences: list[dict], task: dict | None) -> list[dict]:
    notes_by_asr = _shot_notes_by_asr(task)
    needs_asr_text_zh = _needs_asr_chinese_reference(task)
    rows: list[dict] = []
    for pos, sentence in enumerate(sentences or []):
        if not isinstance(sentence, dict):
            continue
        asr_index = _as_int_or_none(sentence.get("asr_index"))
        if asr_index is None:
            asr_index = _as_int_or_none(sentence.get("index"))
        if asr_index is None:
            asr_index = pos
        start_time, end_time, target_duration, tts_duration = _timing_snapshot(sentence)
        sync_point = (
            f"ASR {asr_index}（{_format_sync_time(start_time)}-{_format_sync_time(end_time)}）"
            if start_time is not None and end_time is not None
            else f"ASR {asr_index}"
        )
        rows.append({
            "asr_index": asr_index,
            "sync_point": sync_point,
            "start_time": start_time,
            "end_time": end_time,
            "asr_text": str(sentence.get("source_text") or "").strip(),
            "needs_asr_text_zh": needs_asr_text_zh,
            "target_text": _report_sentence_text(sentence, {}),
            "visual_observation": _format_visual_context(notes_by_asr.get(asr_index)),
            "target_duration": target_duration,
            "tts_duration": tts_duration,
            "duration_ratio": _as_float_or_none(sentence.get("duration_ratio")),
        })
    return rows


def _truncate_context_text(text: str, max_chars: int = _MAX_SUBTITLE_CONTEXT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...[truncated]"


def _subtitle_context(task: dict, cfg: dict) -> dict:
    variant = cfg.get("report_variant") or (
        "normal" if cfg.get("project_type") == "multi_translate" else "av"
    )
    variants = task.get("variants") or {}
    variant_state = variants.get(variant) or {}
    corrected = variant_state.get("corrected_subtitle") or task.get("corrected_subtitle") or {}
    srt_content = ""
    if isinstance(corrected, dict):
        srt_content = str(corrected.get("srt_content") or "").strip()

    srt_path = variant_state.get("srt_path") or task.get("srt_path")
    if not srt_content and srt_path:
        try:
            with open(str(srt_path), "r", encoding="utf-8") as handle:
                srt_content = handle.read().strip()
        except OSError:
            srt_content = ""

    context: dict[str, Any] = {}
    if srt_content:
        context["subtitle_srt"] = _truncate_context_text(srt_content)
    return context


def _build_video_understanding_prompt(task: dict, cfg: dict) -> str:
    target_lang = task.get("target_lang") or task.get("target_language") or cfg.get("target_lang") or ""
    project_type = cfg.get("project_type") or task.get("type") or "omni"
    return (
        "请只观看这个已经合成的视频，输出中文视频理解笔记。不要输出 JSON，不要做复杂模型评分，"
        "也不要根据下面没有提供的句级数据臆测。你的核心任务是读懂成片视频本身。\n"
        f"项目类型：{project_type}；目标语言：{target_lang or '-'}。\n"
        "请按时间顺序写：\n"
        "1. 画面动作、镜头变化、人物/产品关键动作。\n"
        "2. 你能看到的字幕、屏幕文字或明显可读文本。\n"
        "3. 直观看到/听到的音画、字幕或口型错位现象；看不准就写“不确定”。\n"
        "保持简洁，但要保留具体时间点或相邻镜头关系。"
    )


def _program_sync_candidates(sentences: list[dict]) -> list[dict]:
    candidates: list[dict] = []
    for sentence in sentences or []:
        start_time, end_time, target_duration, tts_duration = _timing_snapshot(sentence)
        if target_duration is None or tts_duration is None or target_duration <= 0 or tts_duration <= 0:
            continue
        delta = round(tts_duration - target_duration, 2)
        ratio = round(tts_duration / target_duration, 4)
        long_threshold = max(0.35, target_duration * 0.12)
        short_threshold = max(0.35, target_duration * 0.18)
        problem_type = ""
        safe_action = "none"
        if delta > long_threshold:
            problem_type = "audio_too_long"
            safe_action = "shorten_text"
        elif delta < -short_threshold:
            problem_type = "audio_too_short"
            safe_action = "expand_text"
        else:
            continue

        asr_index = _as_int_or_none(sentence.get("asr_index"))
        if asr_index is None:
            continue
        timing_detail, mismatch_reason, _delta, _ratio = _timing_detail(target_duration, tts_duration)
        severity_threshold = max(0.8, target_duration * 0.25)
        severity = "high" if abs(delta) >= severity_threshold else "medium"
        sync_point = f"ASR {asr_index}"
        if start_time is not None and end_time is not None:
            sync_point = f"ASR {asr_index}（{_format_sync_time(start_time)}-{_format_sync_time(end_time)}）"
        sentence_text = _report_sentence_text(sentence, {})
        direction = "音频太长" if delta > 0 else "音频太短"
        candidates.append({
            "asr_index": asr_index,
            "severity": severity,
            "problem_type": problem_type,
            "sync_point": sync_point,
            "sentence_text": sentence_text,
            "evidence": (
                f"{sync_point} 的目标画面 {target_duration:.2f}s，TTS 音频 {tts_duration:.2f}s，"
                f"{direction} {abs(delta):.2f}s（{round(ratio * 100)}%）。"
            ),
            "timing_detail": timing_detail,
            "mismatch_reason": mismatch_reason,
            "safe_action": safe_action,
            "confidence": 0.72 if severity == "medium" else 0.82,
            "target_duration": round(target_duration, 4),
            "tts_duration": round(tts_duration, 4),
            "duration_delta": delta,
            "duration_ratio": ratio,
            "start_time": start_time,
            "end_time": end_time,
            "source_text": sentence.get("source_text"),
            "translated": sentence.get("translated") or sentence.get("text"),
            "subtitle_text": sentence.get("subtitle_text"),
        })
    return candidates


def _build_assess_messages(
    video_understanding: dict,
    task: dict,
    cfg: dict,
    sentences: list[dict],
    program_candidates: list[dict],
) -> list[dict]:
    if _is_multi_translate_report(cfg) or _is_report_only_scorecard(cfg):
        payload = {
            "source_language": task.get("source_language"),
            "target_lang": task.get("target_lang") or task.get("target_language"),
            "video_understanding": video_understanding,
            "scorecard_rows": _scorecard_rows(sentences, task),
            "constraints": {
                "output_only_timeline": True,
                "one_row_per_asr": True,
                "include_asr_chinese_reference": _needs_asr_chinese_reference(task),
                "do_not_change_video": True,
                "do_not_change_asr_or_tts_text": True,
                "do_not_write_summary": True,
                "score_range": [0, 100],
            },
        }
    else:
        payload = {
            "source_language": task.get("source_language"),
            "target_lang": task.get("target_lang") or task.get("target_language"),
            "plugin_config": cfg,
            "video_understanding": video_understanding,
            "sentences": _compact_sentences(sentences),
            "program_candidates": program_candidates,
            "constraints": {
                "do_not_change_video": True,
                "do_not_shift_timeline": True,
                "allowed_actions": ["none", "shorten_text", "expand_text", "regenerate_tts", "manual_review"],
                "safe_speed_range": [0.95, 1.05],
                "do_not_invent_visual_mismatch": True,
                "prefer_program_timing_evidence": True,
            },
        }
    subtitle_context = _subtitle_context(task, cfg)
    if subtitle_context:
        payload["subtitle_context"] = subtitle_context
    if _is_multi_translate_report(cfg) or _is_report_only_scorecard(cfg):
        return [
            {
                "role": "system",
                "content": (
                    "你是视频翻译音画同步评分员。你的唯一任务是填写逐段审片表，并为每段给出评分。"
                    "只输出 JSON，根字段只能使用 timeline。不要输出总结，不要输出问题列表。"
                    "timeline 必须按 scorecard_rows 的 ASR 顺序覆盖每一段；每项必须包含 asr_index、"
                    "visual_observation、sync_score、diagnosis、recommendation。"
                    "当 constraints.include_asr_chinese_reference 为 true 时，每项还必须包含 asr_text_zh，"
                    "用于填写 ASR 内容的简体中文直译对照；为 false 时不要额外翻译 ASR。"
                    "你只能根据每行的 ASR 内容、正常翻译/TTS 文案，以及 Doubao 视频理解笔记归纳出的实际视频画面评分。"
                    "ASR 内容和正常翻译/TTS 文案已经在 scorecard_rows 中提供，不要改写。"
                    "visual_observation 只写该 ASR 时间段配套视频里实际看到的画面：动作、物体、字幕、屏幕文字、镜头变化；"
                    "如果 scorecard_rows 已有可用 visual_observation，可直接校正后沿用。"
                    "sync_score 是这一段音画同步评分，0-100 分，100 表示完全同步。"
                    "diagnosis 用一句中文简要判断这一段音画同步情况。"
                    "recommendation 用一句中文给整改调整意见；没有问题时必须写“无需调整。”。"
                    "看不清或无法对应时，visual_observation 写“画面不清晰/无法确认”，并按不确定性扣分。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ]
    return [
        {
            "role": "system",
            "content": (
                "你是视频翻译音画同步评估员。Doubao 已经负责观看成片视频，你现在只结合它的视频理解笔记、"
                "最终字幕/TTS 时间线和程序候选点做结构化中文评估。输出 JSON。"
                "另输出 timeline 数组，按 ASR 时间顺序覆盖每一段 ASR；每行必须包含 asr_index、"
                "visual_observation、sync_score、diagnosis、recommendation，其中 visual_observation 写这一段对应的实际画面内容，"
                "sync_score 是这一段音画同步评分，0-100 分，100 表示完全同步；"
                "diagnosis 写逐段诊断意见；recommendation 写整改调整意见，没有明显问题时写“无需调整”。"
                "必须使用中文表述 summary、evidence、timing_detail、recommendation，明确哪些同步点有问题，"
                "哪一句音频太长或太短导致画面对不上。"
                "不要凭空新增视频画面结论；如果 Doubao 笔记说不确定，只按程序候选和字幕/TTS 时间线给出风险级别。"
                "处理建议只能是音频变速、重写文案后重新生成音频、重新生成音频或人工复核；"
                "不要建议剪辑画面或移动时间轴。issues 内每项必须包含 asr_index、severity、problem_type、"
                "evidence、safe_action、confidence，并尽量包含 sync_point、sentence_text、timing_detail、recommendation。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def _build_verify_messages(diagnosis: dict, task: dict, cfg: dict, sentences: list[dict]) -> list[dict]:
    payload = {
        "diagnosis": diagnosis,
        "plugin_config": cfg,
        "sentences": _compact_sentences(sentences),
        "constraints": {
            "accepted_severities": ["medium", "high"],
            "allowed_safe_actions": sorted(_SAFE_AUTO_ACTIONS | {"manual_review", "none"}),
            "do_not_add_new_claims": True,
            "do_not_change_start_end_time": True,
        },
        "source_language": task.get("source_language"),
    }
    return [
        {
            "role": "system",
            "content": (
                "你是视频翻译音画同步复核员。复核程序候选与 Gemini 评估的问题是否成立，"
                "只保留 medium/high 且可安全处理的问题。输出 JSON。"
                "必须使用中文表述 reason、summary、timing_detail、recommendation。"
                "每个 accepted_issues 都要明确问题同步点、问题句子、音频时长偏差原因和处理建议；"
                "处理建议只能是音频变速、重写文案后重新生成音频、重新生成音频或人工复核。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def _resolve_llm_binding(use_case_code: str) -> tuple[str | None, str | None]:
    try:
        from appcore import llm_bindings

        binding = llm_bindings.resolve(use_case_code)
        return binding.get("provider"), binding.get("model")
    except Exception:
        try:
            from appcore.llm_use_cases import get_use_case

            use_case = get_use_case(use_case_code)
            return use_case.get("default_provider"), use_case.get("default_model")
        except Exception:
            return None, None


def _save_debug_payload(
    task_id: str,
    task_dir: str,
    *,
    phase: str,
    label: str,
    use_case_code: str,
    provider: str | None,
    model: str | None,
    messages: list[dict],
    request_payload: dict,
    input_snapshot: list[dict] | None = None,
) -> None:
    filename = f"av_sync_audit.{phase}.json"
    _save_json(
        task_dir,
        filename,
        prompt_file_payload(
            phase=phase,
            label=label,
            use_case_code=use_case_code,
            provider=provider,
            model=model,
            messages=messages,
            request_payload=request_payload,
            input_snapshot=input_snapshot,
        ),
    )
    task_state.add_llm_debug_ref(task_id, "av_sync_audit", {
        "id": f"av_sync_audit.{phase}",
        "label": label,
        "path": filename,
        "phase": phase,
        "use_case": use_case_code,
        "provider": provider,
        "model": model,
    })


def _reset_step_debug_refs(task_id: str, step: str = "av_sync_audit") -> None:
    task = task_state.get(task_id) or {}
    all_refs = dict(task.get("llm_debug_refs") or {})
    if step not in all_refs:
        return
    all_refs[step] = []
    task_state.update(task_id, llm_debug_refs=all_refs)


def _call_video_understand(
    runner,
    task_id: str,
    video_path: str,
    task_dir: str,
    task: dict,
    cfg: dict,
) -> dict:
    use_case_code = "omni_av_sync.understand"
    prompt = _build_video_understanding_prompt(task, cfg)
    system = (
        "你是短视频成片理解员。你的任务是观看视频并用中文写观察笔记，"
        "不要输出 JSON，不要做复杂结构化审计。"
    )
    provider, model = _resolve_llm_binding(use_case_code)
    media_input = None
    llm_video_path = video_path
    if video_path:
        # Docs-anchor:
        # docs/superpowers/specs/2026-05-14-llm-video-upload-optimization-design.md
        media_input = prepare_video_for_llm(
            video_path,
            REVIEW_480P_AUDIO,
        )
        llm_video_path = media_input.llm_path
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    request_payload = build_generate_request_payload(
        use_case_code=use_case_code,
        provider=provider,
        model=model,
        prompt=prompt,
        system=system,
        media=[llm_video_path] if llm_video_path else None,
        temperature=0.1,
        max_output_tokens=1800,
    )
    input_snapshot = []
    if media_input is not None:
        input_snapshot.append(media_debug_snapshot(media_input))
    else:
        input_snapshot.append(
            {
                "key": "video_path",
                "title": "合成成片视频",
                "content": video_path,
            }
        )
    _save_debug_payload(
        task_id,
        task_dir,
        phase="understand",
        label="Doubao 成片视频理解",
        use_case_code=use_case_code,
        provider=provider,
        model=model,
        messages=messages,
        request_payload=request_payload,
        input_snapshot=input_snapshot,
    )
    try:
        result = llm_client.invoke_generate(
            use_case_code,
            prompt=prompt,
            system=system,
            media=[llm_video_path] if llm_video_path else None,
            user_id=getattr(runner, "user_id", None),
            project_id=task_id,
            response_schema=None,
            temperature=0.1,
            max_output_tokens=1800,
        )
    except Exception as exc:  # noqa: BLE001 - keep program audit usable if video understanding fails
        return {"summary": "", "error": str(exc)[:500]}
    finally:
        cleanup_optimized_media(media_input)
    notes = str((result or {}).get("text") or "").strip() if isinstance(result, dict) else ""
    return {"summary": notes}


def _normalize_scorecard_timeline(raw: Any, *, require_rows: bool) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("Gemini scorecard response must contain timeline array")
    if require_rows and not raw:
        raise ValueError("Gemini scorecard response timeline is empty")

    normalized: list[dict] = []
    required_text = ("visual_observation", "diagnosis", "recommendation")
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Gemini scorecard timeline item must be an object")
        asr_index = _as_int_or_none(item.get("asr_index"))
        score = _as_float_or_none(item.get("sync_score"))
        if asr_index is None:
            raise ValueError("Gemini scorecard timeline item missing asr_index")
        if score is None:
            raise ValueError(f"Gemini scorecard ASR {asr_index} missing sync_score")
        row = dict(item)
        row["asr_index"] = asr_index
        row["sync_score"] = max(0.0, min(100.0, score))
        asr_text_zh = str(row.get("asr_text_zh") or "").strip()
        if asr_text_zh:
            row["asr_text_zh"] = asr_text_zh
        elif "asr_text_zh" in row:
            row.pop("asr_text_zh", None)
        for key in required_text:
            value = str(row.get(key) or "").strip()
            if not value:
                raise ValueError(f"Gemini scorecard ASR {asr_index} missing {key}")
            row[key] = value
        normalized.append(row)
    return normalized


def _call_sync_assess(
    runner,
    task_id: str,
    task_dir: str,
    task: dict,
    cfg: dict,
    sentences: list[dict],
    video_understanding: dict,
    program_candidates: list[dict],
) -> dict:
    use_case_code = "omni_av_sync.assess"
    messages = _build_assess_messages(video_understanding, task, cfg, sentences, program_candidates)
    provider, model = _ASSESS_PROVIDER, _ASSESS_MODEL
    is_scorecard = _is_multi_translate_report(cfg) or _is_report_only_scorecard(cfg)
    schema = _SCORECARD_SCHEMA if is_scorecard else _DIAGNOSIS_SCHEMA
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "omni_av_sync_assess", "schema": schema},
    }
    request_payload = build_chat_request_payload(
        use_case_code=use_case_code,
        provider=provider,
        model=model,
        messages=messages,
        response_format=response_format,
        temperature=0.1,
        max_tokens=4096,
    )
    _save_debug_payload(
        task_id,
        task_dir,
        phase="assess",
        label="Gemini 音画同步结构化评估",
        use_case_code=use_case_code,
        provider=provider,
        model=model,
        messages=messages,
        request_payload=request_payload,
        input_snapshot=[
            {
                "key": "video_understanding",
                "title": "Doubao 视频理解笔记",
                "content": json.dumps(video_understanding, ensure_ascii=False, indent=2),
            },
            {
                "key": "program_candidates",
                "title": "程序候选同步点",
                "content": json.dumps(program_candidates, ensure_ascii=False, indent=2),
            },
        ],
    )
    try:
        result = llm_client.invoke_chat(
            use_case_code,
            messages=messages,
            user_id=getattr(runner, "user_id", None),
            project_id=task_id,
            temperature=0.1,
            max_tokens=4096,
            response_format=response_format,
            provider_override=provider,
            model_override=model,
        )
    except Exception as exc:  # noqa: BLE001 - keep deterministic timing candidates visible
        if _is_report_only_scorecard(cfg):
            raise
        if _is_multi_translate_report(cfg):
            return {
                "issues": [],
                "timeline": [],
                "summary": "",
                "assess_error": str(exc)[:500],
                "video_understanding": video_understanding,
                "program_candidates": program_candidates,
            }
        return {
            "issues": [dict(issue) for issue in program_candidates[:5]],
            "summary": "Gemini 结构化评估失败，已保留程序候选同步点供复核。",
            "assess_error": str(exc)[:500],
            "video_understanding": video_understanding,
            "program_candidates": program_candidates,
        }
    diagnosis = _json_from_result(result, {"issues": [], "timeline": [], "summary": ""})
    if is_scorecard:
        diagnosis["timeline"] = _normalize_scorecard_timeline(
            diagnosis.get("timeline"),
            require_rows=_is_report_only_scorecard(cfg),
        )
        diagnosis["issues"] = []
        diagnosis["summary"] = ""
    else:
        if not diagnosis.get("issues") and isinstance(diagnosis.get("accepted_issues"), list):
            diagnosis["issues"] = [issue for issue in diagnosis.get("accepted_issues") or [] if isinstance(issue, dict)]
        if not diagnosis.get("issues") and program_candidates:
            diagnosis["issues"] = [dict(issue) for issue in program_candidates[:5]]
            diagnosis["summary"] = (
                "程序按最终字幕/TTS 时间线检测到候选同步风险；结构化评估未返回问题，"
                "已保留程序候选供复核。"
            )
    diagnosis.setdefault("issues", [])
    diagnosis.setdefault("summary", "")
    diagnosis["video_understanding"] = video_understanding
    diagnosis["program_candidates"] = program_candidates
    return diagnosis


def _call_diagnose(
    runner,
    task_id: str,
    video_path: str,
    task_dir: str,
    task: dict,
    cfg: dict,
    sentences: list[dict],
) -> dict:
    video_understanding = _call_video_understand(runner, task_id, video_path, task_dir, task, cfg)
    program_candidates = _program_sync_candidates(sentences)
    return _call_sync_assess(
        runner,
        task_id,
        task_dir,
        task,
        cfg,
        sentences,
        video_understanding,
        program_candidates,
    )


def _call_verify(
    runner,
    task_id: str,
    task_dir: str,
    task: dict,
    cfg: dict,
    sentences: list[dict],
    diagnosis: dict,
) -> dict:
    use_case_code = "omni_av_sync.verify"
    messages = _build_verify_messages(diagnosis, task, cfg, sentences)
    provider, model = _resolve_llm_binding(use_case_code)
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "omni_av_sync_verify", "schema": _VERIFY_SCHEMA},
    }
    request_payload = build_chat_request_payload(
        use_case_code=use_case_code,
        provider=provider,
        model=model,
        messages=messages,
        response_format=response_format,
        temperature=0.1,
        max_tokens=4096,
    )
    _save_debug_payload(
        task_id,
        task_dir,
        phase="verify",
        label="Gemini 音画同步复核",
        use_case_code=use_case_code,
        provider=provider,
        model=model,
        messages=messages,
        request_payload=request_payload,
        input_snapshot=[
            {
                "key": "diagnosis",
                "title": "诊断结果",
                "content": json.dumps(diagnosis, ensure_ascii=False, indent=2),
            },
        ],
    )
    result = llm_client.invoke_chat(
        use_case_code,
        messages=messages,
        user_id=getattr(runner, "user_id", None),
        project_id=task_id,
        temperature=0.1,
        max_tokens=4096,
        response_format=response_format,
    )
    return _json_from_result(result, {"accepted_issues": [], "rejected_count": 0, "summary": ""})


def _base_report(mode: str) -> dict:
    return {
        "title": "音画同步审计",
        "mode": mode,
        "status": "done",
        "diagnosis": {"issues": [], "summary": ""},
        "verification": {"accepted_issues": [], "rejected_count": 0, "summary": ""},
        "applied_fixes": [],
        "summary": {
            "diagnosed": 0,
            "accepted": 0,
            "applied": 0,
            "rolled_back": 0,
            "manual_review": 0,
        },
    }


def _format_sync_time(value: Any) -> str:
    seconds = _as_float_or_none(value)
    if seconds is None:
        return "--:--.--"
    minutes = int(max(0.0, seconds) // 60)
    remainder = max(0.0, seconds) - minutes * 60
    return f"{minutes:02d}:{remainder:05.2f}"


def _sentences_by_asr_index(sentences: list[dict] | None) -> dict[int, dict]:
    indexed: dict[int, dict] = {}
    for pos, sentence in enumerate(sentences or []):
        if not isinstance(sentence, dict):
            continue
        asr_index = _as_int_or_none(sentence.get("asr_index"))
        if asr_index is None:
            asr_index = _as_int_or_none(sentence.get("index"))
        if asr_index is None:
            asr_index = pos
        indexed[asr_index] = sentence
    return indexed


def _report_sentence_text(sentence: dict | None, issue: dict) -> str:
    if sentence:
        text = str(
            sentence.get("text")
            or sentence.get("translated")
            or sentence.get("tts_text")
            or ""
        ).strip()
        if text:
            return text
    return str(issue.get("sentence_text") or issue.get("final_text") or issue.get("suggested_text") or "").strip()


def _timing_snapshot(sentence: dict | None) -> tuple[float | None, float | None, float | None, float | None]:
    if not sentence:
        return None, None, None, None
    target_duration = _as_float_or_none(sentence.get("target_duration"))
    start_time = _as_float_or_none(sentence.get("start_time"))
    end_time = _as_float_or_none(sentence.get("end_time"))
    if target_duration is None and start_time is not None and end_time is not None and end_time > start_time:
        target_duration = round(end_time - start_time, 4)
    tts_duration = _as_float_or_none(sentence.get("tts_duration"))
    duration_ratio = _as_float_or_none(sentence.get("duration_ratio"))
    if duration_ratio is None and target_duration and tts_duration:
        duration_ratio = _ratio(target_duration, tts_duration)
    return start_time, end_time, target_duration, tts_duration if tts_duration is not None else None


def _timing_detail(target_duration: float | None, tts_duration: float | None) -> tuple[str, str, float | None, float | None]:
    if target_duration is None or tts_duration is None or target_duration <= 0:
        return "缺少完整时长数据，需人工核对该同步点。", "时长数据不完整，无法自动判断是否画面对不上。", None, None
    delta = round(tts_duration - target_duration, 2)
    ratio = round(tts_duration / target_duration, 4)
    ratio_pct = round(ratio * 100)
    if delta > 0.05:
        detail = f"目标画面 {target_duration:.2f}s，TTS 音频 {tts_duration:.2f}s，音频太长 {delta:.2f}s（{ratio_pct}%）"
        reason = "这句音频太长，后半句容易拖到下一个画面或同步点，导致画面对不上。"
    elif delta < -0.05:
        detail = f"目标画面 {target_duration:.2f}s，TTS 音频 {tts_duration:.2f}s，音频太短 {abs(delta):.2f}s（{ratio_pct}%）"
        reason = "这句音频太短，画面动作还没结束时旁白已经结束，容易导致画面对不上。"
    else:
        detail = f"目标画面 {target_duration:.2f}s，TTS 音频 {tts_duration:.2f}s，时长基本匹配（{ratio_pct}%）"
        reason = "句子时长接近画面窗口，如仍有不适配，多半来自语义或画面动作匹配问题。"
    return detail, reason, delta, ratio


def _recommendation_for_issue(issue: dict, delta: float | None, ratio: float | None) -> str:
    action = str(issue.get("safe_action") or "").strip()
    if action == "manual_review":
        return "建议人工复核该同步点，再决定是否重写文案或重新生成音频。"
    if action == "regenerate_tts":
        return "建议保持文案不变，重新生成音频；如果新音频仍有小幅偏差，再考虑音频变速。"
    if action == "expand_text":
        if ratio is not None and ratio < 0.9:
            return "不建议只靠音频变速；建议重写/扩写文案后重新生成音频，让旁白覆盖完整画面动作。"
        return "建议优先尝试音频变速（小幅减速）；如仍对不上，再重写/扩写文案后重新生成音频。"
    if action == "shorten_text":
        if ratio is not None and ratio > 1.1:
            return "不建议只靠音频变速；建议重写/压缩文案后重新生成音频，减少句子拖到下一画面的风险。"
        return "建议优先尝试音频变速（小幅加速）；如仍对不上，再重写/压缩文案后重新生成音频。"
    if delta is not None and delta > 0.05:
        return "建议先评估音频变速是否足够；如果偏差超过小幅变速范围，应重写/压缩文案后重新生成音频。"
    if delta is not None and delta < -0.05:
        return "建议先评估音频变速是否足够；如果偏差超过小幅变速范围，应重写/扩写文案后重新生成音频。"
    return "建议保持现有音频；如现场观感仍不对，重新生成音频并人工复核。"


def _enrich_issue_for_report(issue: dict, sentence_by_asr: dict[int, dict]) -> dict:
    enriched = dict(issue)
    asr_index = _as_int_or_none(enriched.get("asr_index"))
    sentence = sentence_by_asr.get(asr_index) if asr_index is not None else None
    start_time, end_time, target_duration, tts_duration = _timing_snapshot(sentence)
    if asr_index is not None:
        if start_time is not None and end_time is not None:
            sync_point = f"ASR {asr_index}（{_format_sync_time(start_time)}-{_format_sync_time(end_time)}）"
        else:
            sync_point = f"ASR {asr_index}"
        enriched["sync_point"] = sync_point
    sentence_text = _report_sentence_text(sentence, enriched)
    if sentence_text:
        enriched["sentence_text"] = sentence_text
    if sentence:
        source_text = str(sentence.get("source_text") or "").strip()
        if source_text and not enriched.get("source_text"):
            enriched["source_text"] = source_text
    timing_detail, mismatch_reason, delta, ratio = _timing_detail(target_duration, tts_duration)
    enriched["timing_detail"] = timing_detail
    enriched["mismatch_reason"] = mismatch_reason
    enriched["recommendation"] = _recommendation_for_issue(enriched, delta, ratio)
    if target_duration is not None:
        enriched["target_duration"] = round(target_duration, 4)
    if tts_duration is not None:
        enriched["tts_duration"] = round(tts_duration, 4)
    if delta is not None:
        enriched["duration_delta"] = delta
    if ratio is not None:
        enriched["duration_ratio"] = ratio
    return enriched


def _build_human_report(report: dict) -> str:
    summary = report.get("summary") or {}
    diagnosis = report.get("diagnosis") or {}
    verification = report.get("verification") or {}
    accepted = verification.get("accepted_issues") or []
    diagnosed = diagnosis.get("issues") or []
    issues = accepted or diagnosed
    if report.get("analysis_only"):
        summary_line = (
            f"诊断问题 {summary.get('diagnosed', 0)} 个，仅做辅助分析，不执行复核或自动修改。"
        )
    else:
        summary_line = (
            f"诊断问题 {summary.get('diagnosed', 0)} 个，复核确认 {summary.get('accepted', 0)} 个。"
        )
    lines = [
        "音画同步审计结论",
        f"模式：{report.get('mode') or '-'}；状态：{report.get('status') or '-'}；{summary_line}",
    ]
    if not issues:
        if report.get("analysis_only"):
            lines.append("未发现明显需要关注的音画同步候选问题。")
        else:
            lines.append("未确认需要处理的音画同步问题。")
        return "\n".join(lines)
    lines.append("确认问题如下：" if accepted else "候选问题如下：")
    for idx, issue in enumerate(issues, 1):
        lines.extend([
            f"{idx}. 问题同步点：{issue.get('sync_point') or '-'}",
            f"   问题句子：{issue.get('sentence_text') or '-'}",
            f"   时长证据：{issue.get('timing_detail') or '-'}",
            f"   问题说明：{issue.get('mismatch_reason') or issue.get('reason') or issue.get('evidence') or '-'}",
            f"   处理建议：{issue.get('recommendation') or '-'}",
        ])
    return "\n".join(lines)


def _readable_problem(issue: dict) -> str:
    mismatch_reason = str(issue.get("mismatch_reason") or "").strip()
    timing_detail = str(issue.get("timing_detail") or "").strip()
    if "音频太长" in mismatch_reason or "音频太长" in timing_detail:
        return "音频太长，容易拖到下一个画面，导致画面对不上"
    if "音频太短" in mismatch_reason or "音频太短" in timing_detail:
        return "音频偏短，旁白提前结束，画面后半段容易空出来"
    problem_type = str(issue.get("problem_type") or "").strip()
    return _PROBLEM_TYPE_LABELS.get(problem_type, problem_type or "音画同步风险")


def _readable_findings_from_report(report: dict) -> list[dict]:
    diagnosis = report.get("diagnosis") or {}
    verification = report.get("verification") or {}
    accepted = [item for item in verification.get("accepted_issues") or [] if isinstance(item, dict)]
    diagnosed = [item for item in diagnosis.get("issues") or [] if isinstance(item, dict)]
    accepted_asr = {_as_int_or_none(item.get("asr_index")) for item in accepted}
    accepted_asr.discard(None)
    issues = accepted + [
        item for item in diagnosed
        if _as_int_or_none(item.get("asr_index")) not in accepted_asr
    ]
    findings: list[dict] = []
    for issue in issues:
        severity = str(issue.get("severity") or "").lower()
        findings.append({
            "asr_index": _as_int_or_none(issue.get("asr_index")),
            "sync_point": issue.get("sync_point") or "",
            "severity": severity,
            "severity_label": _SEVERITY_LABELS.get(severity, severity.upper() if severity else "未分级"),
            "verified": issue in accepted,
            "problem": _readable_problem(issue),
            "timing": issue.get("timing_detail") or "",
            "sentence_text": issue.get("sentence_text") or "",
            "source_text": issue.get("source_text") or "",
            "recommendation": issue.get("recommendation") or "",
            "evidence": issue.get("evidence") or issue.get("reason") or "",
            "suggested_text": issue.get("final_text") or issue.get("suggested_text") or "",
        })
    severity_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        findings,
        key=lambda item: (
            severity_order.get(item.get("severity") or "", 3),
            item.get("asr_index") if item.get("asr_index") is not None else 999999,
        ),
    )


def _report_issues_for_timeline(report: dict) -> dict[int, dict]:
    diagnosis = report.get("diagnosis") or {}
    verification = report.get("verification") or {}
    issues_by_asr: dict[int, dict] = {}
    for item in diagnosis.get("issues") or []:
        if not isinstance(item, dict):
            continue
        asr_index = _as_int_or_none(item.get("asr_index"))
        if asr_index is not None:
            issues_by_asr[asr_index] = item
    for item in verification.get("accepted_issues") or []:
        if not isinstance(item, dict) or not item.get("accepted", True):
            continue
        asr_index = _as_int_or_none(item.get("asr_index"))
        if asr_index is not None:
            issues_by_asr[asr_index] = item
    return issues_by_asr


def _timeline_hints_by_asr(report: dict) -> dict[int, dict]:
    diagnosis = report.get("diagnosis") or {}
    raw_entries = []
    if isinstance(diagnosis, dict):
        raw_entries.extend(diagnosis.get("timeline") or [])
    raw_entries.extend(report.get("timeline") or [])
    raw_entries.extend(report.get("audit_timeline") or [])
    hints: dict[int, dict] = {}
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        asr_index = _as_int_or_none(item.get("asr_index"))
        if asr_index is not None:
            hints[asr_index] = item
    return hints


def _shot_notes_by_asr(task: dict | None) -> dict[int, dict]:
    notes = (task or {}).get("shot_notes") or {}
    if not isinstance(notes, dict):
        return {}
    result: dict[int, dict] = {}
    for note in notes.get("sentences") or []:
        if not isinstance(note, dict):
            continue
        asr_index = _as_int_or_none(note.get("asr_index"))
        if asr_index is not None:
            result[asr_index] = note
    return result


def _format_visual_context(note: dict | None) -> str:
    if not isinstance(note, dict):
        return ""
    parts: list[str] = []
    for key in ("scene", "action", "shot_type", "emotion_hint"):
        value = str(note.get(key) or "").strip()
        if value:
            parts.append(value)
    on_screen = note.get("on_screen_text")
    if isinstance(on_screen, list):
        text = "、".join(str(item).strip() for item in on_screen if str(item).strip())
    else:
        text = str(on_screen or "").strip()
    if text:
        parts.append(f"屏幕文字：{text}")
    if not parts and note.get("product_visible") is not None:
        parts.append("产品可见" if note.get("product_visible") else "产品未出现在画面中")
    return "；".join(parts)


def _visual_observation_for_timeline(issue: dict | None, hint: dict | None, shot_note: dict | None) -> str:
    for source in (issue, hint):
        if not isinstance(source, dict):
            continue
        for key in (
            "visual_observation",
            "actual_visual",
            "visual_context",
            "visual_summary",
            "scene_description",
            "scene",
        ):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return _format_visual_context(shot_note) or "未记录画面描述，请结合成片人工复核。"


def _sync_score_for_timeline(source: dict | None) -> float | None:
    if not isinstance(source, dict):
        return None
    score = source.get("sync_score")
    if score is None or score == "":
        score = source.get("score")
    return _as_float_or_none(score)


def _asr_chinese_reference_for_timeline(
    task: dict | None,
    *sources: dict | None,
) -> str:
    if not _needs_asr_chinese_reference(task):
        return ""
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("asr_text_zh", "source_text_zh", "source_translation_zh", "asr_chinese"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _build_audit_timeline(report: dict, sentences: list[dict] | None, task: dict | None) -> list[dict]:
    issues_by_asr = _report_issues_for_timeline(report)
    hints_by_asr = _timeline_hints_by_asr(report)
    notes_by_asr = _shot_notes_by_asr(task)
    accepted_asr = {
        _as_int_or_none(item.get("asr_index"))
        for item in (report.get("verification") or {}).get("accepted_issues", [])
        if isinstance(item, dict) and item.get("accepted", True)
    }
    accepted_asr.discard(None)
    rows: list[dict] = []
    seen: set[int] = set()

    for pos, sentence in enumerate(sentences or []):
        if not isinstance(sentence, dict):
            continue
        asr_index = _as_int_or_none(sentence.get("asr_index"))
        if asr_index is None:
            asr_index = _as_int_or_none(sentence.get("index"))
        if asr_index is None:
            asr_index = pos
        seen.add(asr_index)
        issue = issues_by_asr.get(asr_index)
        hint = hints_by_asr.get(asr_index)
        start_time, end_time, target_duration, tts_duration = _timing_snapshot(sentence)
        sync_score = _sync_score_for_timeline(issue)
        if sync_score is None:
            sync_score = _sync_score_for_timeline(hint)
        sync_point = (
            f"ASR {asr_index}（{_format_sync_time(start_time)}-{_format_sync_time(end_time)}）"
            if start_time is not None and end_time is not None
            else f"ASR {asr_index}"
        )
        row = {
            "asr_index": asr_index,
            "source_segment_indices": sentence.get("source_segment_indices") or [],
            "sync_point": sync_point,
            "start_time": start_time,
            "end_time": end_time,
            "target_duration": target_duration,
            "tts_duration": tts_duration,
            "duration_ratio": _as_float_or_none(sentence.get("duration_ratio")),
            "asr_text": str(sentence.get("source_text") or "").strip(),
            "target_text": _report_sentence_text(sentence, issue or hint or {}),
            "visual_observation": _visual_observation_for_timeline(issue, hint, notes_by_asr.get(asr_index)),
            "sync_score": sync_score,
            "diagnosis_status": "issue" if issue else "ok",
            "verified": bool(issue and asr_index in accepted_asr),
            "severity": str((issue or {}).get("severity") or "").lower(),
            "severity_label": "",
            "problem": "",
            "diagnosis": "",
            "timing": "",
            "recommendation": "",
            "evidence": "",
            "suggested_text": "",
        }
        asr_text_zh = _asr_chinese_reference_for_timeline(task, hint, issue, sentence)
        if asr_text_zh:
            row["asr_text_zh"] = asr_text_zh
        if row["severity"]:
            row["severity_label"] = _SEVERITY_LABELS.get(row["severity"], row["severity"].upper())
        if issue:
            row.update({
                "problem": _readable_problem(issue),
                "diagnosis": (
                    str(
                        (hint or {}).get("diagnosis")
                        or issue.get("diagnosis")
                        or issue.get("reason")
                        or issue.get("mismatch_reason")
                        or issue.get("evidence")
                        or ""
                    ).strip()
                    or _readable_problem(issue)
                ),
                "timing": issue.get("timing_detail") or "",
                "recommendation": (hint or {}).get("recommendation") or issue.get("recommendation") or "",
                "evidence": issue.get("evidence") or issue.get("reason") or "",
                "suggested_text": issue.get("final_text") or issue.get("suggested_text") or "",
            })
        else:
            row["diagnosis"] = str((hint or {}).get("diagnosis") or "").strip() or "未发现明显同步风险。"
            row["recommendation"] = str((hint or {}).get("recommendation") or "").strip()
        rows.append(row)

    for asr_index, issue in issues_by_asr.items():
        if asr_index in seen:
            continue
        hint = hints_by_asr.get(asr_index)
        sync_score = _sync_score_for_timeline(issue)
        if sync_score is None:
            sync_score = _sync_score_for_timeline(hint)
        issue_row = {
            "asr_index": asr_index,
            "source_segment_indices": [],
            "sync_point": issue.get("sync_point") or f"ASR {asr_index}",
            "start_time": None,
            "end_time": None,
            "target_duration": issue.get("target_duration"),
            "tts_duration": issue.get("tts_duration"),
            "duration_ratio": issue.get("duration_ratio"),
            "asr_text": issue.get("source_text") or "",
            "target_text": issue.get("sentence_text") or "",
            "visual_observation": _visual_observation_for_timeline(issue, hint, notes_by_asr.get(asr_index)),
            "sync_score": sync_score,
            "diagnosis_status": "issue",
            "verified": bool(asr_index in accepted_asr),
            "severity": str(issue.get("severity") or "").lower(),
            "severity_label": _SEVERITY_LABELS.get(str(issue.get("severity") or "").lower(), str(issue.get("severity") or "").upper()),
            "problem": _readable_problem(issue),
            "diagnosis": str(
                (hint or {}).get("diagnosis")
                or issue.get("diagnosis")
                or issue.get("reason")
                or issue.get("mismatch_reason")
                or issue.get("evidence")
                or ""
            ).strip(),
            "timing": issue.get("timing_detail") or "",
            "recommendation": (hint or {}).get("recommendation") or issue.get("recommendation") or "",
            "evidence": issue.get("evidence") or issue.get("reason") or "",
            "suggested_text": issue.get("final_text") or issue.get("suggested_text") or "",
        }
        asr_text_zh = _asr_chinese_reference_for_timeline(task, hint, issue)
        if asr_text_zh:
            issue_row["asr_text_zh"] = asr_text_zh
        rows.append(issue_row)

    return sorted(
        rows,
        key=lambda row: (
            row.get("start_time") is None,
            row.get("start_time") if row.get("start_time") is not None else 0,
            row.get("asr_index") if row.get("asr_index") is not None else 999999,
        ),
    )


def _attach_readable_report(report: dict) -> None:
    findings = _readable_findings_from_report(report)
    report["readable_findings"] = findings
    if not findings:
        report["readable_summary"] = "中文审计结论：未发现需要处理的音画同步点。"
        return
    verified_count = sum(1 for item in findings if item.get("verified"))
    lead = findings[0]
    if report.get("analysis_only"):
        report["readable_summary"] = (
            f"中文审计结论：发现 {len(findings)} 个需要关注的同步点，本结果仅作辅助分析。"
            f"优先关注 {lead.get('sync_point') or '未知同步点'}："
            f"{lead.get('problem') or '音画同步风险'}。处理建议：{lead.get('recommendation') or '人工复核后处理'}"
        )
    else:
        report["readable_summary"] = (
            f"中文审计结论：发现 {len(findings)} 个需要关注的同步点，"
            f"其中 {verified_count} 个已由复核确认。优先处理 {lead.get('sync_point') or '未知同步点'}："
            f"{lead.get('problem') or '音画同步风险'}。处理建议：{lead.get('recommendation') or '人工复核后处理'}"
        )


def _finalize_report_for_display(report: dict, sentences: list[dict] | None, task: dict | None = None) -> None:
    sentence_by_asr = _sentences_by_asr_index(sentences)
    diagnosis = report.get("diagnosis")
    if isinstance(diagnosis, dict):
        diagnosis["issues"] = [
            _enrich_issue_for_report(issue, sentence_by_asr) if isinstance(issue, dict) else issue
            for issue in diagnosis.get("issues") or []
        ]
    verification = report.get("verification")
    if isinstance(verification, dict):
        verification["accepted_issues"] = [
            _enrich_issue_for_report(issue, sentence_by_asr) if isinstance(issue, dict) else issue
            for issue in verification.get("accepted_issues") or []
        ]
    report["audit_timeline"] = _build_audit_timeline(report, sentences, task)
    report["human_report"] = _build_human_report(report)
    _attach_readable_report(report)


def _ensure_report_preview_items(report: dict) -> None:
    summary = report.get("summary") or {}
    diagnosis = report.get("diagnosis") or {}
    verification = report.get("verification") or {}
    preview = {
        "status": report.get("status"),
        "mode": report.get("mode"),
        "summary": summary,
        "readable_summary": report.get("readable_summary"),
        "readable_findings": report.get("readable_findings") or [],
        "diagnosis_summary": diagnosis.get("summary"),
        "verification_summary": verification.get("summary"),
        "audit_timeline": report.get("audit_timeline") or [],
        "diagnosis_issues": diagnosis.get("issues") or [],
        "accepted_issues": verification.get("accepted_issues") or [],
        "applied_fixes": report.get("applied_fixes") or [],
    }
    lines = [
        f"模式：{report.get('mode') or '-'}",
        f"状态：{report.get('status') or '-'}",
        f"诊断问题：{summary.get('diagnosed', 0)}",
        "辅助分析：是" if report.get("analysis_only") else f"复核通过：{summary.get('accepted', 0)}",
        f"已应用修正：{summary.get('applied', 0)}",
        f"回滚：{summary.get('rolled_back', 0)}",
        f"人工复核：{summary.get('manual_review', 0)}",
    ]
    if diagnosis.get("summary"):
        lines.append(f"诊断摘要：{diagnosis.get('summary')}")
    if verification.get("summary"):
        lines.append(f"复核摘要：{verification.get('summary')}")
    if report.get("readable_summary"):
        lines.append(str(report.get("readable_summary")))
    report["items"] = [
        {"type": "text", "label": "中文审计结论", "content": report.get("human_report") or "\n".join(lines)},
        {
            "type": "text",
            "label": "结构化结果",
            "content": json.dumps(preview, ensure_ascii=False, indent=2),
        },
    ]


def _store_report(task_id: str, report: dict, *, variant: str = "av", sentences: list[dict] | None = None) -> None:
    task = task_state.get(task_id) or {}
    _finalize_report_for_display(report, sentences, task)
    _ensure_report_preview_items(report)
    variants = dict(task.get("variants") or {})
    variant_state = dict(variants.get(variant) or {})
    variant_state["av_sync_audit"] = report
    variants[variant] = variant_state
    task_state.update(task_id, variants=variants)
    task_state.set_artifact(task_id, "av_sync_audit", report)


def _as_float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _segment_indices(segment: dict) -> list[int]:
    raw = segment.get("source_segment_indices")
    if isinstance(raw, list):
        indexes = [_as_int_or_none(item) for item in raw]
        return [idx for idx in indexes if idx is not None]
    idx = _as_int_or_none(raw)
    if idx is not None:
        return [idx]
    idx = _as_int_or_none(segment.get("index"))
    return [idx] if idx is not None else []


def _script_segment_index(script_segments: list[dict]) -> dict[int, dict]:
    indexed: dict[int, dict] = {}
    for pos, segment in enumerate(script_segments or []):
        if not isinstance(segment, dict):
            continue
        idx = _as_int_or_none(segment.get("index"))
        indexed[pos if idx is None else idx] = segment
    return indexed


def _source_window(source_segments: list[dict]) -> tuple[float | None, float | None]:
    starts = [
        value for value in (_as_float_or_none(seg.get("start_time")) for seg in source_segments)
        if value is not None
    ]
    ends = [
        value for value in (_as_float_or_none(seg.get("end_time")) for seg in source_segments)
        if value is not None
    ]
    if not starts or not ends:
        return None, None
    return min(starts), max(ends)


def _subtitle_chunks_for_variant(task: dict, variant: str) -> list[dict]:
    variants = task.get("variants") or {}
    variant_state = variants.get(variant) or {}
    corrected = variant_state.get("corrected_subtitle") or task.get("corrected_subtitle") or {}
    if not isinstance(corrected, dict):
        return []
    chunks = corrected.get("chunks") or []
    return [chunk for chunk in chunks if isinstance(chunk, dict)]


def _chunk_index_values(chunk: dict, key: str) -> set[int]:
    raw = chunk.get(key)
    if isinstance(raw, list):
        values = [_as_int_or_none(item) for item in raw]
        return {value for value in values if value is not None}
    value = _as_int_or_none(raw)
    return {value} if value is not None else set()


def _matching_subtitle_chunks(
    sentence: dict,
    subtitle_chunks: list[dict],
    position: int,
) -> list[dict]:
    if not subtitle_chunks:
        return []
    source_indices = set(sentence.get("source_segment_indices") or [])
    source_matches = [
        chunk for chunk in subtitle_chunks
        if source_indices and source_indices.intersection(_chunk_index_values(chunk, "source_segment_indices"))
    ]
    if source_matches:
        return source_matches
    sentence_matches = [
        chunk for chunk in subtitle_chunks
        if position in _chunk_index_values(chunk, "sentence_indices")
    ]
    if sentence_matches:
        return sentence_matches
    start_time = _as_float_or_none(sentence.get("start_time"))
    end_time = _as_float_or_none(sentence.get("end_time"))
    if start_time is not None and end_time is not None:
        time_matches = []
        for chunk in subtitle_chunks:
            chunk_start = _as_float_or_none(chunk.get("start_time"))
            chunk_end = _as_float_or_none(chunk.get("end_time"))
            if chunk_start is None or chunk_end is None:
                continue
            if min(end_time, chunk_end) - max(start_time, chunk_start) > 0:
                time_matches.append(chunk)
        if time_matches:
            return time_matches
    if position < len(subtitle_chunks):
        return [subtitle_chunks[position]]
    return []


def _subtitle_window(chunks: list[dict]) -> tuple[float | None, float | None]:
    starts = [
        value for value in (_as_float_or_none(chunk.get("start_time")) for chunk in chunks)
        if value is not None
    ]
    ends = [
        value for value in (_as_float_or_none(chunk.get("end_time")) for chunk in chunks)
        if value is not None
    ]
    if not starts or not ends:
        return None, None
    return min(starts), max(ends)


def _normal_report_sentences(task: dict, *, variant: str = "normal") -> list[dict]:
    variants = task.get("variants") or {}
    variant_state = variants.get(variant) or {}
    tts_segments = variant_state.get("segments") or task.get("segments") or []
    script_by_index = _script_segment_index(task.get("script_segments") or [])
    subtitle_chunks = _subtitle_chunks_for_variant(task, variant)
    report_sentences: list[dict] = []
    for pos, segment in enumerate(tts_segments):
        if not isinstance(segment, dict):
            continue
        source_indices = _segment_indices(segment)
        source_segments = [script_by_index[idx] for idx in source_indices if idx in script_by_index]
        start_time, end_time = _source_window(source_segments)
        if start_time is None:
            start_time = _as_float_or_none(segment.get("start_time"))
        if end_time is None:
            end_time = _as_float_or_none(segment.get("end_time"))
        target_duration = _as_float_or_none(segment.get("target_duration"))
        if target_duration is None and start_time is not None and end_time is not None and end_time > start_time:
            target_duration = round(end_time - start_time, 4)
        tts_duration = _as_float_or_none(segment.get("tts_duration"))
        if target_duration is None:
            target_duration = tts_duration or 0.0
        if tts_duration is None:
            tts_duration = 0.0
        source_text = " ".join(
            str(seg.get("text") or "").strip()
            for seg in source_segments
            if str(seg.get("text") or "").strip()
        ).strip() or str(segment.get("text") or "").strip()
        translated = str(
            segment.get("tts_text")
            or segment.get("translated")
            or segment.get("text")
            or ""
        ).strip()
        asr_index = source_indices[0] if source_indices else (_as_int_or_none(segment.get("index")) or pos)
        sentence = {
            "asr_index": asr_index,
            "source_segment_indices": source_indices,
            "start_time": start_time,
            "end_time": end_time,
            "target_duration": target_duration,
            "source_text": source_text,
            "text": translated,
            "translated": translated,
            "tts_duration": tts_duration,
            "duration_ratio": _safe_float(
                segment.get("duration_ratio"),
                _ratio(float(target_duration or 0.0), float(tts_duration or 0.0)),
            ),
            "speed": _safe_float(segment.get("speed"), 1.0),
            "status": segment.get("status") or "report_only",
            "tts_path": segment.get("tts_path"),
        }
        matched_subtitles = _matching_subtitle_chunks(sentence, subtitle_chunks, pos)
        if matched_subtitles:
            subtitle_text = " ".join(
                str(chunk.get("text") or "").strip()
                for chunk in matched_subtitles
                if str(chunk.get("text") or "").strip()
            ).strip()
            subtitle_start, subtitle_end = _subtitle_window(matched_subtitles)
            if subtitle_text:
                sentence["subtitle_text"] = subtitle_text
            if subtitle_start is not None:
                sentence["subtitle_start_time"] = subtitle_start
            if subtitle_end is not None:
                sentence["subtitle_end_time"] = subtitle_end
        report_sentences.append(sentence)
    return report_sentences


def _multi_report_config(task: dict) -> dict:
    return {
        "av_sync_audit": "report_only",
        "project_type": task.get("type") or "multi_translate",
        "report_variant": "normal",
        "translate_algo": "multi_translate_default",
        "tts_strategy": "five_round_rewrite",
        "subtitle": "asr_realign",
        "target_lang": task.get("target_lang") or task.get("target_language"),
    }


def _candidate_issues(verification: dict) -> list[dict]:
    result: list[dict] = []
    seen: set[int] = set()
    for issue in verification.get("accepted_issues") or []:
        if not isinstance(issue, dict) or not issue.get("accepted", True):
            continue
        if str(issue.get("severity") or "").lower() not in _APPLY_SEVERITIES:
            continue
        try:
            asr_index = int(issue.get("asr_index"))
        except (TypeError, ValueError):
            continue
        if asr_index in seen:
            continue
        seen.add(asr_index)
        result.append(issue)
    return result


def _regenerate_sentence_tts(runner, task: dict, task_dir: str, sentence: dict, fix_index: int) -> dict:
    av_inputs = runner._resolve_av_inputs(task)
    target_language = av_inputs.get("target_language")
    voice, tts_voice_id, _speech_rate_voice_id = runner._resolve_av_voice(task)
    engine = runner.profile.get_tts_engine()
    segment = _build_av_tts_segments([sentence])[0]
    result = engine.synthesize_full(
        [segment],
        tts_voice_id or voice.get("id"),
        task_dir,
        variant=f"av_sync_fix_{fix_index}",
        language_code=target_language,
    )
    segments = result.get("segments") or []
    if not segments:
        raise RuntimeError("TTS 修正未返回句段")
    return dict(segments[0])


def _apply_safe_auto(runner, task_id: str, task_dir: str, report: dict, cfg: dict, sentences: list[dict]) -> list[dict]:
    if not _is_sentence_chain(cfg):
        return sentences

    task = task_state.get(task_id) or {}
    final_sentences = [dict(sentence) for sentence in sentences]
    by_asr_index = {
        int(sentence.get("asr_index")): idx
        for idx, sentence in enumerate(final_sentences)
        if sentence.get("asr_index") is not None
    }
    max_fixes = _max_auto_fix_count(len(final_sentences))
    applied_count = 0

    for issue in _candidate_issues(report["verification"]):
        if applied_count >= max_fixes:
            break
        action = str(issue.get("safe_action") or "none")
        if action == "manual_review":
            report["summary"]["manual_review"] += 1
            report["applied_fixes"].append({
                "asr_index": issue.get("asr_index"),
                "action": action,
                "status": "manual_review",
                "reason": issue.get("reason") or "Gemini 要求人工复核",
            })
            continue
        if action not in _SAFE_AUTO_ACTIONS:
            continue

        asr_index = int(issue["asr_index"])
        sentence_idx = by_asr_index.get(asr_index)
        if sentence_idx is None:
            continue

        before = dict(final_sentences[sentence_idx])
        candidate = dict(before)
        before_text = str(before.get("text") or "")
        final_text = str(issue.get("final_text") or issue.get("suggested_text") or before_text).strip()
        if action in {"shorten_text", "expand_text"} and not final_text:
            continue
        candidate["text"] = final_text if action in {"shorten_text", "expand_text"} else before_text

        fix_record = {
            "asr_index": asr_index,
            "action": action,
            "before_text": before_text,
            "after_text": candidate["text"],
            "before_tts_duration": _safe_float(before.get("tts_duration")),
            "after_tts_duration": None,
            "before_duration_ratio": _safe_float(before.get("duration_ratio"), _ratio(
                _safe_float(before.get("target_duration")),
                _safe_float(before.get("tts_duration")),
            )),
            "after_duration_ratio": None,
            "status": "rolled_back",
            "reason": "",
        }

        try:
            tts_segment = _regenerate_sentence_tts(runner, task, task_dir, candidate, applied_count)
            after_duration = _safe_float(tts_segment.get("tts_duration"))
            target_duration = _safe_float(before.get("target_duration"))
            after_ratio = _ratio(target_duration, after_duration)
            speed = _safe_float(tts_segment.get("speed"), _safe_float(before.get("speed"), 1.0))
            candidate.update({
                "tts_duration": after_duration,
                "duration_ratio": after_ratio,
                "tts_path": tts_segment.get("tts_path") or before.get("tts_path"),
                "speed": speed,
                "status": "av_sync_fixed",
            })
            fix_record["after_tts_duration"] = after_duration
            fix_record["after_duration_ratio"] = after_ratio
            safer = (
                _MIN_SAFE_RATIO <= after_ratio <= _MAX_SAFE_RATIO
                or _distance_from_one(after_ratio) < _distance_from_one(fix_record["before_duration_ratio"])
            )
            speed_safe = _MIN_SAFE_SPEED <= speed <= _MAX_SAFE_SPEED
            if not safer or not speed_safe:
                fix_record["status"] = "rolled_back_not_safer"
                fix_record["reason"] = "修正后时长或语速不在安全范围内"
                report["summary"]["rolled_back"] += 1
                report["applied_fixes"].append(fix_record)
                continue
            final_sentences[sentence_idx] = candidate
            fix_record["status"] = "applied"
            fix_record["reason"] = issue.get("reason") or "复核通过并满足安全时长约束"
            report["summary"]["applied"] += 1
            applied_count += 1
            report["applied_fixes"].append(fix_record)
        except Exception as exc:  # noqa: BLE001 - 单句失败只回滚该句
            log.warning("[omni_av_sync_audit] fix failed task=%s asr=%s", task_id, asr_index, exc_info=True)
            fix_record["status"] = "rolled_back"
            fix_record["reason"] = str(exc)[:300]
            report["summary"]["rolled_back"] += 1
            report["applied_fixes"].append(fix_record)

    if report["summary"]["applied"] <= 0:
        return sentences

    final_localized_translation = _build_av_localized_translation(final_sentences)
    final_tts_segments = _build_av_tts_segments(final_sentences)
    full_audio_path = _rebuild_tts_full_audio_from_segments(task_dir, final_tts_segments, variant="av")
    final_tts_output = {
        "full_audio_path": full_audio_path,
        "segments": final_tts_segments,
    }
    task = task_state.get(task_id) or {}
    variants, variant_state = _ensure_variant_state(task, "av")
    variant_state.update({
        "sentences": final_sentences,
        "localized_translation": final_localized_translation,
        "tts_result": final_tts_output,
        "tts_audio_path": full_audio_path,
        "av_sync_audit": report,
    })
    variants["av"] = variant_state
    task_state.update(
        task_id,
        variants=variants,
        segments=final_tts_segments,
        localized_translation=final_localized_translation,
        tts_audio_path=full_audio_path,
    )
    task_state.set_preview_file(task_id, "tts_full_audio", full_audio_path)
    task_state.set_artifact(task_id, "tts", build_tts_artifact(final_tts_segments))
    return final_sentences


def run(runner, task_id: str, video_path: str, task_dir: str) -> dict:
    """Run Omni AV sync audit; never fail the Omni pipeline."""
    try:
        task = task_state.get(task_id) or {}
        cfg = validate_plugin_config(task.get("plugin_config") or {})
        mode = cfg.get("av_sync_audit") or "off"
        runner._set_step(task_id, "av_sync_audit", "running", "正在审计音画同步风险...")

        variants = task.get("variants") or {}
        variant_state = variants.get("av") or {}
        sentences = _normalize_av_sentences(variant_state.get("sentences") or [])
        if not sentences:
            report = _base_report(mode)
            report["status"] = "skipped_missing_av_sentences"
            _store_report(task_id, report, sentences=sentences)
            runner._set_step(task_id, "av_sync_audit", "done", "缺少句级结果，已跳过音画同步审计")
            return report

        report = _base_report(mode)
        _reset_step_debug_refs(task_id)
        try:
            diagnosis = _call_diagnose(runner, task_id, video_path, task_dir, task, cfg, sentences)
            report["diagnosis"] = diagnosis
            report["summary"]["diagnosed"] = len(diagnosis.get("issues") or [])
        except Exception as exc:  # noqa: BLE001 - 审计失败不阻塞合成
            report["status"] = "diagnose_failed"
            report["diagnosis"] = {"issues": [], "summary": "", "error": str(exc)[:500]}
            _store_report(task_id, report, sentences=sentences)
            runner._set_step(task_id, "av_sync_audit", "done", "音画同步评估失败，已跳过自动修正")
            return report

        if mode == "report_only":
            report["verification"] = {
                "accepted_issues": [],
                "rejected_count": 0,
                "summary": "音画同步审计仅做 Gemini 结构化评分，不执行复核或自动修改。",
            }
            _store_report(task_id, report, sentences=sentences)
            runner._set_step(task_id, "av_sync_audit", "done", "音画同步审计完成（仅报告）")
            return report

        try:
            verification = _call_verify(runner, task_id, task_dir, task, cfg, sentences, report["diagnosis"])
            report["verification"] = verification
            report["summary"]["accepted"] = len(_candidate_issues(verification))
        except Exception as exc:  # noqa: BLE001 - 复核失败只保留诊断报告
            report["status"] = "verify_failed"
            report["verification"] = {
                "accepted_issues": [],
                "rejected_count": 0,
                "summary": "",
                "error": str(exc)[:500],
            }
            _store_report(task_id, report, sentences=sentences)
            runner._set_step(task_id, "av_sync_audit", "done", "Gemini 复核失败，已跳过自动修正")
            return report

        if mode == "safe_auto":
            _apply_safe_auto(runner, task_id, task_dir, report, cfg, sentences)

        _store_report(task_id, report, sentences=sentences)
        message = "音画同步审计完成"
        if mode == "report_only":
            message += "（仅报告）"
        elif report["summary"]["applied"]:
            message += f"，已安全修正 {report['summary']['applied']} 句"
        runner._set_step(task_id, "av_sync_audit", "done", message)
        return report
    except Exception as exc:  # noqa: BLE001 - 最外层兜底，不能阻塞 Omni
        log.warning("[omni_av_sync_audit] unexpected failure task=%s", task_id, exc_info=True)
        report = _base_report("unknown")
        report["status"] = "failed"
        report["error"] = str(exc)[:500]
        _store_report(task_id, report)
        try:
            runner._set_step(task_id, "av_sync_audit", "done", "音画同步审计异常，已跳过")
        except Exception:
            pass
        return report


def run_report_only(
    runner,
    task_id: str,
    video_path: str,
    task_dir: str,
    *,
    variant: str = "normal",
) -> dict:
    """Run the AV sync audit as a multi-translate evaluation only.

    This path deliberately never calls the safe-auto fixer and never mutates
    TTS/subtitle outputs. It only stores a report under ``av_sync_audit``.
    """
    try:
        task = task_state.get(task_id) or {}
        cfg = _multi_report_config(task)
        cfg["report_variant"] = variant
        mode = "report_only"
        runner._set_step(task_id, "av_sync_audit", "running", "正在评估音画同步风险...")

        sentences = _normal_report_sentences(task, variant=variant)
        if not sentences:
            report = _base_report(mode)
            report["status"] = "skipped_missing_report_sentences"
            _store_report(task_id, report, variant=variant, sentences=sentences)
            runner._set_step(task_id, "av_sync_audit", "done", "缺少 TTS 句级结果，已跳过音画同步评估")
            return report

        report = _base_report(mode)
        report["source_variant"] = variant
        report["analysis_only"] = True
        _reset_step_debug_refs(task_id)
        try:
            diagnosis = _call_diagnose(runner, task_id, video_path, task_dir, task, cfg, sentences)
            report["diagnosis"] = diagnosis
            report["summary"]["diagnosed"] = len(diagnosis.get("issues") or [])
        except Exception as exc:  # noqa: BLE001 - evaluation must not block the pipeline
            report["status"] = "diagnose_failed"
            report["diagnosis"] = {"issues": [], "summary": "", "error": str(exc)[:500]}
            _store_report(task_id, report, variant=variant, sentences=sentences)
            runner._set_step(task_id, "av_sync_audit", "done", "音画同步评估失败，已跳过音画同步评估")
            return report

        report["verification"] = {
            "accepted_issues": [],
            "rejected_count": 0,
            "summary": "多语种音画同步审计仅做辅助分析，不执行 Gemini 复核或自动修改。",
        }

        _store_report(task_id, report, variant=variant, sentences=sentences)
        runner._set_step(task_id, "av_sync_audit", "done", "音画同步评估完成（仅报告）")
        return report
    except Exception as exc:  # noqa: BLE001 - report-only audit must never fail multi-translate
        log.warning("[multi_av_sync_audit] unexpected failure task=%s", task_id, exc_info=True)
        report = _base_report("report_only")
        report["status"] = "failed"
        report["error"] = str(exc)[:500]
        _store_report(task_id, report, variant=variant)
        try:
            runner._set_step(task_id, "av_sync_audit", "done", "音画同步评估异常，已跳过")
        except Exception:
            pass
        return report
