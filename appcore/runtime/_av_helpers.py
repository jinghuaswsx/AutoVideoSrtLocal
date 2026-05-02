"""AV (audio-visual) sync sub-pipeline helpers.

由 ``appcore.runtime`` package 在 PR 3.3 抽出；行为不变。
"""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

import appcore.task_state as task_state
from appcore import tts_generation_stats
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
from appcore.events import (
    EVT_ALIGNMENT_READY,
    EVT_ASR_RESULT,
    EVT_CAPCUT_READY,
    EVT_PIPELINE_DONE,
    EVT_PIPELINE_ERROR,
    EVT_STEP_UPDATE,
    EVT_SUBTITLE_READY,
    EVT_TRANSLATE_RESULT,
    EVT_TTS_SCRIPT_READY,
    Event,
    EventBus,
)

from ._helpers import _is_av_pipeline_task, _av_target_lang

log = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - import only for typing
    from ._pipeline_runner import PipelineRunner

# Lazy-import: PipelineRunner is in __init__.py / _pipeline_runner.py to avoid circular imports
def _PipelineRunner():
    from . import PipelineRunner as _PR
    return _PR


def _default_av_variant_state(label: str = "音画同步版") -> dict:
    return {
        "label": label,
        "localized_translation": {},
        "tts_script": {},
        "tts_result": {},
        "english_asr_result": {},
        "corrected_subtitle": {},
        "timeline_manifest": {},
        "result": {},
        "exports": {},
        "artifacts": {},
        "preview_files": {},
        "sentences": [],
    }


def _ensure_variant_state(task: dict, variant: str, label: str = "音画同步版") -> tuple[dict, dict]:
    variants = dict(task.get("variants") or {})
    base = variants.get(variant)
    variant_state = dict(base) if isinstance(base, dict) else _default_av_variant_state(label)
    variant_state.setdefault("label", label)
    variant_state.setdefault("artifacts", {})
    variant_state.setdefault("preview_files", {})
    variant_state.setdefault("sentences", [])
    variants[variant] = variant_state
    return variants, variant_state


def _join_source_full_text(script_segments: list[dict]) -> str:
    return "\n".join(
        str(segment.get("text") or "").strip()
        for segment in script_segments
        if str(segment.get("text") or "").strip()
    ).strip()


def _load_json_if_exists(path: str):
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _restore_av_localize_outputs_from_files(
    task_id: str,
    *,
    runner: PipelineRunner,
    task: dict,
    task_dir: str,
    variant: str,
    target_language: str,
    target_language_name: str,
    source_full_text: str,
) -> bool:
    full_audio_path = os.path.join(task_dir, f"tts_full.{variant}.mp3")
    srt_path = os.path.join(task_dir, f"subtitle.{variant}.srt")
    localized_path = os.path.join(task_dir, f"localized_translation.{variant}.json")
    tts_result_path = os.path.join(task_dir, f"tts_result.{variant}.json")
    subtitle_path = os.path.join(task_dir, f"corrected_subtitle.{variant}.json")

    if not all(os.path.isfile(path) for path in (full_audio_path, srt_path, localized_path, tts_result_path, subtitle_path)):
        return False

    try:
        localized_translation = _load_json_if_exists(localized_path)
        tts_segments = _load_json_if_exists(tts_result_path)
        subtitle_payload = _load_json_if_exists(subtitle_path)
        shot_notes = task.get("shot_notes") or _load_json_if_exists(os.path.join(task_dir, "shot_notes.json")) or {}
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        log.warning("failed to restore AV outputs from disk for task %s: %s", task_id, exc)
        return False

    if not isinstance(localized_translation, dict) or not isinstance(tts_segments, list) or not isinstance(subtitle_payload, dict):
        return False

    srt_content = str(subtitle_payload.get("srt_content") or "")
    if not srt_content:
        try:
            with open(srt_path, "r", encoding="utf-8") as fh:
                srt_content = fh.read()
        except OSError:
            srt_content = ""
    subtitle_units = list(subtitle_payload.get("chunks") or [])
    tts_result = {"full_audio_path": full_audio_path, "segments": tts_segments}

    task = task_state.get(task_id) or task
    variants, variant_state = _ensure_variant_state(task, variant)
    variant_state.update(
        {
            "sentences": tts_segments,
            "localized_translation": localized_translation,
            "tts_result": tts_result,
            "tts_audio_path": full_audio_path,
            "subtitle_units": subtitle_units,
            "srt_path": srt_path,
            "corrected_subtitle": {"chunks": subtitle_units, "srt_content": srt_content},
            "shot_notes": shot_notes,
        }
    )
    variant_state.setdefault("preview_files", {})["tts_full_audio"] = full_audio_path
    variant_state.setdefault("preview_files", {})["srt"] = srt_path
    variant_state.setdefault("artifacts", {})["tts"] = build_tts_artifact(tts_segments)
    variants[variant] = variant_state

    task_state.update(
        task_id,
        variants=variants,
        shot_notes=shot_notes,
        localized_translation=localized_translation,
        source_full_text_zh=source_full_text,
        segments=tts_segments,
        tts_audio_path=full_audio_path,
        srt_path=srt_path,
        corrected_subtitle={"chunks": subtitle_units, "srt_content": srt_content},
        tts_duration_status="done",
    )
    task_state.set_preview_file(task_id, "tts_full_audio", full_audio_path)
    task_state.set_preview_file(task_id, "srt", srt_path)
    task_state.set_artifact(
        task_id,
        "translate",
        build_translate_artifact(
            source_full_text,
            localized_translation,
            target_language=target_language,
        ),
    )
    task_state.set_artifact(task_id, "tts", build_tts_artifact(tts_segments))
    task_state.set_artifact(
        task_id,
        "subtitle",
        build_subtitle_artifact(srt_content, target_language=target_language),
    )
    runner._set_step(task_id, "translate", "done", f"{target_language_name}音画同步翻译已从缓存恢复")
    runner._set_step(task_id, "tts", "done", f"{target_language_name}配音已从缓存恢复")
    runner._set_step(task_id, "subtitle", "done", f"{target_language_name}字幕已从缓存恢复")
    return True


def _normalize_av_sentences(sentences: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for fallback_index, sentence in enumerate(sentences or []):
        if not isinstance(sentence, dict):
            continue
        start_time = float(sentence.get("start_time", 0.0) or 0.0)
        end_time = float(sentence.get("end_time", start_time) or start_time)
        target_duration = float(sentence.get("target_duration", max(0.0, end_time - start_time)) or 0.0)
        text = str(sentence.get("text") or "")
        normalized.append(
            {
                **sentence,
                "asr_index": int(sentence.get("asr_index", sentence.get("index", fallback_index))),
                "start_time": start_time,
                "end_time": end_time,
                "target_duration": target_duration,
                "target_chars_range": list(sentence.get("target_chars_range") or []),
                "text": text,
                "est_chars": int(sentence.get("est_chars", len(text)) or 0),
            }
        )
    return normalized


def _build_av_localized_translation(sentences: list[dict]) -> dict:
    localized_sentences = []
    text_parts = []
    for fallback_index, sentence in enumerate(sentences or []):
        text = str(sentence.get("text") or "")
        if text:
            text_parts.append(text)
        localized_sentences.append(
            {
                "index": fallback_index,
                "asr_index": int(sentence.get("asr_index", fallback_index)),
                "text": text,
                "source_segment_indices": [int(sentence.get("asr_index", fallback_index))],
            }
        )
    return {
        "full_text": " ".join(text_parts).strip(),
        "sentences": localized_sentences,
    }


def _build_av_tts_segments(sentences: list[dict]) -> list[dict]:
    segments: list[dict] = []
    for fallback_index, sentence in enumerate(sentences or []):
        text = str(sentence.get("text") or "")
        segments.append(
            {
                "index": fallback_index,
                "asr_index": int(sentence.get("asr_index", fallback_index)),
                "text": text,
                "translated": text,
                "tts_text": text,
                "start_time": float(sentence.get("start_time", 0.0) or 0.0),
                "end_time": float(sentence.get("end_time", sentence.get("start_time", 0.0)) or 0.0),
                "target_duration": float(sentence.get("target_duration", 0.0) or 0.0),
                "target_chars_range": list(sentence.get("target_chars_range") or []),
                "tts_duration": float(sentence.get("tts_duration", 0.0) or 0.0),
                "tts_path": sentence.get("tts_path"),
                "speed": float(sentence.get("speed", 1.0) or 1.0),
                "rewrite_rounds": int(sentence.get("rewrite_rounds", 0) or 0),
                "status": sentence.get("status"),
            }
        )
    return segments


def _rebuild_tts_full_audio_from_segments(task_dir: str, segments: list[dict], variant: str = "av") -> str:
    seg_dir = os.path.join(task_dir, "tts_segments", variant) if variant else os.path.join(task_dir, "tts_segments")
    os.makedirs(seg_dir, exist_ok=True)
    concat_list_path = os.path.join(seg_dir, "concat.rewrite.txt")
    with open(concat_list_path, "w", encoding="utf-8") as concat_file:
        for segment in segments or []:
            segment_path = os.path.abspath(str(segment.get("tts_path") or ""))
            if not segment_path or not os.path.exists(segment_path):
                raise FileNotFoundError(f"找不到配音片段: {segment_path}")
            escaped_segment_path = segment_path.replace("\\", "/").replace("'", "'\\''")
            concat_file.write(f"file '{escaped_segment_path}'\n")

    full_audio_name = f"tts_full.{variant}.mp3" if variant else "tts_full.mp3"
    full_audio_path = os.path.join(task_dir, full_audio_name)

    import subprocess

    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path, "-c", "copy", full_audio_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"音频拼接失败: {result.stderr}")
    return full_audio_path


def _build_av_debug_state(
    sentences: list[dict],
    model: str = "openai/gpt-5.5",
    source_normalization: dict | None = None,
) -> dict:
    ok_statuses = {"ok", "rewritten_ok", "speed_adjusted"}
    total = len(sentences or [])
    ok_sentences = sum(
        1
        for sentence in (sentences or [])
        if isinstance(sentence, dict) and sentence.get("status") in ok_statuses
    )
    text_rewrite_attempts = sum(
        int(sentence.get("text_rewrite_attempts", 0) or 0)
        for sentence in (sentences or [])
        if isinstance(sentence, dict)
    )
    tts_regenerate_attempts = sum(
        int(sentence.get("tts_regenerate_attempts", 0) or 0)
        for sentence in (sentences or [])
        if isinstance(sentence, dict)
    )
    speed_adjustment_attempts = sum(
        int(sentence.get("speed_adjustment_attempts", 0) or 0)
        for sentence in (sentences or [])
        if isinstance(sentence, dict)
    )
    best_effort_sentences = sum(
        1
        for sentence in (sentences or [])
        if isinstance(sentence, dict) and sentence.get("best_effort")
    )
    source_summary = (source_normalization or {}).get("summary") or {}
    return {
        "model": model,
        "source_normalization": source_normalization or {},
        "summary": {
            "total_sentences": total,
            "ok_sentences": ok_sentences,
            "warning_sentences": total - ok_sentences,
            "text_rewrite_attempts": text_rewrite_attempts,
            "tts_regenerate_attempts": tts_regenerate_attempts,
            "speed_adjustment_attempts": speed_adjustment_attempts,
            "best_effort_sentences": best_effort_sentences,
            "source_changed_sentences": int(source_summary.get("changed_sentences") or 0),
        },
        "sentence_convergence": {
            "model": model,
            "sentences": sentences or [],
        },
        "steps": [
            {"code": "source_normalize", "label": "原文纯净化", "status": "done"},
            {"code": "sentence_localize", "label": "GPT-5.5 句级本土化", "status": "done"},
            {"code": "tts_first_pass", "label": "ElevenLabs 首轮生成", "status": "done"},
            {"code": "duration_converge", "label": "句级时长收敛", "status": "done"},
            {"code": "rebuild_outputs", "label": "重建音频和字幕", "status": "done"},
        ],
    }


def _fail_localize(task_id: str, runner: "PipelineRunner", step: str, message: str) -> None:
    task_state.update(task_id, status="failed", error=message)
    runner._set_step(task_id, step, "error", message)
    runner._emit(task_id, EVT_PIPELINE_ERROR, {"error": message})


def _new_silent_runner(user_id: int | None = None) -> "PipelineRunner":
    bus = EventBus()
    return PipelineRunner(bus=bus, user_id=user_id)
