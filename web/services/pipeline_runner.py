"""
流水线执行服务

负责按顺序调度各 pipeline 模块，通过 SocketIO 推送实时进度。
与 HTTP 路由层解耦：路由只负责启动线程，具体执行逻辑在此处。
"""
import os
import json
import time
import uuid
import threading

from web.extensions import socketio
from web import store


def _save_json(task_dir: str, filename: str, data):
    """保存中间结果到任务目录"""
    path = os.path.join(task_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def emit(task_id: str, event: str, data: dict):
    socketio.emit(event, data, room=task_id)


def set_step(task_id: str, step: str, status: str, message: str = ""):
    store.set_step(task_id, step, status)
    emit(task_id, "step_update", {"step": step, "status": status, "message": message})


def start(task_id: str):
    """在后台线程中启动流水线"""
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


# ── 各步骤 ────────────────────────────────────────────────────

def _step_extract(task_id: str, video_path: str, task_dir: str):
    set_step(task_id, "extract", "running", "正在提取音频...")
    from pipeline.extract import extract_audio
    audio_path = extract_audio(video_path, task_dir)
    store.update(task_id, audio_path=audio_path)
    set_step(task_id, "extract", "done", "音频提取完成")


def _step_asr(task_id: str, task_dir: str):
    task = store.get(task_id)
    audio_path = task["audio_path"]

    set_step(task_id, "asr", "running", "正在上传音频到 TOS...")
    from pipeline.storage import upload_file, delete_file
    from pipeline.asr import transcribe

    tos_key = f"asr-audio/{task_id}_{uuid.uuid4().hex[:8]}.wav"
    audio_url = upload_file(audio_path, tos_key)

    set_step(task_id, "asr", "running", "正在识别语音（豆包 ASR）...")
    try:
        utterances = transcribe(audio_url)
    finally:
        try:
            delete_file(tos_key)
        except Exception:
            pass

    store.update(task_id, utterances=utterances)
    _save_json(task_dir, "asr_result.json", utterances)
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
    _save_json(task_dir, "scene_cuts.json", scene_cuts)
    _save_json(task_dir, "alignment_draft.json", alignment)
    set_step(task_id, "alignment", "waiting", "分段建议已生成，等待您确认")
    emit(
        task_id,
        "alignment_result",
        {
            "utterances": task.get("utterances", []),
            "scene_cuts": scene_cuts,
            "break_after": alignment["break_after"],
            "script_segments": alignment["script_segments"],
            "recommended_voice_id": suggested_voice["id"] if suggested_voice else None,
        },
    )

    for _ in range(600):
        if store.get(task_id).get("_alignment_confirmed"):
            break
        time.sleep(1)

    confirmed = store.get(task_id).get("alignment", alignment)
    _save_json(task_dir, "alignment_draft.json", confirmed)
    set_step(task_id, "alignment", "done", "分段已确认")


def _step_translate(task_id: str):
    task = store.get(task_id)
    task_dir = task["task_dir"]

    set_step(task_id, "translate", "running", "正在翻译文案（Claude）...")
    from pipeline.translate import translate_segments
    segments = translate_segments(task["script_segments"])

    store.update(task_id, segments=segments, script_segments=segments, _segments_confirmed=False)
    _save_json(task_dir, "translate_result.json", segments)
    set_step(task_id, "translate", "waiting", "翻译完成，等待您确认/编辑")
    emit(task_id, "translate_result", {"segments": segments})

    # 等待用户确认（最多 10 分钟）
    for _ in range(600):
        if store.get(task_id).get("_segments_confirmed"):
            break
        time.sleep(1)

    # 保存用户确认后的最终版本
    _save_json(task_dir, "translate_confirmed.json", store.get(task_id)["script_segments"])
    set_step(task_id, "translate", "done", "翻译已确认")


def _step_tts(task_id: str, task_dir: str):
    task = store.get(task_id)

    set_step(task_id, "tts", "running", "正在生成英文语音（ElevenLabs）...")
    from pipeline.extract import get_video_duration
    from pipeline.timeline import build_timeline_manifest
    from pipeline.tts import get_default_voice, get_voice_by_id, generate_full_audio

    voice = None
    if task.get("voice_id"):
        voice = get_voice_by_id(task["voice_id"])
    if not voice and task.get("recommended_voice_id"):
        voice = get_voice_by_id(task["recommended_voice_id"])
    if not voice:
        voice = get_default_voice(task.get("voice_gender", "male"))

    result = generate_full_audio(
        task["script_segments"],
        voice["elevenlabs_voice_id"],
        task_dir,
    )
    timeline_manifest = build_timeline_manifest(
        result["segments"],
        video_duration=get_video_duration(task["video_path"]),
    )
    store.update(
        task_id,
        segments=result["segments"],
        script_segments=result["segments"],
        tts_audio_path=result["full_audio_path"],
        voice_id=voice["id"],
        timeline_manifest=timeline_manifest,
    )
    _save_json(task_dir, "tts_result.json", result["segments"])
    _save_json(task_dir, "timeline_manifest.json", timeline_manifest)
    set_step(task_id, "tts", "done", "语音生成完成")


def _step_subtitle(task_id: str, task_dir: str):
    task = store.get(task_id)

    set_step(task_id, "subtitle", "running", "正在生成字幕...")
    from pipeline.subtitle import build_srt_from_manifest, save_srt
    srt_content = build_srt_from_manifest(task["timeline_manifest"])
    srt_path = os.path.join(task_dir, "subtitle.srt")
    save_srt(srt_content, srt_path)

    store.update(task_id, srt_path=srt_path)
    set_step(task_id, "subtitle", "done", "字幕生成完成")
    emit(task_id, "subtitle_preview", {"srt": srt_content})


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
    set_step(task_id, "compose", "done", "合成完成！")


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
    set_step(task_id, "export", "done", "CapCut 项目已导出")
    emit(task_id, "capcut_ready", {"download": f"/api/tasks/{task_id}/download/capcut"})
    emit(task_id, "pipeline_done", {
        "task_id": task_id,
        "downloads": {
            "soft": f"/api/tasks/{task_id}/download/soft",
            "hard": f"/api/tasks/{task_id}/download/hard",
            "srt": f"/api/tasks/{task_id}/download/srt",
            "capcut": f"/api/tasks/{task_id}/download/capcut",
        },
    })
