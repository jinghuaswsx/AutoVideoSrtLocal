"""Shared local artifact download logic for translation tasks."""

from __future__ import annotations

import os

from flask import Response, jsonify, send_file
from flask_login import current_user

from appcore.api_keys import resolve_jianying_project_root
from appcore.safe_paths import PathSafetyError, resolve_under_allowed_roots
from config import OUTPUT_DIR, UPLOAD_DIR
from pipeline.capcut import build_capcut_archive_name, rewrite_capcut_project_paths
from web import store


_ARTIFACT_KIND_MAP: dict[str, str] = {
    "soft": "soft_video",
    "hard": "hard_video",
    "srt": "srt",
    "capcut": "capcut_archive",
}

_PREVIEW_NAME_TO_ARTIFACT_KIND: set[str] = {"hard_video", "soft_video", "srt"}


def preview_artifact_tos_redirect(task: dict, name: str, variant: str | None = None):
    """Compatibility hook kept for callers; local mode never redirects to TOS."""
    return None


def _preview_artifact_candidates(
    task_id: str,
    name: str,
    task: dict | None = None,
    variant: str | None = None,
) -> list[str]:
    task_payload = task or {}
    task_dir = task_payload.get("task_dir") or os.path.join(OUTPUT_DIR, task_id)
    candidates: list[str] = []

    preview_files = (
        (task_payload.get("variants", {}).get(variant, {}).get("preview_files", {}))
        if variant
        else task_payload.get("preview_files", {})
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
            "audio_extract": [f"{task_id}_audio.mp3", f"{task_id}_audio.wav"],
            "tts_full_audio": ["tts_full.mp3", "tts_full.wav"],
            "soft_video": [f"{task_id}_soft.mp4", "soft.mp4"],
            "hard_video": [f"{task_id}_hard.mp4", "hard.mp4"],
        }

    for filename in filename_map.get(name, []):
        candidates.append(os.path.join(task_dir, filename))

    return candidates


def resolve_preview_artifact_path(
    task_id: str,
    name: str,
    task: dict | None = None,
    variant: str | None = None,
) -> str | None:
    if not task:
        return None

    for path in _preview_artifact_candidates(task_id, name, task, variant=variant):
        safe_path = _safe_artifact_path(task, path)
        if safe_path and os.path.isfile(safe_path):
            return safe_path
    return None


def _resolved_variant_key(variant: str | None) -> str:
    return variant or "normal"


def _preview_artifact_path(task: dict, name: str, variant: str | None) -> str | None:
    variant_state, result, _exports, srt_path = _paths_for(task, variant)
    preview_files = variant_state.get("preview_files") or {} if variant else task.get("preview_files") or {}
    return {
        "hard_video": preview_files.get("hard_video") or result.get("hard_video"),
        "soft_video": preview_files.get("soft_video") or result.get("soft_video"),
        "srt": preview_files.get("srt") or srt_path,
    }.get(name)


def _artifact_allowed_roots(task: dict) -> list[str]:
    roots = []
    task_dir = (task.get("task_dir") or "").strip()
    if task_dir:
        roots.append(task_dir)
    roots.extend([OUTPUT_DIR, UPLOAD_DIR])
    return roots


def artifact_allowed_roots(task: dict) -> list[str]:
    return _artifact_allowed_roots(task)


def _safe_artifact_path(task: dict, path: str | None) -> str | None:
    if not path:
        return None
    try:
        return str(resolve_under_allowed_roots(path, _artifact_allowed_roots(task)))
    except PathSafetyError:
        return None


def _local_artifact_exists(task: dict, path: str | None) -> bool:
    safe_path = _safe_artifact_path(task, path)
    return bool(safe_path and os.path.isfile(safe_path))


def safe_task_file_response(
    task: dict,
    path: str | None,
    *,
    not_found_message: str = "Artifact not found",
    **send_file_kwargs,
):
    safe_path = _safe_artifact_path(task, path)
    if not safe_path or not os.path.isfile(safe_path):
        return jsonify({"error": not_found_message}), 404
    return send_file(os.path.abspath(safe_path), **send_file_kwargs)


def safe_task_dir_path(task: dict, path: str | None) -> str | None:
    if not path:
        return None
    try:
        safe_path = resolve_under_allowed_roots(path, _artifact_allowed_roots(task))
    except PathSafetyError:
        return None
    if not os.path.isdir(safe_path):
        return None
    return str(safe_path)


def artifact_kind_for_download(file_type: str) -> str | None:
    return _ARTIFACT_KIND_MAP.get(file_type)


def artifact_upload_slot(artifact_kind: str, variant: str | None = None) -> str:
    return f"{_resolved_variant_key(variant)}:{artifact_kind}"


def get_tos_upload_record(task: dict, artifact_kind: str, variant: str | None = None) -> dict | None:
    """Return legacy metadata only; downloads no longer use it."""
    payload = (task.get("tos_uploads") or {}).get(artifact_upload_slot(artifact_kind, variant))
    if isinstance(payload, dict) and payload.get("tos_key"):
        return payload
    return None


def upload_capcut_archive_for_current_user(
    task_id: str,
    task: dict,
    variant: str | None,
    archive_path: str,
) -> dict | None:
    """Compatibility hook; CapCut archives are served from local disk."""
    if not _local_artifact_exists(task, archive_path):
        return None
    return {
        "storage_backend": "local",
        "artifact_kind": "capcut_archive",
        "variant": _resolved_variant_key(variant),
        "file_size": os.path.getsize(archive_path),
    }


def _paths_for(task: dict, variant: str | None) -> tuple[dict, dict, dict, str | None]:
    if variant:
        variant_state = (task.get("variants") or {}).get(variant, {}) or {}
        result = variant_state.get("result") or {}
        exports = variant_state.get("exports") or {}
        srt_path = variant_state.get("srt_path")
    else:
        variant_state = {}
        result = task.get("result") or {}
        exports = task.get("exports") or {}
        srt_path = task.get("srt_path")
    return variant_state, result, exports, srt_path


def _rewrite_capcut_for_current_user(
    task_id: str,
    task: dict,
    variant: str | None,
    exports: dict,
    archive_path: str | None,
    rewrite_capcut_paths: bool,
) -> None:
    if not rewrite_capcut_paths or not _local_artifact_exists(task, archive_path):
        return

    project_dir = exports.get("capcut_project")
    if not project_dir or not os.path.isdir(project_dir):
        return

    manifest_path = exports.get("capcut_manifest")
    try:
        jianying_project_dir = rewrite_capcut_project_paths(
            project_dir=project_dir,
            manifest_path=manifest_path,
            archive_path=archive_path,
            jianying_project_root=resolve_jianying_project_root(current_user.id),
        )
        updated_exports = dict(exports)
        updated_exports["jianying_project_dir"] = jianying_project_dir
        if variant:
            store.update_variant(task_id, variant, exports=updated_exports)
        else:
            store.update(task_id, exports=updated_exports)
    except Exception:
        pass


def _send_local_artifact(task: dict, task_id: str, file_type: str, variant: str | None, path: str) -> Response:
    safe_path = _safe_artifact_path(task, path)
    if not safe_path:
        return jsonify({
            "error": "local artifact missing",
            "message": "本地产物缺失，请先运行本地存储迁移回填，或重新生成该任务。",
        }), 404
    if not os.path.isfile(safe_path):
        return jsonify({"error": "local artifact missing"}), 404
    download_name = None
    if file_type == "capcut":
        source_name = task.get("display_name") or task.get("original_filename") or task_id
        download_name = build_capcut_archive_name(source_name, variant=variant)
    return send_file(os.path.abspath(safe_path), as_attachment=True, download_name=download_name)


def serve_artifact_download(
    task: dict,
    task_id: str,
    file_type: str,
    variant: str | None = None,
    rewrite_capcut_paths: bool = True,
) -> Response:
    """Serve a task artifact from local disk only."""
    _variant_state, result, exports, srt_path = _paths_for(task, variant)
    path_map = {
        "soft": result.get("soft_video"),
        "hard": result.get("hard_video"),
        "srt": srt_path,
        "capcut": exports.get("capcut_archive"),
    }
    path = path_map.get(file_type)
    local_available = _local_artifact_exists(task, path)

    if file_type == "capcut":
        _rewrite_capcut_for_current_user(task_id, task, variant, exports, path, rewrite_capcut_paths)

    if local_available:
        return _send_local_artifact(task, task_id, file_type, variant, path)

    return jsonify({
        "error": "local artifact missing",
        "message": "本地产物缺失，请先运行本地存储迁移回填，或重新生成该任务。",
    }), 404
