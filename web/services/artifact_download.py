"""Shared local artifact download logic for translation tasks."""

from __future__ import annotations

import os

from flask import Response, jsonify, send_file
from flask_login import current_user

from appcore.api_keys import resolve_jianying_project_root
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


def _local_artifact_exists(path: str | None) -> bool:
    return bool(path and os.path.exists(path))


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
    if not archive_path or not os.path.exists(archive_path):
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
    if not rewrite_capcut_paths or not _local_artifact_exists(archive_path):
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
    download_name = None
    if file_type == "capcut":
        source_name = task.get("display_name") or task.get("original_filename") or task_id
        download_name = build_capcut_archive_name(source_name, variant=variant)
    return send_file(os.path.abspath(path), as_attachment=True, download_name=download_name)


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
    local_available = _local_artifact_exists(path)

    if file_type == "capcut":
        _rewrite_capcut_for_current_user(task_id, task, variant, exports, path, rewrite_capcut_paths)

    if local_available:
        return _send_local_artifact(task, task_id, file_type, variant, path)

    return jsonify({
        "error": "local artifact missing",
        "message": "本地产物缺失，请先运行本地存储迁移回填，或重新生成该任务。",
    }), 404
