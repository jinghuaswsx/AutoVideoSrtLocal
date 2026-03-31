"""
Pipeline execution service.

Runs each processing step in order and streams status updates over Socket.IO.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

from web import store
from web.extensions import socketio
from web.preview_artifacts import (
    build_alignment_artifact,
    build_asr_artifact,
    build_compose_artifact,
    build_export_artifact,
    build_extract_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
    build_tts_artifact,
    build_variant_compare_artifact,
)


def _save_json(task_dir: str, filename: str, data):
    path = os.path.join(task_dir, filename)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def emit(task_id: str, event: str, data: dict):
    socketio.emit(event, data, room=task_id)


def set_step(task_id: str, step: str, status: str, message: str = ""):
    store.set_step(task_id, step, status)
    emit(task_id, "step_update", {"step": step, "status": status, "message": message})


def start(task_id: str):
    thread = threading.Thread(target=_run, args=(task_id,), daemon=True)
    thread.start()


def _run(task_id: str):
    task = store.get(task_id)
    video_path = task["video_path"]
    task_dir = task["task_dir"]

    try:
        _step_extract(task_id, video_path, task_dir)
        _step_asr(task_id, task_dir)
        _step_alignment(task_id, video_path, task_dir)
        _step_translate(task_id)
        _step_tts(task_id, task_dir)
        _step_subtitle(task_id, task_dir)
        _step_compose(task_id, video_path, task_dir)
        _step_export(task_id, video_path, task_dir)
    except Exception as exc:
        store.update(task_id, status="error", error=str(exc))
        emit(task_id, "pipeline_error", {"error": str(exc)})


def _artifact_download_url(task_id: str, file_type: str) -> str:
    return f"/api/tasks/{task_id}/download/{file_type}"


def _set_export_artifact(task_id: str, manifest_text: str):
    payload = build_export_artifact(manifest_text)
    payload["items"][0]["url"] = _artifact_download_url(task_id, "capcut")
    store.set_artifact(task_id, "export", payload)


def _interactive_review_enabled(task_id: str) -> bool:
    task = store.get(task_id) or {}
    return bool(task.get("interactive_review"))


def _wait_for_confirmation(task_id: str, flag_name: str, timeout_seconds: int = 600):
    for _ in range(timeout_seconds):
        current = store.get(task_id) or {}
        if current.get(flag_name):
            return
        time.sleep(1)


def _step_extract(task_id: str, video_path: str, task_dir: str):
    set_step(task_id, "extract", "running", "正在提取音频...")

    from pipeline.extract import extract_audio

    audio_path = extract_audio(video_path, task_dir)
    store.update(task_id, audio_path=audio_path)
    store.set_preview_file(task_id, "audio_extract", audio_path)
    store.set_artifact(task_id, "extract", build_extract_artifact())

    set_step(task_id, "extract", "done", "音频提取完成")


def _step_asr(task_id: str, task_dir: str):
    task = store.get(task_id)
    audio_path = task["audio_path"]

    set_step(task_id, "asr", "running", "正在上传音频到 TOS...")

    from pipeline.asr import transcribe
    from pipeline.storage import delete_file, upload_file

    tos_key = f"asr-audio/{task_id}_{uuid.uuid4().hex[:8]}.wav"
    audio_url = upload_file(audio_path, tos_key)

    set_step(task_id, "asr", "running", "正在识别中文语音...")
    try:
        utterances = transcribe(audio_url)
    finally:
        try:
            delete_file(tos_key)
        except Exception:
            pass

    store.update(task_id, utterances=utterances)
    store.set_artifact(task_id, "asr", build_asr_artifact(utterances))
    _save_json(task_dir, "asr_result.json", {"utterances": utterances})

    set_step(task_id, "asr", "done", f"识别完成，共 {len(utterances)} 段")
    emit(task_id, "asr_result", {"segments": utterances})


def _step_alignment(task_id: str, video_path: str, task_dir: str):
    task = store.get(task_id)

    set_step(task_id, "alignment", "running", "正在分析镜头并生成分段建议...")

    from pipeline.alignment import compile_alignment, detect_scene_cuts
    from pipeline.voice_library import get_voice_library

    scene_cuts = detect_scene_cuts(video_path)
    alignment = compile_alignment(task.get("utterances", []), scene_cuts=scene_cuts)
    suggested_voice = get_voice_library().recommend_voice(
        " ".join(item.get("text", "") for item in task.get("utterances", []))
    )

    store.update(
        task_id,
        scene_cuts=scene_cuts,
        alignment=alignment,
        script_segments=alignment["script_segments"],
        segments=alignment["script_segments"],
        recommended_voice_id=suggested_voice["id"] if suggested_voice else None,
        _alignment_confirmed=False,
    )
    store.set_artifact(
        task_id,
        "alignment",
        build_alignment_artifact(scene_cuts, alignment["script_segments"], alignment["break_after"]),
    )
    _save_json(task_dir, "scene_cuts.json", scene_cuts)
    _save_json(task_dir, "alignment_draft.json", alignment)

    requires_confirmation = _interactive_review_enabled(task_id)
    emit(
        task_id,
        "alignment_result",
        {
            "utterances": task.get("utterances", []),
            "scene_cuts": scene_cuts,
            "break_after": alignment["break_after"],
            "script_segments": alignment["script_segments"],
            "recommended_voice_id": suggested_voice["id"] if suggested_voice else None,
            "requires_confirmation": requires_confirmation,
        },
    )

    if requires_confirmation:
        set_step(task_id, "alignment", "waiting", "分段建议已生成，等待确认")
        _wait_for_confirmation(task_id, "_alignment_confirmed")
        confirmed = store.get(task_id).get("alignment", alignment)
        done_message = "分段已确认"
    else:
        store.confirm_alignment(task_id, alignment["break_after"], alignment["script_segments"])
        confirmed = store.get(task_id).get("alignment", alignment)
        done_message = "分段已自动确认，继续执行"

    store.set_artifact(
        task_id,
        "alignment",
        build_alignment_artifact(
            store.get(task_id).get("scene_cuts", []),
            confirmed.get("script_segments", []),
            confirmed.get("break_after", []),
        ),
    )
    _save_json(task_dir, "alignment_draft.json", confirmed)
    set_step(task_id, "alignment", "done", done_message)


def _step_translate(task_id: str):
    task = store.get(task_id)
    task_dir = task["task_dir"]

    set_step(task_id, "translate", "running", "正在生成整段本土化翻译...")

    from pipeline.localization import VARIANT_KEYS, build_source_full_text_zh
    from pipeline.translate import generate_localized_translation

    source_full_text_zh = build_source_full_text_zh(task.get("script_segments", []))
    variants = dict(task.get("variants", {}))

    for variant in VARIANT_KEYS:
        localized_translation = generate_localized_translation(
            source_full_text_zh,
            task.get("script_segments", []),
            variant=variant,
        )
        variant_state = dict(variants.get(variant, {}))
        variant_state["localized_translation"] = localized_translation
        variants[variant] = variant_state
        _save_json(task_dir, f"localized_translation.{variant}.json", localized_translation)

    localized_translation = variants.get("normal", {}).get("localized_translation", {})

    store.update(
        task_id,
        source_full_text_zh=source_full_text_zh,
        localized_translation=localized_translation,
        variants=variants,
        _segments_confirmed=True,
    )
    store.set_artifact(task_id, "asr", build_asr_artifact(task.get("utterances", []), source_full_text_zh))
    compare_variants = {}
    for variant, variant_state in variants.items():
        payload = build_translate_artifact(source_full_text_zh, variant_state.get("localized_translation", {}))
        store.set_variant_artifact(task_id, variant, "translate", payload)
        compare_variants[variant] = {
            "label": variant_state.get("label", variant),
            "items": payload.get("items", []),
        }
    store.set_artifact(task_id, "translate", build_variant_compare_artifact("翻译本土化", compare_variants))
    _save_json(task_dir, "source_full_text_zh.json", {"full_text": source_full_text_zh})
    _save_json(task_dir, "localized_translation.json", localized_translation)

    emit(
        task_id,
        "translate_result",
        {
            "source_full_text_zh": source_full_text_zh,
            "localized_translation": localized_translation,
            "variants": variants,
            "requires_confirmation": False,
        },
    )
    set_step(task_id, "translate", "done", "本土化翻译完成")


def _step_tts(task_id: str, task_dir: str):
    task = store.get(task_id)

    set_step(task_id, "tts", "running", "正在生成 ElevenLabs 朗读文案与配音...")

    from pipeline.extract import get_video_duration
    from pipeline.localization import VARIANT_KEYS, build_tts_segments
    from pipeline.timeline import build_timeline_manifest
    from pipeline.translate import generate_tts_script
    from pipeline.tts import generate_full_audio, get_default_voice, get_voice_by_id

    voice = None
    if task.get("voice_id"):
        voice = get_voice_by_id(task["voice_id"])
    if not voice and task.get("recommended_voice_id"):
        voice = get_voice_by_id(task["recommended_voice_id"])
    if not voice:
        voice = get_default_voice(task.get("voice_gender", "male"))

    variants = dict(task.get("variants", {}))
    video_duration = get_video_duration(task["video_path"])

    for variant in VARIANT_KEYS:
        variant_state = dict(variants.get(variant, {}))
        localized_translation = variant_state.get("localized_translation", {})
        tts_script = generate_tts_script(localized_translation)
        tts_segments = build_tts_segments(tts_script, task.get("script_segments", []))
        result = generate_full_audio(tts_segments, voice["elevenlabs_voice_id"], task_dir, variant=variant)
        timeline_manifest = build_timeline_manifest(
            result["segments"],
            video_duration=video_duration,
        )

        variant_state.update(
            {
                "segments": result["segments"],
                "tts_script": tts_script,
                "tts_audio_path": result["full_audio_path"],
                "timeline_manifest": timeline_manifest,
                "voice_id": voice["id"],
            }
        )
        variants[variant] = variant_state
        store.set_variant_preview_file(task_id, variant, "tts_full_audio", result["full_audio_path"])
        store.set_variant_artifact(task_id, variant, "tts", build_tts_artifact(tts_script, result["segments"]))
        _save_json(task_dir, f"tts_script.{variant}.json", tts_script)
        _save_json(task_dir, f"tts_result.{variant}.json", result["segments"])
        _save_json(task_dir, f"timeline_manifest.{variant}.json", timeline_manifest)

    normal_variant = variants.get("normal", {})
    store.update(
        task_id,
        variants=variants,
        segments=normal_variant.get("segments", []),
        tts_script=normal_variant.get("tts_script", {}),
        tts_audio_path=normal_variant.get("tts_audio_path"),
        voice_id=voice["id"],
        timeline_manifest=normal_variant.get("timeline_manifest", {}),
    )
    if normal_variant.get("tts_audio_path"):
        store.set_preview_file(task_id, "tts_full_audio", normal_variant["tts_audio_path"])
    compare_variants = {}
    for variant, variant_state in variants.items():
        payload = build_tts_artifact(
            variant_state.get("tts_script", {}),
            variant_state.get("segments", []),
        )
        store.set_variant_artifact(task_id, variant, "tts", payload)
        compare_variants[variant] = {
            "label": variant_state.get("label", variant),
            "items": payload.get("items", []),
        }
    store.set_artifact(task_id, "tts", build_variant_compare_artifact("语音生成", compare_variants))

    emit(task_id, "tts_script_ready", {"tts_script": normal_variant.get("tts_script", {}), "variants": variants})
    set_step(task_id, "tts", "done", "英文配音生成完成")


def _step_subtitle(task_id: str, task_dir: str):
    task = store.get(task_id)

    set_step(task_id, "subtitle", "running", "正在根据英文音频校正字幕...")

    from pipeline.asr import transcribe_local_audio
    from pipeline.subtitle import build_srt_from_chunks, save_srt
    from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr
    from pipeline.tts import _get_audio_duration

    english_utterances = transcribe_local_audio(task["tts_audio_path"], prefix=f"tts-asr/{task_id}")
    english_asr_result = {
        "full_text": " ".join(
            utterance.get("text", "").strip()
            for utterance in english_utterances
            if utterance.get("text")
        ).strip(),
        "utterances": english_utterances,
    }
    corrected_chunks = align_subtitle_chunks_to_asr(
        task.get("tts_script", {}).get("subtitle_chunks", []),
        english_asr_result,
        total_duration=_get_audio_duration(task["tts_audio_path"]),
    )
    srt_content = build_srt_from_chunks(corrected_chunks)
    srt_path = os.path.join(task_dir, "subtitle.srt")
    save_srt(srt_content, srt_path)

    store.update(
        task_id,
        english_asr_result=english_asr_result,
        corrected_subtitle={"chunks": corrected_chunks, "srt_content": srt_content},
        srt_path=srt_path,
    )
    store.set_artifact(
        task_id,
        "subtitle",
        build_subtitle_artifact(english_asr_result, corrected_chunks, srt_content),
    )
    _save_json(task_dir, "english_asr_result.json", english_asr_result)
    _save_json(
        task_dir,
        "corrected_subtitle.json",
        {"chunks": corrected_chunks, "srt_content": srt_content},
    )

    emit(task_id, "english_asr_result", english_asr_result)
    emit(task_id, "subtitle_preview", {"srt": srt_content})
    set_step(task_id, "subtitle", "done", "英文字幕生成完成")


def _step_compose(task_id: str, video_path: str, task_dir: str):
    task = store.get(task_id)

    set_step(task_id, "compose", "running", "正在合成视频...")

    from pipeline.compose import compose_video

    result = compose_video(
        video_path=video_path,
        tts_audio_path=task["tts_audio_path"],
        srt_path=task["srt_path"],
        output_dir=task_dir,
        subtitle_position=task.get("subtitle_position", "bottom"),
        timeline_manifest=task.get("timeline_manifest"),
    )

    store.update(task_id, result=result, status="composing_done")
    store.set_preview_file(task_id, "soft_video", result["soft_video"])
    store.set_preview_file(task_id, "hard_video", result["hard_video"])
    store.set_artifact(task_id, "compose", build_compose_artifact())

    set_step(task_id, "compose", "done", "视频合成完成")


def _step_export(task_id: str, video_path: str, task_dir: str):
    task = store.get(task_id)

    set_step(task_id, "export", "running", "正在导出 CapCut 项目...")

    from pipeline.capcut import export_capcut_project

    export_result = export_capcut_project(
        video_path=video_path,
        tts_audio_path=task["tts_audio_path"],
        srt_path=task["srt_path"],
        timeline_manifest=task.get("timeline_manifest", {}),
        output_dir=task_dir,
        subtitle_position=task.get("subtitle_position", "bottom"),
        draft_title=task.get("original_filename"),
    )

    exports = dict(task.get("exports", {}))
    exports.update(
        {
            "capcut_project": export_result["project_dir"],
            "capcut_archive": export_result["archive_path"],
            "capcut_manifest": export_result["manifest_path"],
        }
    )
    store.update(task_id, exports=exports, status="done")

    manifest_text = ""
    try:
        with open(export_result["manifest_path"], "r", encoding="utf-8") as fh:
            manifest_text = fh.read()
    except OSError:
        manifest_text = ""
    _set_export_artifact(task_id, manifest_text)

    set_step(task_id, "export", "done", "CapCut 项目已导出")
    emit(task_id, "capcut_ready", {"download": _artifact_download_url(task_id, "capcut")})
    emit(
        task_id,
        "pipeline_done",
        {
            "task_id": task_id,
            "downloads": {
                "soft": _artifact_download_url(task_id, "soft"),
                "hard": _artifact_download_url(task_id, "hard"),
                "srt": _artifact_download_url(task_id, "srt"),
                "capcut": _artifact_download_url(task_id, "capcut"),
            },
        },
    )


def _step_subtitle(task_id: str, task_dir: str):
    task = store.get(task_id)

    set_step(task_id, "subtitle", "running", "正在根据英文音频校正字幕...")

    from pipeline.asr import transcribe_local_audio
    from pipeline.localization import VARIANT_KEYS
    from pipeline.subtitle import build_srt_from_chunks, save_srt
    from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr
    from pipeline.tts import _get_audio_duration

    variants = dict(task.get("variants", {}))
    compare_variants = {}

    for variant in VARIANT_KEYS:
        variant_state = dict(variants.get(variant, {}))
        english_utterances = transcribe_local_audio(
            variant_state["tts_audio_path"],
            prefix=f"tts-asr/{task_id}/{variant}",
        )
        english_asr_result = {
            "full_text": " ".join(
                utterance.get("text", "").strip()
                for utterance in english_utterances
                if utterance.get("text")
            ).strip(),
            "utterances": english_utterances,
        }
        corrected_chunks = align_subtitle_chunks_to_asr(
            variant_state.get("tts_script", {}).get("subtitle_chunks", []),
            english_asr_result,
            total_duration=_get_audio_duration(variant_state["tts_audio_path"]),
        )
        srt_content = build_srt_from_chunks(corrected_chunks)
        srt_path = os.path.join(task_dir, f"subtitle.{variant}.srt")
        save_srt(srt_content, srt_path)

        variant_state.update(
            {
                "english_asr_result": english_asr_result,
                "corrected_subtitle": {"chunks": corrected_chunks, "srt_content": srt_content},
                "srt_path": srt_path,
            }
        )
        variants[variant] = variant_state
        payload = build_subtitle_artifact(english_asr_result, corrected_chunks, srt_content)
        store.set_variant_artifact(task_id, variant, "subtitle", payload)
        compare_variants[variant] = {
            "label": variant_state.get("label", variant),
            "items": payload.get("items", []),
        }
        _save_json(task_dir, f"english_asr_result.{variant}.json", english_asr_result)
        _save_json(
            task_dir,
            f"corrected_subtitle.{variant}.json",
            {"chunks": corrected_chunks, "srt_content": srt_content},
        )

    normal_variant = variants.get("normal", {})
    store.update(
        task_id,
        variants=variants,
        english_asr_result=normal_variant.get("english_asr_result", {}),
        corrected_subtitle=normal_variant.get("corrected_subtitle", {}),
        srt_path=normal_variant.get("srt_path"),
    )
    store.set_artifact(task_id, "subtitle", build_variant_compare_artifact("字幕生成", compare_variants))

    emit(
        task_id,
        "english_asr_result",
        {"variants": variants, "english_asr_result": normal_variant.get("english_asr_result", {})},
    )
    emit(
        task_id,
        "subtitle_preview",
        {"variants": variants, "srt": normal_variant.get("corrected_subtitle", {}).get("srt_content", "")},
    )
    set_step(task_id, "subtitle", "done", "英文字幕生成完成")


def _step_compose(task_id: str, video_path: str, task_dir: str):
    task = store.get(task_id)

    set_step(task_id, "compose", "running", "正在合成视频...")

    from pipeline.compose import compose_video
    from pipeline.localization import VARIANT_KEYS

    variants = dict(task.get("variants", {}))
    compare_variants = {}

    for variant in VARIANT_KEYS:
        variant_state = dict(variants.get(variant, {}))
        result = compose_video(
            video_path=video_path,
            tts_audio_path=variant_state["tts_audio_path"],
            srt_path=variant_state["srt_path"],
            output_dir=task_dir,
            subtitle_position=task.get("subtitle_position", "bottom"),
            timeline_manifest=variant_state.get("timeline_manifest"),
            variant=variant,
        )
        variant_state["result"] = result
        variants[variant] = variant_state
        store.set_variant_preview_file(task_id, variant, "soft_video", result["soft_video"])
        store.set_variant_preview_file(task_id, variant, "hard_video", result["hard_video"])
        payload = build_compose_artifact()
        store.set_variant_artifact(task_id, variant, "compose", payload)
        compare_variants[variant] = {
            "label": variant_state.get("label", variant),
            "items": payload.get("items", []),
        }

    normal_variant = variants.get("normal", {})
    store.update(task_id, variants=variants, result=normal_variant.get("result", {}), status="composing_done")
    if normal_variant.get("result", {}).get("soft_video"):
        store.set_preview_file(task_id, "soft_video", normal_variant["result"]["soft_video"])
    if normal_variant.get("result", {}).get("hard_video"):
        store.set_preview_file(task_id, "hard_video", normal_variant["result"]["hard_video"])
    store.set_artifact(task_id, "compose", build_variant_compare_artifact("视频合成", compare_variants))

    set_step(task_id, "compose", "done", "视频合成完成")


def _step_export(task_id: str, video_path: str, task_dir: str):
    task = store.get(task_id)

    set_step(task_id, "export", "running", "正在导出 CapCut 项目...")

    from pipeline.capcut import export_capcut_project
    from pipeline.localization import VARIANT_KEYS

    variants = dict(task.get("variants", {}))
    compare_variants = {}

    for variant in VARIANT_KEYS:
        variant_state = dict(variants.get(variant, {}))
        export_result = export_capcut_project(
            video_path=video_path,
            tts_audio_path=variant_state["tts_audio_path"],
            srt_path=variant_state["srt_path"],
            timeline_manifest=variant_state.get("timeline_manifest", {}),
            output_dir=task_dir,
            subtitle_position=task.get("subtitle_position", "bottom"),
            draft_title=task.get("original_filename"),
            variant=variant,
        )
        exports = dict(variant_state.get("exports", {}))
        exports.update(
            {
                "capcut_project": export_result["project_dir"],
                "capcut_archive": export_result["archive_path"],
                "capcut_manifest": export_result["manifest_path"],
            }
        )
        variant_state["exports"] = exports
        variants[variant] = variant_state

        manifest_text = ""
        try:
            with open(export_result["manifest_path"], "r", encoding="utf-8") as fh:
                manifest_text = fh.read()
        except OSError:
            manifest_text = ""
        payload = build_export_artifact(
            manifest_text,
            deploy_url=f"/api/tasks/{task_id}/deploy/capcut?variant={variant}",
        )
        payload["items"][0]["url"] = f"{_artifact_download_url(task_id, 'capcut')}?variant={variant}"
        store.set_variant_artifact(task_id, variant, "export", payload)
        compare_variants[variant] = {
            "label": variant_state.get("label", variant),
            "items": payload.get("items", []),
        }

    normal_variant = variants.get("normal", {})
    store.update(task_id, variants=variants, exports=normal_variant.get("exports", {}), status="done")
    store.set_artifact(task_id, "export", build_variant_compare_artifact("CapCut 导出", compare_variants))

    set_step(task_id, "export", "done", "CapCut 项目已导出")
    emit(task_id, "capcut_ready", {"download": f"{_artifact_download_url(task_id, 'capcut')}?variant=normal"})
    emit(
        task_id,
        "pipeline_done",
        {
            "task_id": task_id,
            "downloads": {
                "soft": _artifact_download_url(task_id, "soft"),
                "hard": _artifact_download_url(task_id, "hard"),
                "srt": _artifact_download_url(task_id, "srt"),
                "capcut": f"{_artifact_download_url(task_id, 'capcut')}?variant=normal",
            },
        },
    )
