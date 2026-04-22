"""Shared artifact download logic for translation tasks."""

from __future__ import annotations

import os
from datetime import datetime

from flask import Response, jsonify, redirect, send_file
from flask_login import current_user

from appcore import tos_clients
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
    """Return a signed TOS redirect for preview routes when local preview is unavailable."""
    if name not in _PREVIEW_NAME_TO_ARTIFACT_KIND:
        return None
    if not _is_pure_tos_task(task) and _local_artifact_exists(_preview_artifact_path(task, name, variant)):
        return None
    record = get_tos_upload_record(task, name, variant)
    if not record:
        return None
    try:
        return redirect(tos_clients.generate_signed_download_url(record["tos_key"]))
    except Exception:
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


def _is_pure_tos_task(task: dict) -> bool:
    return (task.get("delivery_mode") or "").strip() == "pure_tos"


def _local_artifact_exists(path: str | None) -> bool:
    return bool(path and os.path.exists(path))


def artifact_kind_for_download(file_type: str) -> str | None:
    return _ARTIFACT_KIND_MAP.get(file_type)


def artifact_upload_slot(artifact_kind: str, variant: str | None = None) -> str:
    return f"{_resolved_variant_key(variant)}:{artifact_kind}"


def get_tos_upload_record(task: dict, artifact_kind: str, variant: str | None = None) -> dict | None:
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
    """Upload a rewritten CapCut archive to TOS for the current user."""
    if not archive_path or not os.path.exists(archive_path) or not tos_clients.is_tos_configured():
        return None

    resolved_variant = _resolved_variant_key(variant)
    source_name = task.get("display_name") or task.get("original_filename") or task_id
    download_name = build_capcut_archive_name(source_name, variant=variant)
    tos_key = tos_clients.build_artifact_object_key(current_user.id, task_id, resolved_variant, download_name)
    slot = artifact_upload_slot("capcut_archive", variant)
    uploads = dict(task.get("tos_uploads") or {})
    previous = uploads.get(slot)

    if isinstance(previous, dict):
        previous_key = previous.get("tos_key")
        if previous_key and previous_key != tos_key:
            try:
                tos_clients.delete_object(previous_key)
            except Exception:
                pass

    tos_clients.upload_file(archive_path, tos_key)
    payload = {
        "tos_key": tos_key,
        "artifact_kind": "capcut_archive",
        "variant": resolved_variant,
        "file_size": os.path.getsize(archive_path),
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "jianying_project_root": resolve_jianying_project_root(current_user.id),
    }
    uploads[slot] = payload
    store.update(task_id, tos_uploads=uploads)
    return payload


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
    """Serve a task artifact with local-first behavior for non-``pure_tos`` tasks."""
    _variant_state, result, exports, srt_path = _paths_for(task, variant)
    path_map = {
        "soft": result.get("soft_video"),
        "hard": result.get("hard_video"),
        "srt": srt_path,
        "capcut": exports.get("capcut_archive"),
    }
    path = path_map.get(file_type)
    artifact_kind = artifact_kind_for_download(file_type)
    pure_tos = _is_pure_tos_task(task)
    local_available = _local_artifact_exists(path)

    if file_type == "capcut":
        _rewrite_capcut_for_current_user(task_id, task, variant, exports, path, rewrite_capcut_paths)

    if local_available and not pure_tos:
        return _send_local_artifact(task, task_id, file_type, variant, path)

    uploaded_artifact = get_tos_upload_record(task, artifact_kind, variant) if artifact_kind else None
    if uploaded_artifact:
        try:
            return redirect(tos_clients.generate_signed_download_url(uploaded_artifact["tos_key"]))
        except Exception:
            pass

    if file_type == "capcut" and pure_tos and local_available:
        try:
            upload_payload = upload_capcut_archive_for_current_user(task_id, task, variant, path)
        except Exception:
            upload_payload = None
        if upload_payload:
            try:
                return redirect(tos_clients.generate_signed_download_url(upload_payload["tos_key"]))
            except Exception:
                pass
        return jsonify({"error": "CapCut 工程包尚未上传到 TOS，暂不可下载"}), 409

    if pure_tos and artifact_kind:
        return jsonify({"error": "下载文件尚未上传到 TOS，暂不可下载"}), 409

    if not local_available:
        return jsonify({"error": "File not ready"}), 404

    return _send_local_artifact(task, task_id, file_type, variant, path)
