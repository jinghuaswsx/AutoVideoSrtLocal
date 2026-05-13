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
    "subtitle_risk": "字幕节奏风险",
    "tts_quality_risk": "TTS 质量风险",
}


_DIAGNOSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "issues": {"type": "array"},
        "summary": {"type": "string"},
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
        })
    return compact


def _format_seconds(value: Any) -> str:
    return f"{_safe_float(value):.2f}s"


def _sentence_by_asr_index(sentences: list[dict]) -> dict[int, dict]:
    indexed: dict[int, dict] = {}
    for sentence in sentences:
        try:
            indexed[int(sentence.get("asr_index"))] = sentence
        except (TypeError, ValueError):
            continue
    return indexed


def _issue_asr_index(issue: dict) -> int | None:
    try:
        return int(issue.get("asr_index"))
    except (TypeError, ValueError):
        return None


def _issue_action(issue: dict) -> str:
    return str(issue.get("safe_action") or issue.get("action") or "").strip()


def _action_matches(action: str, keyword: str) -> bool:
    action_lc = action.lower()
    return keyword in action_lc


def _severity_label(issue: dict) -> str:
    raw = str(issue.get("severity") or "").lower()
    return _SEVERITY_LABELS.get(raw, raw.upper() if raw else "未分级")


def _sentence_text(sentence: dict, issue: dict) -> str:
    return str(
        issue.get("sentence_text")
        or issue.get("target_text")
        or sentence.get("text")
        or sentence.get("translated")
        or ""
    ).strip()


def _target_duration(sentence: dict, issue: dict) -> float:
    return _safe_float(
        issue.get("target_duration"),
        _safe_float(sentence.get("target_duration")),
    )


def _tts_duration(sentence: dict, issue: dict) -> float:
    return _safe_float(
        issue.get("tts_duration"),
        _safe_float(sentence.get("tts_duration")),
    )


def _duration_ratio(sentence: dict, issue: dict) -> float:
    target_duration = _target_duration(sentence, issue)
    tts_duration = _tts_duration(sentence, issue)
    return _safe_float(
        issue.get("duration_ratio"),
        _safe_float(sentence.get("duration_ratio"), _ratio(target_duration, tts_duration)),
    )


def _problem_label(issue: dict, sentence: dict) -> str:
    ratio = _duration_ratio(sentence, issue)
    if ratio > _MAX_SAFE_RATIO:
        return "音频太长，容易拖到下一个画面，导致画面对不上"
    if 0 < ratio < _MIN_SAFE_RATIO:
        return "音频偏短，旁白提前结束，画面后半段容易空出来"
    problem_type = str(issue.get("problem_type") or "").strip()
    return _PROBLEM_TYPE_LABELS.get(problem_type, problem_type or "音画同步风险")


def _timing_text(sentence: dict, issue: dict) -> str:
    target_duration = _target_duration(sentence, issue)
    tts_duration = _tts_duration(sentence, issue)
    ratio = _duration_ratio(sentence, issue)
    if target_duration <= 0 or tts_duration <= 0:
        return "缺少可用的句级时长数据，需要人工复核"
    delta = tts_duration - target_duration
    if delta > 0:
        relation = f"音频超出 {_format_seconds(delta)}"
    elif delta < 0:
        relation = f"音频短缺 {_format_seconds(abs(delta))}"
    else:
        relation = "音频时长与画面窗口一致"
    return (
        f"画面可用时长 {_format_seconds(target_duration)}，"
        f"TTS 实测 {_format_seconds(tts_duration)}，比例 {ratio:.2f}，{relation}"
    )


def _sync_point_text(asr_index: int | None, sentence: dict) -> str:
    label = f"ASR {asr_index}" if asr_index is not None else "未知 ASR"
    start = sentence.get("start_time")
    end = sentence.get("end_time")
    if start is None or end is None:
        return label
    return f"{label} · {_format_seconds(start)} → {_format_seconds(end)}"


def _recommendation_text(issue: dict, sentence: dict) -> str:
    action = _issue_action(issue)
    ratio = _duration_ratio(sentence, issue)
    target_duration = _target_duration(sentence, issue)
    tts_duration = _tts_duration(sentence, issue)

    if target_duration <= 0 or tts_duration <= 0:
        return "建议先人工核对这一句的时间轴和 TTS 文件，再决定是重新生成音频还是调整文案。"

    if _MIN_SAFE_SPEED <= ratio <= _MAX_SAFE_SPEED:
        return (
            f"可优先做音频变速，建议速度约 {ratio:.2f}x，仍在 0.95-1.05 安全范围；"
            "变速后复听，确认不失真且画面能对上。"
        )

    if ratio > _MAX_SAFE_SPEED:
        if _action_matches(action, "regenerate"):
            return (
                "不建议只做音频变速：要塞进画面需要约 "
                f"{ratio:.2f}x，超过 0.95-1.05 安全范围。建议先重新生成音频；"
                "如果仍然过长，再重写文案、压缩这一句后重新生成音频。"
            )
        return (
            "不建议只做音频变速：要塞进画面需要约 "
            f"{ratio:.2f}x，超过 0.95-1.05 安全范围。建议重写文案、缩短这一句，"
            "然后重新生成音频。"
        )

    if ratio < _MIN_SAFE_SPEED:
        return (
            "不建议只做音频变速：要铺满画面需要约 "
            f"{ratio:.2f}x 慢放，低于 0.95-1.05 安全范围。建议扩写或补足这一句文案，"
            "然后重新生成音频。"
        )

    if _action_matches(action, "manual"):
        return "建议人工复核画面动作和旁白内容，再决定是否重写文案并重新生成音频。"
    if _action_matches(action, "shorten"):
        return "建议重写文案、缩短这一句，然后重新生成音频。"
    if _action_matches(action, "expand"):
        return "建议扩写或补足这一句文案，然后重新生成音频。"
    return "建议重新生成这一句音频；如果时长仍不贴合，再调整文案。"


def _merge_issue_details(base_issue: dict, accepted_issue: dict | None) -> dict:
    if not accepted_issue:
        return dict(base_issue)
    merged = dict(base_issue)
    for key, value in accepted_issue.items():
        if value not in (None, ""):
            merged[key] = value
    return merged


def _readable_findings(report: dict, sentences: list[dict]) -> list[dict]:
    sentence_index = _sentence_by_asr_index(sentences)
    diagnosis_issues = [
        issue for issue in (report.get("diagnosis") or {}).get("issues") or []
        if isinstance(issue, dict)
    ]
    accepted_by_asr = {
        asr_index: issue
        for issue in (report.get("verification") or {}).get("accepted_issues") or []
        if isinstance(issue, dict)
        for asr_index in [_issue_asr_index(issue)]
        if asr_index is not None and issue.get("accepted", True)
    }

    findings: list[dict] = []
    seen: set[int] = set()
    for issue in diagnosis_issues:
        asr_index = _issue_asr_index(issue)
        if asr_index is None:
            continue
        seen.add(asr_index)
        accepted_issue = accepted_by_asr.get(asr_index)
        merged = _merge_issue_details(issue, accepted_issue)
        sentence = sentence_index.get(asr_index, {})
        findings.append({
            "asr_index": asr_index,
            "sync_point": _sync_point_text(asr_index, sentence),
            "severity": str(merged.get("severity") or "").lower(),
            "severity_label": _severity_label(merged),
            "verified": bool(accepted_issue),
            "problem": _problem_label(merged, sentence),
            "timing": _timing_text(sentence, merged),
            "sentence_text": _sentence_text(sentence, merged),
            "source_text": str(sentence.get("source_text") or "").strip(),
            "recommendation": _recommendation_text(merged, sentence),
            "evidence": str(merged.get("evidence") or merged.get("reason") or "").strip(),
            "suggested_text": str(
                merged.get("final_text") or merged.get("suggested_text") or ""
            ).strip(),
        })

    for asr_index, accepted_issue in accepted_by_asr.items():
        if asr_index in seen:
            continue
        sentence = sentence_index.get(asr_index, {})
        findings.append({
            "asr_index": asr_index,
            "sync_point": _sync_point_text(asr_index, sentence),
            "severity": str(accepted_issue.get("severity") or "").lower(),
            "severity_label": _severity_label(accepted_issue),
            "verified": True,
            "problem": _problem_label(accepted_issue, sentence),
            "timing": _timing_text(sentence, accepted_issue),
            "sentence_text": _sentence_text(sentence, accepted_issue),
            "source_text": str(sentence.get("source_text") or "").strip(),
            "recommendation": _recommendation_text(accepted_issue, sentence),
            "evidence": str(accepted_issue.get("reason") or accepted_issue.get("evidence") or "").strip(),
            "suggested_text": str(
                accepted_issue.get("final_text") or accepted_issue.get("suggested_text") or ""
            ).strip(),
        })

    severity_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        findings,
        key=lambda item: (
            severity_order.get(item.get("severity") or "", 3),
            int(item.get("asr_index") or 0),
        ),
    )


def _attach_readable_report(report: dict, sentences: list[dict]) -> None:
    findings = _readable_findings(report, sentences)
    report["readable_findings"] = findings
    if not findings:
        report["readable_summary"] = "中文审计结论：未发现需要处理的音画同步点。"
        return

    verified_count = sum(1 for item in findings if item.get("verified"))
    high_items = [item for item in findings if item.get("severity") == "high"]
    lead = high_items[0] if high_items else findings[0]
    report["readable_summary"] = (
        f"中文审计结论：发现 {len(findings)} 个需要关注的同步点，"
        f"其中 {verified_count} 个已由复核确认。优先处理 {lead['sync_point']}："
        f"{lead['problem']}。处理建议：{lead['recommendation']}"
    )


def _build_diagnosis_prompt(task: dict, cfg: dict, sentences: list[dict]) -> str:
    payload = {
        "source_language": task.get("source_language"),
        "target_lang": task.get("target_lang") or task.get("target_language"),
        "plugin_config": cfg,
        "shot_notes": task.get("shot_notes"),
        "sentences": _compact_sentences(sentences),
        "constraints": {
            "do_not_change_video": True,
            "do_not_shift_timeline": True,
            "allowed_actions": ["none", "shorten_text", "expand_text", "regenerate_tts", "manual_review"],
            "safe_speed_range": [0.95, 1.05],
        },
    }
    return (
        "请审计这个 Omni 视频翻译任务的音画同步风险。"
        "只输出 JSON，issues 内每项必须包含 asr_index、severity、problem_type、"
        "evidence、safe_action、confidence；evidence 和 summary 必须使用简体中文，"
        "要明确哪个 ASR 同步点、哪一句 TTS 过长或过短、会怎样导致画面对不上，"
        "并给出是音频变速、重写文案后重新生成音频，还是仅重新生成音频。"
        "不要建议剪辑画面或移动时间轴。\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


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
                "你是视频翻译音画同步复核员。复核 Doubao 提出的问题是否成立，"
                "只保留 medium/high 且可安全处理的问题。输出 JSON。"
                "reason 和 summary 必须使用简体中文，并明确处理方式："
                "音频变速、重写/压缩/扩写文案后重新生成音频，或仅重新生成音频。"
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


def _call_diagnose(
    runner,
    task_id: str,
    video_path: str,
    task_dir: str,
    task: dict,
    cfg: dict,
    sentences: list[dict],
) -> dict:
    use_case_code = "omni_av_sync.diagnose"
    prompt = _build_diagnosis_prompt(task, cfg, sentences)
    system = (
        "你是短视频音画同步审计员。你只能提出结构化候选问题，"
        "不能决定修改视频，也不能建议大幅变速或剪辑。"
    )
    provider, model = _resolve_llm_binding(use_case_code)
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
        media=[video_path] if video_path else None,
        response_schema=_DIAGNOSIS_SCHEMA,
        temperature=0.1,
        max_output_tokens=4096,
    )
    _save_debug_payload(
        task_id,
        task_dir,
        phase="diagnose",
        label="Doubao 音画同步诊断",
        use_case_code=use_case_code,
        provider=provider,
        model=model,
        messages=messages,
        request_payload=request_payload,
        input_snapshot=[
            {
                "key": "sentences",
                "title": "句级时间轴",
                "content": json.dumps(_compact_sentences(sentences), ensure_ascii=False, indent=2),
            },
        ],
    )
    result = llm_client.invoke_generate(
        use_case_code,
        prompt=prompt,
        system=system,
        media=[video_path] if video_path else None,
        user_id=getattr(runner, "user_id", None),
        project_id=task_id,
        response_schema=_DIAGNOSIS_SCHEMA,
        temperature=0.1,
        max_output_tokens=4096,
    )
    return _json_from_result(result, {"issues": [], "summary": ""})


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
        "diagnosis_issues": diagnosis.get("issues") or [],
        "accepted_issues": verification.get("accepted_issues") or [],
        "applied_fixes": report.get("applied_fixes") or [],
    }
    lines = [
        f"模式：{report.get('mode') or '-'}",
        f"状态：{report.get('status') or '-'}",
        f"诊断问题：{summary.get('diagnosed', 0)}",
        f"复核通过：{summary.get('accepted', 0)}",
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
        {"type": "text", "label": "审计摘要", "content": "\n".join(lines)},
        {
            "type": "text",
            "label": "结构化结果",
            "content": json.dumps(preview, ensure_ascii=False, indent=2),
        },
    ]


def _store_report(
    task_id: str,
    report: dict,
    *,
    variant: str = "av",
    sentences: list[dict] | None = None,
) -> None:
    _attach_readable_report(report, sentences or [])
    _ensure_report_preview_items(report)
    task = task_state.get(task_id) or {}
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


def _normal_report_sentences(task: dict, *, variant: str = "normal") -> list[dict]:
    variants = task.get("variants") or {}
    variant_state = variants.get(variant) or {}
    tts_segments = variant_state.get("segments") or task.get("segments") or []
    script_by_index = _script_segment_index(task.get("script_segments") or [])
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
        report_sentences.append({
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
        })
    return report_sentences


def _multi_report_config(task: dict) -> dict:
    return {
        "av_sync_audit": "report_only",
        "project_type": task.get("type") or "multi_translate",
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
            _store_report(task_id, report)
            runner._set_step(task_id, "av_sync_audit", "done", "缺少句级结果，已跳过音画同步审计")
            return report

        report = _base_report(mode)
        try:
            diagnosis = _call_diagnose(runner, task_id, video_path, task_dir, task, cfg, sentences)
            report["diagnosis"] = diagnosis
            report["summary"]["diagnosed"] = len(diagnosis.get("issues") or [])
        except Exception as exc:  # noqa: BLE001 - 审计失败不阻塞合成
            report["status"] = "diagnose_failed"
            report["diagnosis"] = {"issues": [], "summary": "", "error": str(exc)[:500]}
            _store_report(task_id, report, sentences=sentences)
            runner._set_step(task_id, "av_sync_audit", "done", "Doubao 诊断失败，已跳过自动修正")
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
            sentences = _apply_safe_auto(runner, task_id, task_dir, report, cfg, sentences)

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
        mode = "report_only"
        runner._set_step(task_id, "av_sync_audit", "running", "正在评估音画同步风险...")

        sentences = _normal_report_sentences(task, variant=variant)
        if not sentences:
            report = _base_report(mode)
            report["status"] = "skipped_missing_report_sentences"
            _store_report(task_id, report, variant=variant)
            runner._set_step(task_id, "av_sync_audit", "done", "缺少 TTS 句级结果，已跳过音画同步评估")
            return report

        report = _base_report(mode)
        report["source_variant"] = variant
        try:
            diagnosis = _call_diagnose(runner, task_id, video_path, task_dir, task, cfg, sentences)
            report["diagnosis"] = diagnosis
            report["summary"]["diagnosed"] = len(diagnosis.get("issues") or [])
        except Exception as exc:  # noqa: BLE001 - evaluation must not block the pipeline
            report["status"] = "diagnose_failed"
            report["diagnosis"] = {"issues": [], "summary": "", "error": str(exc)[:500]}
            _store_report(task_id, report, variant=variant, sentences=sentences)
            runner._set_step(task_id, "av_sync_audit", "done", "Doubao 诊断失败，已跳过音画同步评估")
            return report

        try:
            verification = _call_verify(runner, task_id, task_dir, task, cfg, sentences, report["diagnosis"])
            report["verification"] = verification
            report["summary"]["accepted"] = len(_candidate_issues(verification))
        except Exception as exc:  # noqa: BLE001 - keep the diagnosis report
            report["status"] = "verify_failed"
            report["verification"] = {
                "accepted_issues": [],
                "rejected_count": 0,
                "summary": "",
                "error": str(exc)[:500],
            }
            _store_report(task_id, report, variant=variant, sentences=sentences)
            runner._set_step(task_id, "av_sync_audit", "done", "Gemini 复核失败，已保留诊断报告")
            return report

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
