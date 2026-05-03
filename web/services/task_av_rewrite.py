"""Helpers for AV sentence rewrite state updates."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass

from appcore.preview_artifacts import (
    build_subtitle_artifact,
    build_translate_artifact,
    build_tts_artifact,
    build_variant_compare_artifact,
)
from appcore.runtime import _build_av_localized_translation, _build_av_tts_segments
from pipeline import tts
from pipeline.av_subtitle_units import build_subtitle_units_from_sentences
from pipeline.duration_reconcile import classify_overshoot, compute_speed_for_target, duration_ratio
from pipeline.subtitle import build_srt_from_chunks, save_srt
from web import store
from web.services.task_access import refresh_task as refresh_task_state


@dataclass(frozen=True)
class AvComposeOutputs:
    result: dict
    exports: dict
    artifacts: dict
    preview_files: dict
    tos_uploads: dict
    variant_result: dict
    variant_exports: dict
    variant_artifacts: dict
    variant_preview_files: dict


@dataclass(frozen=True)
class TaskAvRewriteOutcome:
    payload: dict
    status_code: int = 200


def resolve_av_voice_ids(
    task: dict,
    variant_state: dict,
    *,
    user_id: int,
    get_voice_by_id=None,
) -> tuple[str | None, str | None]:
    stored_voice_id = variant_state.get("voice_id") or task.get("voice_id") or task.get("recommended_voice_id")
    voice = None
    if stored_voice_id:
        try:
            lookup_voice = get_voice_by_id or tts.get_voice_by_id
            voice = lookup_voice(stored_voice_id, user_id)
        except Exception:
            voice = None
    if not isinstance(voice, dict):
        elevenlabs_voice_id = stored_voice_id if isinstance(stored_voice_id, str) else None
        return stored_voice_id, elevenlabs_voice_id
    resolved_voice_id = voice.get("id") or stored_voice_id
    elevenlabs_voice_id = voice.get("elevenlabs_voice_id") or voice.get("voice_id") or voice.get("id")
    return resolved_voice_id, elevenlabs_voice_id


def rebuild_tts_full_audio(
    task_dir: str,
    segments: list[dict],
    variant: str = "av",
    *,
    run_command=None,
) -> str:
    seg_dir = os.path.join(task_dir, "tts_segments", variant) if variant else os.path.join(task_dir, "tts_segments")
    os.makedirs(seg_dir, exist_ok=True)
    concat_list_path = os.path.join(seg_dir, "concat.rewrite.txt")
    with open(concat_list_path, "w", encoding="utf-8") as concat_file:
        for segment in segments:
            segment_path = os.path.abspath(str(segment.get("tts_path") or ""))
            if not segment_path or not os.path.exists(segment_path):
                raise FileNotFoundError(f"找不到配音片段: {segment_path}")
            escaped_segment_path = segment_path.replace("'", "'\\''")
            concat_file.write(f"file '{escaped_segment_path}'\n")

    full_audio_name = f"tts_full.{variant}.mp3" if variant else "tts_full.mp3"
    full_audio_path = os.path.join(task_dir, full_audio_name)
    run = run_command or subprocess.run
    result = run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path, "-c", "copy", full_audio_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"音频拼接失败: {result.stderr}")
    return full_audio_path


def build_translate_compare_artifact(
    task: dict,
    *,
    set_variant_artifact=None,
    build_translate=build_translate_artifact,
    build_variant_compare=build_variant_compare_artifact,
) -> dict:
    variants = dict(task.get("variants", {}))
    compare_variants = {}
    source_full_text_zh = task.get("source_full_text_zh", "")
    persist_variant_artifact = set_variant_artifact or store.set_variant_artifact

    for variant, variant_state in variants.items():
        localized_translation = variant_state.get("localized_translation", {})
        payload = build_translate(source_full_text_zh, localized_translation)
        persist_variant_artifact(task["id"], variant, "translate", payload)
        compare_variants[variant] = {
            "label": variant_state.get("label", variant),
            "items": payload.get("items", []),
        }

    return build_variant_compare("翻译本土化", compare_variants)


def clear_av_compose_outputs(
    task: dict,
    variant_state: dict,
    variant: str = "av",
) -> AvComposeOutputs:
    result = dict(task.get("result") or {})
    exports = dict(task.get("exports") or {})
    artifacts = dict(task.get("artifacts") or {})
    preview_files = dict(task.get("preview_files") or {})
    tos_uploads = dict(task.get("tos_uploads") or {})

    result.pop("hard_video", None)
    exports.pop("capcut_archive", None)
    exports.pop("capcut_project", None)
    exports.pop("jianying_project_dir", None)
    artifacts.pop("compose", None)
    artifacts.pop("export", None)
    preview_files.pop("hard_video", None)

    for key, payload in list(tos_uploads.items()):
        payload_variant = payload.get("variant") if isinstance(payload, dict) else None
        if key.startswith(f"{variant}:") or payload_variant == variant:
            tos_uploads.pop(key, None)

    variant_result = dict(variant_state.get("result") or {})
    variant_exports = dict(variant_state.get("exports") or {})
    variant_artifacts = dict(variant_state.get("artifacts") or {})
    variant_preview_files = dict(variant_state.get("preview_files") or {})

    variant_result.clear()
    variant_exports.clear()
    variant_artifacts.pop("compose", None)
    variant_artifacts.pop("export", None)
    variant_preview_files.pop("hard_video", None)

    return AvComposeOutputs(
        result=result,
        exports=exports,
        artifacts=artifacts,
        preview_files=preview_files,
        tos_uploads=tos_uploads,
        variant_result=variant_result,
        variant_exports=variant_exports,
        variant_artifacts=variant_artifacts,
        variant_preview_files=variant_preview_files,
    )


def rewrite_task_av_sentence(
    task_id: str,
    task: dict,
    body: Mapping[str, object],
    *,
    user_id: int,
    variant: str = "av",
    resolve_voice_ids=None,
    generate_segment_audio=None,
    get_audio_duration=None,
    classify_duration=None,
    compute_speed=None,
    calculate_duration_ratio=None,
    rebuild_audio=None,
    build_subtitle_units=None,
    build_srt=None,
    save_subtitle=None,
    build_localized_translation=None,
    build_tts_segments=None,
    build_tts_artifact_payload=None,
    build_subtitle_artifact_payload=None,
    clear_outputs=None,
    update_task=None,
    refresh_task=None,
) -> TaskAvRewriteOutcome:
    variant_state = dict((task.get("variants") or {}).get(variant) or {})
    sentences = [dict(item) for item in (variant_state.get("sentences") or []) if isinstance(item, dict)]
    if not sentences:
        return TaskAvRewriteOutcome({"error": "当前任务没有可重写的音画同步句子"}, 400)

    try:
        asr_index = int(body.get("asr_index"))
    except (TypeError, ValueError):
        return TaskAvRewriteOutcome({"error": "asr_index 非法"}, 400)
    new_text = str(body.get("text") or "").strip()
    if not new_text:
        return TaskAvRewriteOutcome({"error": "text 不能为空"}, 400)

    sentence_index = None
    for idx, sentence in enumerate(sentences):
        current_asr_index = int(sentence.get("asr_index", sentence.get("index", idx)))
        if current_asr_index == asr_index:
            sentence_index = idx
            break
    if sentence_index is None:
        return TaskAvRewriteOutcome({"error": "未找到对应句子"}, 404)

    task_dir = str(task.get("task_dir") or "").strip()
    if not task_dir:
        return TaskAvRewriteOutcome({"error": "任务目录缺失，无法重写"}, 400)

    resolve_voice_ids = resolve_voice_ids or resolve_av_voice_ids
    resolved_voice_id, elevenlabs_voice_id = resolve_voice_ids(task, variant_state, user_id=user_id)
    if not elevenlabs_voice_id:
        return TaskAvRewriteOutcome({"error": "未找到可用音色，无法重写配音"}, 400)

    av_inputs = task.get("av_translate_inputs") or {}
    target_language = str(av_inputs.get("target_language") or "en").strip().lower() or "en"

    updated_sentence = dict(sentences[sentence_index])
    attempts = updated_sentence.get("attempts")
    updated_sentence["attempts"] = attempts if isinstance(attempts, list) else []
    segment_path = updated_sentence.get("tts_path") or os.path.join(
        task_dir,
        "tts_segments",
        variant,
        f"seg_{sentence_index:04d}.mp3",
    )
    updated_sentence["text"] = new_text
    updated_sentence["est_chars"] = len(new_text)
    updated_sentence["tts_path"] = segment_path

    generate_audio = generate_segment_audio or tts.generate_segment_audio
    read_audio_duration = get_audio_duration or tts.get_audio_duration
    classify = classify_duration or classify_overshoot
    compute_target_speed = compute_speed or compute_speed_for_target
    ratio = calculate_duration_ratio or duration_ratio

    generate_audio(
        text=new_text,
        voice_id=elevenlabs_voice_id,
        output_path=segment_path,
        language_code=target_language,
    )
    tts_duration = float(read_audio_duration(segment_path) or 0.0)
    target_duration = float(updated_sentence.get("target_duration", 0.0) or 0.0)
    status, _speed = classify(target_duration, tts_duration)
    updated_sentence["tts_duration"] = tts_duration
    updated_sentence["status"] = status
    updated_sentence["speed"] = 1.0
    updated_sentence["duration_ratio"] = ratio(target_duration, tts_duration)

    if status == "ok":
        speed = compute_target_speed(target_duration, tts_duration)
        if speed is not None and speed != 1.0:
            generate_audio(
                text=new_text,
                voice_id=elevenlabs_voice_id,
                output_path=segment_path,
                language_code=target_language,
                speed=speed,
            )
            updated_sentence["tts_duration"] = float(read_audio_duration(segment_path) or 0.0)
            updated_sentence["duration_ratio"] = ratio(target_duration, updated_sentence["tts_duration"])
            updated_sentence["status"] = "speed_adjusted"
            updated_sentence["speed"] = speed
    elif status == "needs_rewrite":
        updated_sentence["status"] = "warning_long"
        updated_sentence["speed"] = 1.0
    elif status == "needs_expand":
        updated_sentence["status"] = "warning_short"
        updated_sentence["speed"] = 1.0

    sentences[sentence_index] = updated_sentence
    build_localized_translation = build_localized_translation or _build_av_localized_translation
    build_tts_segments = build_tts_segments or _build_av_tts_segments
    localized_translation = build_localized_translation(sentences)
    tts_segments = build_tts_segments(sentences)

    rebuild_audio = rebuild_audio or rebuild_tts_full_audio
    full_audio_path = rebuild_audio(task_dir, tts_segments, variant)

    sync_granularity = str((av_inputs or {}).get("sync_granularity") or "hybrid")
    build_subtitle_units = build_subtitle_units or build_subtitle_units_from_sentences
    subtitle_units = build_subtitle_units(sentences, mode=sync_granularity)
    build_srt = build_srt or build_srt_from_chunks
    srt_content = build_srt(subtitle_units)
    save_subtitle = save_subtitle or save_srt
    srt_path = save_subtitle(srt_content, os.path.join(task_dir, f"subtitle.{variant}.srt"))

    clear_outputs = clear_outputs or clear_av_compose_outputs
    cleared_outputs = clear_outputs(task, variant_state, variant=variant)
    result = cleared_outputs.result
    exports = cleared_outputs.exports
    artifacts = cleared_outputs.artifacts
    preview_files = cleared_outputs.preview_files
    tos_uploads = cleared_outputs.tos_uploads
    variant_result = cleared_outputs.variant_result
    variant_exports = cleared_outputs.variant_exports
    variant_artifacts = cleared_outputs.variant_artifacts
    variant_preview_files = cleared_outputs.variant_preview_files

    build_tts_payload = build_tts_artifact_payload or build_tts_artifact
    build_subtitle_payload = build_subtitle_artifact_payload or build_subtitle_artifact
    artifacts["tts"] = build_tts_payload(tts_segments)
    artifacts["subtitle"] = build_subtitle_payload(srt_content, target_language=target_language)
    preview_files["tts_full_audio"] = full_audio_path
    preview_files["srt"] = srt_path

    variant_artifacts["tts"] = build_tts_payload(tts_segments)
    variant_artifacts["subtitle"] = build_subtitle_payload(srt_content, target_language=target_language)
    variant_preview_files["tts_full_audio"] = full_audio_path
    variant_preview_files["srt"] = srt_path

    steps = dict(task.get("steps") or {})
    steps["tts"] = "done"
    steps["subtitle"] = "done"
    steps["compose"] = "done"
    steps["export"] = "done"

    step_messages = dict(task.get("step_messages") or {})
    step_messages["tts"] = f"句子 #{asr_index} 配音已更新"
    step_messages["subtitle"] = "字幕已基于最新配音重新生成"
    step_messages["compose"] = "配音或字幕已更新，请从此步继续重新合成"
    step_messages["export"] = "配音或字幕已更新，请从此步继续重新导出"

    variant_state.update(
        {
            "voice_id": resolved_voice_id or variant_state.get("voice_id"),
            "sentences": sentences,
            "localized_translation": localized_translation,
            "tts_result": {"full_audio_path": full_audio_path, "segments": tts_segments},
            "tts_audio_path": full_audio_path,
            "subtitle_units": subtitle_units,
            "srt_path": srt_path,
            "corrected_subtitle": {"chunks": subtitle_units, "srt_content": srt_content},
            "result": variant_result,
            "exports": variant_exports,
            "artifacts": variant_artifacts,
            "preview_files": variant_preview_files,
        }
    )
    variants = dict(task.get("variants") or {})
    variants[variant] = variant_state

    persist = update_task or store.update
    persist(
        task_id,
        status="done",
        variants=variants,
        steps=steps,
        step_messages=step_messages,
        segments=tts_segments,
        localized_translation=localized_translation,
        tts_audio_path=full_audio_path,
        srt_path=srt_path,
        corrected_subtitle={"chunks": subtitle_units, "srt_content": srt_content},
        result=result,
        exports=exports,
        artifacts=artifacts,
        preview_files=preview_files,
        tos_uploads=tos_uploads,
        voice_id=resolved_voice_id or task.get("voice_id"),
    )
    refresh = refresh_task or refresh_task_state
    updated_task = refresh(task_id, task)
    return TaskAvRewriteOutcome(
        {
            "ok": True,
            "status": updated_sentence["status"],
            "tts_duration": updated_sentence["tts_duration"],
            "compose_stale": True,
            "task": updated_task,
        }
    )
