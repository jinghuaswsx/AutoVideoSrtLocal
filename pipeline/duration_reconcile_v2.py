from __future__ import annotations

import os
import re
import subprocess
import shutil
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from queue import Empty, Queue
from typing import Any, Callable

from appcore import omni_ffmpeg_tempo_config
from pipeline import av_translate, tts, speech_rate_model
from pipeline.av_translate import FALLBACK_CPS

MIN_DURATION_RATIO = 0.95
MAX_DURATION_RATIO = 1.05
MIN_FFMPEG_TEMPO_RATIO = 0.9
MAX_FFMPEG_TEMPO_RATIO = 1.1
MIN_TTS_SPEED = 0.95
MAX_TTS_SPEED = 1.05
MAX_TEXT_REWRITE_ATTEMPTS = 10
MAX_TTS_REGENERATE_ATTEMPTS = 10
DEFAULT_SENTENCE_RECONCILE_WORKERS = 5


def duration_ratio(target_duration: float, tts_duration: float) -> float:
    if target_duration <= 0:
        return 1.0
    return tts_duration / target_duration


def classify_overshoot(target_duration: float, tts_duration: float) -> tuple[str, float]:
    ratio = duration_ratio(target_duration, tts_duration)
    if MIN_DURATION_RATIO <= ratio <= MAX_DURATION_RATIO:
        return ("ok", 1.0)
    if ratio > MAX_DURATION_RATIO:
        return ("needs_rewrite", 1.0)
    return ("needs_expand", 1.0)


# 优化二：标点停顿感知的 CPS 预测模型
def compute_target_chars_range_v2(
    target_duration: float,
    voice_id: str,
    target_language: str,
    source_text: str = "",
) -> tuple[int, int]:
    cps = speech_rate_model.get_effective_rate(
        voice_id,
        target_language,
        fallback=FALLBACK_CPS.get(target_language, 14.0),
    )
    
    # 统计停顿符号
    short_pauses = len(re.findall(r"[,，、;；:：]", source_text or ""))
    long_pauses = len(re.findall(r"[\.。\?？!！]", source_text or ""))
    
    pause_deduction = (short_pauses * 0.15) + (long_pauses * 0.3)
    
    # 保留最小时限保护，避免过度扣减
    min_allowed_duration = max(target_duration * 0.3, 0.5)
    effective_duration = max(min_allowed_duration, target_duration - pause_deduction)
    
    lo = max(1, int(cps * effective_duration * 0.92))
    hi = max(lo + 1, int(cps * effective_duration * 1.08 + 0.5))
    return (lo, hi)


# 优化三：本地声学时长预测模型
def predict_tts_duration(
    text: str,
    voice_id: str,
    target_language: str,
    speed: float = 1.0,
) -> float:
    cps = speech_rate_model.get_effective_rate(
        voice_id,
        target_language,
        fallback=FALLBACK_CPS.get(target_language, 14.0),
    )
    
    short_pauses = len(re.findall(r"[,，、;；:：]", text or ""))
    long_pauses = len(re.findall(r"[\.。\?？!！]", text or ""))
    
    pause_time = (short_pauses * 0.15) + (long_pauses * 0.3)
    
    char_count = len(text or "")
    base_duration = char_count / cps if cps > 0 else char_count / 14.0
    
    if speed is not None and speed > 0:
        base_duration = base_duration / speed
        
    return base_duration + pause_time


def _scaled_target_chars_range_v2(
    old_range: Any,
    target_duration: float,
    tts_duration: float,
    voice_id: str,
    target_language: str,
    text: str,
) -> tuple[int, int]:
    if not old_range or len(old_range) != 2 or tts_duration <= 0:
        return compute_target_chars_range_v2(target_duration, voice_id, target_language, text)
    scale = target_duration / tts_duration
    lo = max(1, int(old_range[0] * scale))
    hi = max(lo + 1, int(old_range[1] * scale + 0.5))
    return (lo, hi)


def _tts_segment_map(tts_output: dict) -> dict[int, dict]:
    mapped = {}
    for position, segment in enumerate((tts_output or {}).get("segments") or []):
        asr_index = int(segment.get("asr_index", segment.get("index", position)))
        mapped[asr_index] = segment
    return mapped


def _duration_reason(status: str) -> str:
    if status == "ok":
        return "within_duration_ratio"
    if status == "needs_semantic_repair":
        return "semantic_coverage_missing"
    if status == "needs_rewrite":
        return "above_duration_ratio"
    if status == "needs_expand":
        return "below_duration_ratio"
    return status


def _duration_distance(target_duration: float, tts_duration: float) -> float:
    return abs(duration_ratio(target_duration, tts_duration) - 1.0)


def _delta_pct(target_duration: float, tts_duration: float) -> float:
    if target_duration <= 0:
        return 0.0
    return round(((tts_duration - target_duration) / target_duration) * 100, 2)


def _candidate_from_current(current: dict, *, round_number: int) -> dict:
    return {
        "round": round_number,
        "text": current["text"],
        "tts_path": current.get("tts_path"),
        "tts_base_path": current.get("tts_base_path"),
        "tts_duration": float(current.get("tts_duration", 0.0) or 0.0),
        "duration_ratio": duration_ratio(
            float(current.get("target_duration", 0.0) or 0.0),
            float(current.get("tts_duration", 0.0) or 0.0),
        ),
        "target_duration": float(current.get("target_duration", 0.0) or 0.0),
        "target_chars_range": tuple(current.get("target_chars_range") or (1, 2)),
        "status": current.get("status", "ok"),
        "speed": current.get("speed", 1.0),
        "must_keep_terms": list(current.get("must_keep_terms") or []),
        "covered_source_terms": list(current.get("covered_source_terms") or []),
        "omitted_source_terms": list(current.get("omitted_source_terms") or []),
        "coverage_ok": current.get("coverage_ok", True),
        "semantic_repair_attempts": int(current.get("semantic_repair_attempts", 0) or 0),
    }


def _apply_candidate(current: dict, candidate: dict) -> None:
    current["text"] = candidate["text"]
    current["est_chars"] = len(candidate["text"])
    current["tts_path"] = candidate.get("tts_path")
    current["tts_base_path"] = candidate.get("tts_base_path") or current.get("tts_base_path")
    current["tts_duration"] = float(candidate.get("tts_duration", 0.0) or 0.0)
    current["target_chars_range"] = tuple(candidate.get("target_chars_range") or current["target_chars_range"])
    current["duration_ratio"] = duration_ratio(current["target_duration"], current["tts_duration"])
    current["speed"] = candidate.get("speed", 1.0)
    current["selected_attempt_round"] = int(candidate.get("round", 0) or 0)
    for key in ("must_keep_terms", "covered_source_terms", "omitted_source_terms"):
        if key in candidate:
            current[key] = list(candidate.get(key) or [])
    if "coverage_ok" in candidate:
        current["coverage_ok"] = bool(candidate.get("coverage_ok"))
    if "semantic_repair_attempts" in candidate:
        current["semantic_repair_attempts"] = int(candidate.get("semantic_repair_attempts") or 0)


def _error_text(exc: Exception) -> str:
    return str(exc)[:500]


def _emit_sentence_progress(
    callback: Callable[[dict], None] | None,
    *,
    position: int,
    current: dict,
    phase: str,
) -> None:
    if callback is None:
        return
    # 复用原来的 progress payload
    payload = {
        "mode": "sentence_reconcile_v2",
        "round": position + 1,
        "sentence_position": position,
        "asr_index": current.get("asr_index"),
        "phase": phase,
        "target_duration": current.get("target_duration"),
        "target_chars": list(current.get("target_chars_range") or []),
        "audio_duration": current.get("tts_duration"),
        "duration_ratio": round(float(current.get("duration_ratio", 0.0) or 0.0), 4),
        "delta_pct": _delta_pct(
            float(current.get("target_duration", 0.0) or 0.0),
            float(current.get("tts_duration", 0.0) or 0.0),
        ),
        "text": current.get("text", ""),
        "source_text": current.get("source_text") or current.get("original_source_text") or "",
        "status": current.get("status", ""),
        "speed": current.get("speed", 1.0),
        "attempts": list(current.get("attempts") or []),
    }
    callback(payload)


def _ffmpeg_tempo_output_path(current: dict, *, round_number: int, attempt_number: int) -> str:
    output_path = current.get("tts_path") or f"av_seg_{current['asr_index']}.mp3"
    base, ext = os.path.splitext(output_path)
    return f"{base}.ffmpeg_tempo_r{round_number}_a{attempt_number}{ext or '.mp3'}"


def _apply_ffmpeg_tempo_alignment(
    *,
    audio_path: str,
    audio_duration: float,
    target_duration: float,
    output_path: str,
) -> dict | None:
    if not audio_path or audio_duration <= 0 or target_duration <= 0:
        return None
    ratio = audio_duration / target_duration
    if not (MIN_FFMPEG_TEMPO_RATIO <= ratio <= MAX_FFMPEG_TEMPO_RATIO):
        return None
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-filter:a", f"atempo={ratio:.4f}",
        "-vn", "-acodec", "libmp3lame", "-q:a", "3",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except Exception as exc:
        return {"failed_reason": _error_text(exc)}
    if result.returncode != 0:
        return {"failed_reason": (result.stderr or "ffmpeg tempo alignment failed")[:500]}
    post_duration = tts.get_audio_duration(output_path)
    if post_duration <= 0:
        return {"failed_reason": "ffprobe returned empty duration"}
    return {
        "ratio": round(ratio, 4),
        "pre_duration": round(audio_duration, 3),
        "post_duration": round(post_duration, 3),
        "new_audio_path": output_path,
    }


def _try_ffmpeg_tempo_alignment(
    *,
    current: dict,
    position: int,
    on_progress: Callable[[dict], None] | None,
    reason: str,
) -> bool:
    target_duration = float(current.get("target_duration", 0.0) or 0.0)
    audio_duration = float(current.get("tts_duration", 0.0) or 0.0)
    ratio = duration_ratio(target_duration, audio_duration)
    if not (MIN_FFMPEG_TEMPO_RATIO <= ratio <= MAX_FFMPEG_TEMPO_RATIO):
        return False
    if abs(audio_duration - target_duration) < 0.001:
        return False

    current["speed_adjustment_attempts"] += 1
    round_number = int(current.get("selected_attempt_round", 0) or 0)
    output_path = _ffmpeg_tempo_output_path(
        current,
        round_number=round_number,
        attempt_number=current["speed_adjustment_attempts"],
    )
    result = _apply_ffmpeg_tempo_alignment(
        audio_path=str(current.get("tts_path") or ""),
        audio_duration=audio_duration,
        target_duration=target_duration,
        output_path=output_path,
    )
    current["final_fallback_action"] = "ffmpeg_tempo_align"
    current["final_fallback_reason"] = reason
    if not result or result.get("failed_reason"):
        current["ffmpeg_tempo_applied"] = False
        current["ffmpeg_tempo_failed_reason"] = (
            (result or {}).get("failed_reason") or "ffmpeg tempo alignment skipped"
        )
        _emit_sentence_progress(on_progress, position=position, current=current, phase="ffmpeg_tempo_align")
        return False

    current["tts_path"] = result["new_audio_path"]
    current["tts_duration"] = float(result["post_duration"])
    current["duration_ratio"] = duration_ratio(target_duration, current["tts_duration"])
    current["speed"] = result["ratio"]
    current["status"] = "speed_adjusted"
    current["ffmpeg_tempo_applied"] = True
    current["ffmpeg_tempo_ratio"] = result["ratio"]
    current["ffmpeg_tempo_pre_duration"] = result["pre_duration"]
    current["ffmpeg_tempo_post_duration"] = result["post_duration"]
    current["ffmpeg_tempo_audio_path"] = result["new_audio_path"]
    if not _semantic_coverage_issue(current):
        current["best_effort"] = False
        current.pop("best_effort_reason", None)
    _emit_sentence_progress(on_progress, position=position, current=current, phase="ffmpeg_tempo_align")
    return True


def _preserve_sentence_fields(current: dict, av_sentence: dict) -> None:
    for key, value in av_sentence.items():
        if key in current:
            continue
        if (
            key.startswith("source")
            or key.startswith("original_source")
            or key.startswith("localization")
            or key.startswith("shot")
            or key in {"must_keep_terms", "covered_source_terms", "omitted_source_terms", "coverage_ok"}
        ):
            current[key] = value


def _regenerate_segment(
    *,
    sentence: dict,
    voice_id: str,
    target_language: str,
    speed: float | None = None,
    suffix: str | None = None,
) -> tuple[str, float]:
    output_path = (
        sentence.get("tts_base_path")
        or sentence.get("tts_path")
        or f"av_seg_{sentence['asr_index']}.mp3"
    )
    if suffix:
        base, ext = os.path.splitext(output_path)
        output_path = f"{base}.{suffix}{ext or '.mp3'}"
    tts.generate_segment_audio(
        text=sentence["text"],
        voice_id=voice_id,
        output_path=output_path,
        language_code=target_language,
        speed=speed,
    )
    return output_path, tts.get_audio_duration(output_path)


def _candidate_suffix(kind: str, round_number: int, attempt_number: int | None = None) -> str:
    if attempt_number is None:
        return f"{kind}_r{round_number}"
    return f"{kind}_r{round_number}_a{attempt_number}"


def _semantic_coverage_issue(sentence: dict) -> bool:
    omitted = [str(term).strip() for term in (sentence.get("omitted_source_terms") or []) if str(term).strip()]
    return sentence.get("coverage_ok") is False or bool(omitted)


def _candidate_rank(candidate: dict) -> tuple[int, float]:
    return (
        1 if _semantic_coverage_issue(candidate) else 0,
        _duration_distance(
            float(candidate.get("target_duration", 0.0) or 0.0),
            float(candidate.get("tts_duration", 0.0) or 0.0),
        ),
    )


def _warning_status_for_current(current: dict) -> str:
    if _semantic_coverage_issue(current):
        return "warning_semantic"
    ratio = float(current.get("duration_ratio", 1.0) or 1.0)
    return "warning_long" if ratio > MAX_DURATION_RATIO else "warning_short"


def _mark_selected_attempt(attempts: list[dict], selected_round: int) -> None:
    for attempt in attempts:
        attempt["selected"] = int(attempt.get("round", -1)) == selected_round


def _initial_sentence_state(
    *,
    position: int,
    av_sentence: dict,
    tts_by_index: dict[int, dict],
    max_rewrite_rounds: int,
    max_tts_regenerate_attempts: int,
) -> dict:
    asr_index = int(av_sentence.get("asr_index", position))
    tts_segment = dict(tts_by_index.get(asr_index, {}))
    tts_base_path = tts_segment.get("tts_path") or f"av_seg_{asr_index}.mp3"
    current = {
        "asr_index": asr_index,
        "start_time": av_sentence.get("start_time"),
        "end_time": av_sentence.get("end_time"),
        "target_duration": float(av_sentence.get("target_duration", 0.0) or 0.0),
        "target_chars_range": tuple(av_sentence.get("target_chars_range") or (1, 2)),
        "text": av_sentence.get("text", ""),
        "est_chars": int(av_sentence.get("est_chars", len(av_sentence.get("text", ""))) or 0),
        "tts_path": tts_segment.get("tts_path"),
        "tts_base_path": tts_base_path,
        "tts_duration": float(tts_segment.get("tts_duration", 0.0) or 0.0),
        "speed": 1.0,
        "rewrite_rounds": 0,
        "text_rewrite_attempts": 0,
        "tts_regenerate_attempts": 0,
        "speed_adjustment_attempts": 0,
        "semantic_repair_attempts": 0,
        "max_text_rewrite_attempts": max_rewrite_rounds,
        "max_tts_regenerate_attempts": max_tts_regenerate_attempts,
        "selected_attempt_round": 0,
        "best_effort": False,
        "status": "ok",
        "duration_ratio": duration_ratio(
            float(av_sentence.get("target_duration", 0.0) or 0.0),
            float(tts_segment.get("tts_duration", 0.0) or 0.0),
        ),
        "must_keep_terms": list(av_sentence.get("must_keep_terms") or []),
        "covered_source_terms": list(av_sentence.get("covered_source_terms") or []),
        "omitted_source_terms": list(av_sentence.get("omitted_source_terms") or []),
        "coverage_ok": (
            bool(av_sentence.get("coverage_ok"))
            if av_sentence.get("coverage_ok") is not None
            else not bool(av_sentence.get("omitted_source_terms") or [])
        ),
        "attempts": [],
    }
    _preserve_sentence_fields(current, av_sentence)

    status, speed = classify_overshoot(current["target_duration"], current["tts_duration"])
    if _semantic_coverage_issue(current):
        status = "needs_semantic_repair"
    current["status"] = status
    current["speed"] = speed
    current["duration_ratio"] = duration_ratio(current["target_duration"], current["tts_duration"])
    return current


def _reconcile_one_sentence(
    *,
    position: int,
    current: dict,
    text_rewrite_enabled: bool,
    voice_id: str,
    target_language: str,
    av_inputs: dict,
    shot_notes: dict,
    script_segments: list[dict],
    user_id: int | None,
    project_id: str | None,
    max_rewrite_rounds: int,
    max_tts_regenerate_attempts: int,
    ffmpeg_tempo_enabled: bool,
    on_progress: Callable[[dict], None] | None,
) -> dict:
    _emit_sentence_progress(on_progress, position=position, current=current, phase="initial_measure")
    status = current["status"]
    asr_index = int(current.get("asr_index", position))

    if status in {"needs_rewrite", "needs_expand", "needs_semantic_repair"}:
        if not text_rewrite_enabled:
            current["text_rewrite_disabled"] = True
            current["status"] = _warning_status_for_current(current)
            current["speed"] = 1.0
            current["best_effort"] = True
            _emit_sentence_progress(on_progress, position=position, current=current, phase="rewrite_skipped")
        else:
            # === 本地声学沙盒重写与初筛阶段 ===
            initial_candidate = _candidate_from_current(current, round_number=0)
            initial_candidate["sandbox_predicted"] = False  # 它是有物理音频的真实候选
            
            sandbox_candidates = [initial_candidate]
            seen_texts = {current["text"].strip()}  # 去重用的文本集合
            
            round_limit = min(max_rewrite_rounds, max_tts_regenerate_attempts)
            
            for rewrite_round in range(1, round_limit + 1):
                before_text = current["text"]
                if _semantic_coverage_issue(current):
                    action = "repair_coverage"
                    current["semantic_repair_attempts"] += 1
                elif current["status"] == "needs_rewrite":
                    action = "shorten"
                else:
                    action = "expand"
                
                # 优化二：标点停顿感知的 CPS 字符估计范围计算
                if action == "repair_coverage" and duration_ratio(current["target_duration"], current["tts_duration"]) >= MIN_DURATION_RATIO:
                    new_range = tuple(current["target_chars_range"])
                else:
                    new_range = _scaled_target_chars_range_v2(
                        current["target_chars_range"],
                        current["target_duration"],
                        current["tts_duration"],
                        voice_id,
                        target_language,
                        before_text,
                    )
                
                rewrite_temperature = av_translate.rewrite_temperature_for_attempt(rewrite_round)
                current["text_rewrite_attempts"] += 1
                current["active_attempt"] = rewrite_round
                current["active_action"] = action
                current["active_temperature"] = rewrite_temperature
                current["active_tts_attempt"] = current["tts_regenerate_attempts"] + 1
                
                _emit_sentence_progress(on_progress, position=position, current=current, phase="rewrite_start")
                
                try:
                    rewrite_result = av_translate.rewrite_one(
                        asr_index=asr_index,
                        prev_text=before_text,
                        overshoot_sec=max(0.0, current["tts_duration"] - current["target_duration"]),
                        direction=action,
                        new_target_chars_range=new_range,
                        script_segments=script_segments,
                        shot_notes=shot_notes,
                        av_inputs=av_inputs,
                        voice_id=voice_id,
                        user_id=user_id,
                        project_id=project_id,
                        attempt_number=rewrite_round,
                        previous_attempts=list(current["attempts"]),
                        temperature=rewrite_temperature,
                        required_terms=list(current.get("must_keep_terms") or []),
                        omitted_terms=list(current.get("omitted_source_terms") or []),
                        return_sentence=True,
                    )
                except Exception as exc:
                    current["rewrite_rounds"] = rewrite_round
                    current["attempts"].append(
                        {
                            "round": rewrite_round,
                            "text_attempt": current["text_rewrite_attempts"],
                            "tts_attempt": current["tts_regenerate_attempts"],
                            "temperature": rewrite_temperature,
                            "action": action,
                            "before_text": before_text,
                            "after_text": "",
                            "target_duration": current["target_duration"],
                            "tts_duration": current["tts_duration"],
                            "duration_ratio": round(current["duration_ratio"], 4),
                            "delta_pct": _delta_pct(current["target_duration"], current["tts_duration"]),
                            "status": "rewrite_error",
                            "reason": "rewrite_failed",
                            "error": _error_text(exc),
                            "selected": False,
                            "sandbox_predicted": True,
                        }
                    )
                    _emit_sentence_progress(on_progress, position=position, current=current, phase="rewrite_error")
                    continue
                
                if isinstance(rewrite_result, dict):
                    debug_calls = rewrite_result.pop("_llm_debug_calls", [])
                    if debug_calls:
                        current.setdefault("_llm_debug_calls", []).extend(debug_calls)
                    new_text = str(rewrite_result.get("text") or "")
                    covered_source_terms = list(rewrite_result.get("covered_source_terms") or [])
                    omitted_source_terms = list(rewrite_result.get("omitted_source_terms") or [])
                    if action == "repair_coverage":
                        omitted_source_terms = []
                    coverage_ok = bool(rewrite_result.get("coverage_ok")) if "coverage_ok" in rewrite_result else (action == "repair_coverage")
                else:
                    new_text = str(rewrite_result or "")
                    covered_source_terms = list(current.get("must_keep_terms") or []) if action == "repair_coverage" else []
                    omitted_source_terms = []
                    coverage_ok = True
                
                # === 优化三：本地声学时长预测沙盒测速 ===
                predicted_duration = predict_tts_duration(new_text, voice_id, target_language)
                
                current["text"] = new_text
                current["est_chars"] = len(new_text)
                current["rewrite_rounds"] = rewrite_round
                current["target_chars_range"] = new_range
                
                # 记录沙盒预测的临时状态
                current["tts_duration"] = predicted_duration
                
                temp_candidate_dict = {
                    "coverage_ok": coverage_ok,
                    "omitted_source_terms": omitted_source_terms,
                    "covered_source_terms": covered_source_terms,
                }
                status, speed = classify_overshoot(current["target_duration"], predicted_duration)
                if _semantic_coverage_issue(temp_candidate_dict):
                    status = "needs_semantic_repair"
                current["status"] = status
                current["speed"] = speed
                current["duration_ratio"] = duration_ratio(current["target_duration"], predicted_duration)
                
                attempt = {
                    "round": rewrite_round,
                    "text_attempt": current["text_rewrite_attempts"],
                    "tts_attempt": current["tts_regenerate_attempts"],
                    "temperature": rewrite_temperature,
                    "action": action,
                    "before_text": before_text,
                    "after_text": new_text,
                    "target_duration": current["target_duration"],
                    "tts_duration": predicted_duration,
                    "duration_ratio": round(current["duration_ratio"], 4),
                    "delta_pct": _delta_pct(current["target_duration"], predicted_duration),
                    "status": status,
                    "reason": _duration_reason(status),
                    "coverage_ok": coverage_ok,
                    "omitted_source_terms": omitted_source_terms,
                    "selected": False,
                    "sandbox_predicted": True,
                }
                current["attempts"].append(attempt)
                _emit_sentence_progress(on_progress, position=position, current=current, phase="rewrite_attempt")
                
                new_text_stripped = new_text.strip()
                if new_text_stripped and new_text_stripped not in seen_texts:
                    seen_texts.add(new_text_stripped)
                    candidate = _candidate_from_current(current, round_number=rewrite_round)
                    candidate["coverage_ok"] = coverage_ok
                    candidate["omitted_source_terms"] = omitted_source_terms
                    candidate["covered_source_terms"] = covered_source_terms
                    candidate["sandbox_predicted"] = True
                    sandbox_candidates.append(candidate)
                
                # 沙盒预测只能做粗筛，不能单独决定最终候选。即使命中预测
                # 窗口，也至少多保留一个备选文本，避免真实 TTS 测量偏离时
                # 没有后续候选可选。
                if (
                    status == "ok"
                    and not _semantic_coverage_issue(temp_candidate_dict)
                    and len(sandbox_candidates) >= min(3, round_limit + 1)
                ):
                    break
            
            # === Stage 1 粗筛：排序并筛选出 Top 3 沙盒候选文本 ===
            def _rank_sandbox_candidate(c: dict) -> tuple[int, float]:
                return (
                    1 if _semantic_coverage_issue(c) else 0,
                    abs(duration_ratio(c["target_duration"], c["tts_duration"]) - 1.0)
                )
            
            sorted_candidates = sorted(sandbox_candidates, key=_rank_sandbox_candidate)
            top_candidates = sorted_candidates[:3]
            
            # === Stage 2 精筛：多候选并发真实合成与物理对齐决策 ===
            _emit_sentence_progress(on_progress, position=position, current=current, phase="tts_regen_start")
            
            def _generate_candidate_audio(c: dict) -> dict:
                if not c.get("sandbox_predicted", True):
                    return c
                
                temp_sentence = {
                    "asr_index": asr_index,
                    "text": c["text"],
                    "tts_base_path": current["tts_base_path"],
                    "tts_path": current["tts_path"]
                }
                path, real_duration = _regenerate_segment(
                    sentence=temp_sentence,
                    voice_id=voice_id,
                    target_language=target_language,
                    suffix=_candidate_suffix("rewrite_v2_final", c["round"]),
                )
                c["tts_path"] = path
                c["tts_duration"] = real_duration
                c["duration_ratio"] = duration_ratio(c["target_duration"], real_duration)
                c["sandbox_predicted"] = False
                
                status, speed = classify_overshoot(c["target_duration"], real_duration)
                if _semantic_coverage_issue(c):
                    status = "needs_semantic_repair"
                c["status"] = status
                c["speed"] = speed
                return c

            real_candidates = []
            winner = None
            
            for c in top_candidates:
                # 触发真实的物理合成
                real_c = _generate_candidate_audio(c)
                real_candidates.append(real_c)
                
                # 只有真正触发了物理生成的重写候选才累加 count
                if c["round"] > 0:
                    current["tts_regenerate_attempts"] += 1
                
                # 校验它的物理时长是否已经完美（即真实的 status == "ok"，或者在 FFmpeg 变速完美区内）
                ratio = duration_ratio(real_c["target_duration"], real_c["tts_duration"])
                perfect_sync = (real_c["status"] == "ok")
                tempo_adjustable = (MIN_FFMPEG_TEMPO_RATIO <= ratio <= MAX_FFMPEG_TEMPO_RATIO) and not _semantic_coverage_issue(real_c)
                
                if perfect_sync or tempo_adjustable:
                    winner = real_c
                    break
            
            # 如果跑完一遍（或者因为中途不完美没有 break），我们在所有已物理生成的 real_candidates 中挑选最好的
            if not winner:
                def _rank_real_candidate(c: dict) -> tuple[int, float]:
                    return (
                        1 if _semantic_coverage_issue(c) else 0,
                        abs(duration_ratio(c["target_duration"], c["tts_duration"]) - 1.0)
                    )
                sorted_real_candidates = sorted(real_candidates, key=_rank_real_candidate)
                winner = sorted_real_candidates[0]
            
            # 应用物理表现最佳的最终获胜候选
            _apply_candidate(current, winner)
            current["tts_duration"] = winner["tts_duration"]
            current["duration_ratio"] = winner["duration_ratio"]
            current["status"] = winner["status"]
            current["speed"] = winner["speed"]
            
            # 清理其余未被选中的落选音频文件，避免积压废音频
            for c in real_candidates:
                if c["round"] != winner["round"]:
                    path_to_remove = c.get("tts_path")
                    if path_to_remove and os.path.exists(path_to_remove) and path_to_remove != winner.get("tts_path"):
                        try:
                            os.remove(path_to_remove)
                        except Exception:
                            pass
            
            # 更新对应 attempts 里的真实物理时长和状态，保留 Gantt Panel 数据一致性
            for att in current["attempts"]:
                att_round = int(att.get("round", -1))
                matching_real = next((rc for rc in real_candidates if rc["round"] == att_round), None)
                if matching_real:
                    att["tts_duration"] = matching_real["tts_duration"]
                    att["duration_ratio"] = round(matching_real["duration_ratio"], 4)
                    att["delta_pct"] = _delta_pct(matching_real["target_duration"], matching_real["tts_duration"])
                    att["status"] = matching_real["status"]
                    att["reason"] = _duration_reason(matching_real["status"])
                    att["sandbox_predicted"] = False
                
                att["selected"] = (att_round == winner["round"])
            
            _mark_selected_attempt(current["attempts"], winner["round"])
            
            # 物理真实配音如果微有出入，利用 FFmpeg 变速修正
            if current["status"] in {"needs_rewrite", "needs_expand"} and ffmpeg_tempo_enabled:
                _try_ffmpeg_tempo_alignment(
                    current=current,
                    position=position,
                    on_progress=on_progress,
                    reason="sandbox_perfect_real_offset",
                )
            elif current["status"] in {"needs_rewrite", "needs_expand", "needs_semantic_repair"}:
                current["status"] = _warning_status_for_current(current)
                current["best_effort"] = True
                current["best_effort_reason"] = "sandbox_missed_after_real_tts"

    _emit_sentence_progress(on_progress, position=position, current=current, phase="sentence_done")
    return current


def _sentence_worker_count(max_sentence_workers: int, sentence_count: int) -> int:
    try:
        requested = int(max_sentence_workers)
    except (TypeError, ValueError):
        requested = DEFAULT_SENTENCE_RECONCILE_WORKERS
    return max(1, min(requested, max(sentence_count, 1)))


def _text_rewrite_enabled_for_task(task: dict | None) -> bool:
    task = task or {}
    if task.get("type") == "english_redub":
        return str(task.get("script_mode") or "original").strip().lower() == "rewrite"
    return True


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
    max_rewrite_rounds: int = MAX_TEXT_REWRITE_ATTEMPTS,
    max_tts_regenerate_attempts: int = MAX_TTS_REGENERATE_ATTEMPTS,
    on_progress: Callable[[dict], None] | None = None,
    max_sentence_workers: int = DEFAULT_SENTENCE_RECONCILE_WORKERS,
) -> list[dict]:
    tts_by_index = _tts_segment_map(tts_output)
    av_sentences = list((av_output or {}).get("sentences") or [])
    text_rewrite_enabled = _text_rewrite_enabled_for_task(task)
    ffmpeg_tempo_enabled = omni_ffmpeg_tempo_config.is_enabled()
    
    initial_states = [
        _initial_sentence_state(
            position=position,
            av_sentence=av_sentence,
            tts_by_index=tts_by_index,
            max_rewrite_rounds=max_rewrite_rounds,
            max_tts_regenerate_attempts=max_tts_regenerate_attempts,
        )
        for position, av_sentence in enumerate(av_sentences)
    ]

    for position, current in enumerate(initial_states):
        queued = dict(current)
        queued["status"] = "queued"
        _emit_sentence_progress(on_progress, position=position, current=queued, phase="queued")

    if not initial_states:
        return []

    worker_count = _sentence_worker_count(max_sentence_workers, len(initial_states))
    
    # 纯本地声学沙盒并发执行
    progress_queue: Queue[dict] = Queue()

    def _queue_progress(record: dict) -> None:
        progress_queue.put(record)

    def _drain_progress() -> None:
        while True:
            try:
                record = progress_queue.get_nowait()
            except Empty:
                break
            if on_progress is not None:
                on_progress(record)

    final_by_position: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="sentence-reconcile-v2") as executor:
        futures = {
            executor.submit(
                _reconcile_one_sentence,
                position=position,
                current=current,
                text_rewrite_enabled=text_rewrite_enabled,
                voice_id=voice_id,
                target_language=target_language,
                av_inputs=av_inputs,
                shot_notes=shot_notes,
                script_segments=script_segments,
                user_id=user_id,
                project_id=project_id,
                max_rewrite_rounds=max_rewrite_rounds,
                max_tts_regenerate_attempts=max_tts_regenerate_attempts,
                ffmpeg_tempo_enabled=ffmpeg_tempo_enabled,
                on_progress=_queue_progress,
            ): position
            for position, current in enumerate(initial_states)
        }
        pending = set(futures)
        while pending:
            _drain_progress()
            done, pending = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
            for future in done:
                position = futures[future]
                try:
                    final_by_position[position] = future.result()
                except Exception:
                    for pending_future in pending:
                      pending_future.cancel()
                    _drain_progress()
                    raise
        _drain_progress()

    return [final_by_position[position] for position in range(len(initial_states))]
