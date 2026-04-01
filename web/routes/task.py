"""
任务管理蓝图

负责视频上传、任务生命周期管理、翻译确认、文件下载。
不包含任何业务执行逻辑，执行逻辑在 services/pipeline_runner.py。
"""
import os
import uuid

from flask import Blueprint, request, jsonify, send_file, render_template

from config import OUTPUT_DIR, UPLOAD_DIR
from pipeline.alignment import build_script_segments
from pipeline.capcut import deploy_capcut_project
from web.preview_artifacts import build_alignment_artifact, build_translate_artifact
from web import store
from web.services import pipeline_runner

bp = Blueprint("task", __name__, url_prefix="/api/tasks")


@bp.route("/upload-page", endpoint="upload_page")
def upload_page():
    return render_template("index.html")


def _artifact_candidates(task_id: str, name: str, task: dict | None = None, variant: str | None = None) -> list[str]:
    task_dir = (task or {}).get("task_dir") or os.path.join(OUTPUT_DIR, task_id)
    candidates: list[str] = []

    preview_files = (
        ((task or {}).get("variants", {}).get(variant, {}).get("preview_files", {}))
        if variant
        else (task or {}).get("preview_files", {})
    )
    preview_path = preview_files.get(name)
    if preview_path:
        candidates.append(preview_path)

    if variant:
        filename_map = {
            "tts_full_audio": [f"tts_full.{variant}.mp3", f"tts_full.{variant}.wav"],
            "soft_video": [f"{task_id}_soft.{variant}.mp4"],
            "hard_video": [f"{task_id}_hard.{variant}.mp4"],
        }
    else:
        filename_map = {
            "audio_extract": [f"{task_id}_audio.wav", f"{task_id}_audio.mp3"],
            "tts_full_audio": ["tts_full.mp3", "tts_full.wav"],
            "soft_video": [f"{task_id}_soft.mp4", "soft.mp4"],
            "hard_video": [f"{task_id}_hard.mp4", "hard.mp4"],
        }

    for filename in filename_map.get(name, []):
        candidates.append(os.path.join(task_dir, filename))

    return candidates


def _resolve_artifact_path(task_id: str, name: str, task: dict | None = None, variant: str | None = None) -> str | None:
    for path in _artifact_candidates(task_id, name, task, variant=variant):
        if path and os.path.exists(path):
            return os.path.abspath(path)
    return None


@bp.route("", methods=["POST"])
def upload():
    """上传视频，创建任务，返回 task_id"""
    if "video" not in request.files:
        return jsonify({"error": "No video file"}), 400
    file = request.files["video"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    ext = os.path.splitext(file.filename)[1].lower()
    video_path = os.path.join(UPLOAD_DIR, f"{task_id}{ext}")
    file.save(video_path)

    store.create(task_id, video_path, task_dir, original_filename=os.path.basename(file.filename))
    return jsonify({"task_id": task_id}), 201


@bp.route("/<task_id>", methods=["GET"])
def get_task(task_id):
    task = store.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)


@bp.route("/<task_id>/artifact/<name>", methods=["GET"])
def get_artifact(task_id, name):
    task = store.get(task_id)
    variant = request.args.get("variant") or None
    path = _resolve_artifact_path(task_id, name, task, variant=variant)
    if not path:
        return jsonify({"error": "Artifact not found"}), 404

    return send_file(path, as_attachment=False)


@bp.route("/<task_id>/start", methods=["POST"])
def start(task_id):
    """配置并启动流水线"""
    task = store.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    store.update(
        task_id,
        voice_gender=body.get("voice_gender", "male"),
        voice_id=None if body.get("voice_id") in (None, "", "auto") else body.get("voice_id"),
        subtitle_position=body.get("subtitle_position", "bottom"),
        interactive_review=bool(body.get("interactive_review", False)),
    )
    pipeline_runner.start(task_id)
    return jsonify({"status": "started"})


@bp.route("/<task_id>/alignment", methods=["PUT"])
def update_alignment(task_id):
    task = store.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    break_after = body.get("break_after")
    if not isinstance(break_after, list):
        return jsonify({"error": "break_after required"}), 400

    try:
        script_segments = build_script_segments(task.get("utterances", []), break_after)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    store.confirm_alignment(task_id, break_after, script_segments)
    store.set_artifact(
        task_id,
        "alignment",
        build_alignment_artifact(task.get("scene_cuts", []), script_segments, break_after),
    )
    return jsonify({"status": "ok", "script_segments": script_segments})


@bp.route("/<task_id>/segments", methods=["PUT"])
def update_segments(task_id):
    """用户确认/编辑翻译结果"""
    task = store.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json()
    if not body or "segments" not in body:
        return jsonify({"error": "segments required"}), 400

    store.confirm_segments(task_id, body["segments"])
    store.set_artifact(task_id, "translate", build_translate_artifact(body["segments"]))
    return jsonify({"status": "ok"})


@bp.route("/<task_id>/download/<file_type>", methods=["GET"])
def download(task_id, file_type):
    """下载成品文件，file_type: soft | hard | srt"""
    task = store.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    variant = request.args.get("variant") or None
    variant_state = task.get("variants", {}).get(variant, {}) if variant else {}
    result = variant_state.get("result", {}) if variant else task.get("result", {})
    path_map = {
        "soft": result.get("soft_video"),
        "hard": result.get("hard_video"),
        "srt": result.get("srt"),
        "capcut": (
            variant_state.get("exports", {}).get("capcut_archive")
            if variant
            else task.get("exports", {}).get("capcut_archive")
        ),
    }
    path = path_map.get(file_type)
    if not path or not os.path.exists(path):
        return jsonify({"error": "File not ready"}), 404

    return send_file(os.path.abspath(path), as_attachment=True)


@bp.route("/<task_id>/deploy/capcut", methods=["POST"])
def deploy_capcut(task_id):
    task = store.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    variant = request.args.get("variant") or None
    variant_state = task.get("variants", {}).get(variant, {}) if variant else {}
    exports = variant_state.get("exports", {}) if variant else task.get("exports", {})
    project_dir = exports.get("capcut_project")
    if not project_dir or not os.path.isdir(project_dir):
        return jsonify({"error": "CapCut project not ready"}), 404

    deployed_project_dir = deploy_capcut_project(project_dir)
    exports = dict(exports)
    exports["jianying_project_dir"] = deployed_project_dir

    if variant:
        store.update_variant(task_id, variant, exports=exports)
    else:
        store.update(task_id, exports=exports)

    return jsonify({"status": "ok", "deployed_project_dir": deployed_project_dir})
