"""Omni AV sync audit and bounded sentence-level fixes."""
from __future__ import annotations

import json
import logging
import math
from copy import deepcopy
from typing import Any

from appcore import llm_client, task_state
from appcore.omni_plugin_config import validate_plugin_config
from appcore.preview_artifacts import build_tts_artifact
from appcore.runtime import (
    _build_av_localized_translation,
    _build_av_tts_segments,
    _ensure_variant_state,
    _normalize_av_sentences,
    _rebuild_tts_full_audio_from_segments,
)

log = logging.getLogger(__name__)

_SAFE_AUTO_ACTIONS = {"shorten_text", "expand_text", "regenerate_tts"}
_APPLY_SEVERITIES = {"medium", "high"}
_MIN_SAFE_RATIO = 0.95
_MAX_SAFE_RATIO = 1.05
_MIN_SAFE_SPEED = 0.95
_MAX_SAFE_SPEED = 1.05


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
            "start_time": sentence.get("start_time"),
            "end_time": sentence.get("end_time"),
            "target_duration": sentence.get("target_duration"),
            "text": sentence.get("text"),
            "tts_duration": sentence.get("tts_duration"),
            "duration_ratio": sentence.get("duration_ratio"),
            "speed": sentence.get("speed"),
            "status": sentence.get("status"),
        })
    return compact


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
        "evidence、safe_action、confidence；不要建议剪辑画面或移动时间轴。\n\n"
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
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def _call_diagnose(runner, task_id: str, video_path: str, task: dict, cfg: dict, sentences: list[dict]) -> dict:
    result = llm_client.invoke_generate(
        "omni_av_sync.diagnose",
        prompt=_build_diagnosis_prompt(task, cfg, sentences),
        system=(
            "你是短视频音画同步审计员。你只能提出结构化候选问题，"
            "不能决定修改视频，也不能建议大幅变速或剪辑。"
        ),
        media=[video_path] if video_path else None,
        user_id=getattr(runner, "user_id", None),
        project_id=task_id,
        response_schema=_DIAGNOSIS_SCHEMA,
        temperature=0.1,
        max_output_tokens=4096,
    )
    return _json_from_result(result, {"issues": [], "summary": ""})


def _call_verify(runner, task_id: str, task: dict, cfg: dict, sentences: list[dict], diagnosis: dict) -> dict:
    result = llm_client.invoke_chat(
        "omni_av_sync.verify",
        messages=_build_verify_messages(diagnosis, task, cfg, sentences),
        user_id=getattr(runner, "user_id", None),
        project_id=task_id,
        temperature=0.1,
        max_tokens=4096,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "omni_av_sync_verify", "schema": _VERIFY_SCHEMA},
        },
    )
    return _json_from_result(result, {"accepted_issues": [], "rejected_count": 0, "summary": ""})


def _base_report(mode: str) -> dict:
    return {
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


def _store_report(task_id: str, report: dict) -> None:
    task = task_state.get(task_id) or {}
    variants = dict(task.get("variants") or {})
    variant_state = dict(variants.get("av") or {})
    variant_state["av_sync_audit"] = report
    variants["av"] = variant_state
    task_state.update(task_id, variants=variants)
    task_state.set_artifact(task_id, "av_sync_audit", report)


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
            diagnosis = _call_diagnose(runner, task_id, video_path, task, cfg, sentences)
            report["diagnosis"] = diagnosis
            report["summary"]["diagnosed"] = len(diagnosis.get("issues") or [])
        except Exception as exc:  # noqa: BLE001 - 审计失败不阻塞合成
            report["status"] = "diagnose_failed"
            report["diagnosis"] = {"issues": [], "summary": "", "error": str(exc)[:500]}
            _store_report(task_id, report)
            runner._set_step(task_id, "av_sync_audit", "done", "Doubao 诊断失败，已跳过自动修正")
            return report

        try:
            verification = _call_verify(runner, task_id, task, cfg, sentences, report["diagnosis"])
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
            _store_report(task_id, report)
            runner._set_step(task_id, "av_sync_audit", "done", "Gemini 复核失败，已跳过自动修正")
            return report

        if mode == "safe_auto":
            _apply_safe_auto(runner, task_id, task_dir, report, cfg, sentences)

        _store_report(task_id, report)
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
