"""统一的成品下载逻辑。

翻译主线（英文/德语/法语）的下载路由共用这个 helper，保证：
  1. 下载优先走 TOS 预签名 URL（客户端直连 TOS，不经过 Flask）
  2. TOS 不可用或尚未上传时才 fallback 到本地 send_file
  3. 下载 CapCut 工程包时，按当前下载用户的 jianying_project_root 重写路径
     后即时上传 TOS，并记录到 task.tos_uploads

被调用方（三个模块的 download 路由）只负责：
  - 鉴权（确认 task 归属）
  - 解析 variant 参数
  - 把 task / task_id / file_type / variant 交给 serve_artifact_download
"""
from __future__ import annotations
import os
from datetime import datetime

from flask import Response, jsonify, redirect, send_file
from flask_login import current_user

from appcore import tos_clients
from appcore.api_keys import resolve_jianying_project_root
from pipeline.capcut import (
    build_capcut_archive_name,
    rewrite_capcut_project_paths,
)
from web import store


_ARTIFACT_KIND_MAP: dict[str, str] = {
    "soft": "soft_video",
    "hard": "hard_video",
    "srt": "srt",
    "capcut": "capcut_archive",
}


def _resolved_variant_key(variant: str | None) -> str:
    return variant or "normal"


def _is_pure_tos_task(task: dict) -> bool:
    return (task.get("delivery_mode") or "").strip() == "pure_tos"


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
    task_id: str, task: dict, variant: str | None, archive_path: str
) -> dict | None:
    """点击 CapCut 下载时，按当前用户重写路径并上传 TOS，返回上传记录或 None。"""
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
    """返回 (variant_state, result, exports, srt_path)。

    保留与原英文模块 task.py 严格一致的语义：
      - variant 为 None 时，从 task 顶层字段读（适配历史任务格式）
      - variant 有值时，从 task.variants[variant] 读
    """
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


def serve_artifact_download(
    task: dict,
    task_id: str,
    file_type: str,
    variant: str | None = None,
    rewrite_capcut_paths: bool = True,
) -> Response:
    """处理翻译主线的下载请求，统一逻辑：TOS 优先，本地兜底。

    参数:
        task:     store.get(task_id) 的结果；路由层已经做过归属校验
        task_id:  项目 ID
        file_type: 前端传入的下载类型：soft / hard / srt / capcut
        variant:  variant key，None 表示使用顶层字段（历史格式）
        rewrite_capcut_paths: 下载 capcut 时是否按当前用户重写工程路径（默认 True）
    """
    variant_state, result, exports, srt_path = _paths_for(task, variant)
    path_map = {
        "soft": result.get("soft_video"),
        "hard": result.get("hard_video"),
        "srt": srt_path,
        "capcut": exports.get("capcut_archive"),
    }
    path = path_map.get(file_type)
    artifact_kind = artifact_kind_for_download(file_type)

    # ─── CapCut：按当前用户重写路径 + 即时上传 ───
    if file_type == "capcut" and path:
        project_dir = exports.get("capcut_project")
        if rewrite_capcut_paths and project_dir and os.path.isdir(project_dir):
            manifest_path = exports.get("capcut_manifest")
            try:
                jianying_project_dir = rewrite_capcut_project_paths(
                    project_dir=project_dir,
                    manifest_path=manifest_path,
                    archive_path=path,
                    jianying_project_root=resolve_jianying_project_root(current_user.id),
                )
                updated_exports = dict(exports)
                updated_exports["jianying_project_dir"] = jianying_project_dir
                if variant:
                    store.update_variant(task_id, variant, exports=updated_exports)
                else:
                    store.update(task_id, exports=updated_exports)
            except Exception:
                pass  # 重写失败不阻断下载
        try:
            upload_payload = upload_capcut_archive_for_current_user(task_id, task, variant, path)
        except Exception:
            upload_payload = None
        if upload_payload:
            try:
                return redirect(tos_clients.generate_signed_download_url(upload_payload["tos_key"]))
            except Exception:
                pass
        if _is_pure_tos_task(task):
            return jsonify({"error": "CapCut 工程包尚未上传到 TOS，暂不可下载"}), 409

    # ─── 其它类型：优先查任务完成时上传的 TOS 记录 ───
    if artifact_kind:
        uploaded_artifact = get_tos_upload_record(task, artifact_kind, variant)
        if uploaded_artifact:
            try:
                return redirect(tos_clients.generate_signed_download_url(uploaded_artifact["tos_key"]))
            except Exception:
                pass
        if _is_pure_tos_task(task):
            return jsonify({"error": "下载文件尚未上传到 TOS，暂不可下载"}), 409

    # ─── Fallback：本地文件 ───
    if not path or not os.path.exists(path):
        return jsonify({"error": "File not ready"}), 404

    download_name = None
    if file_type == "capcut":
        source_name = task.get("display_name") or task.get("original_filename") or task_id
        download_name = build_capcut_archive_name(source_name, variant=variant)
    return send_file(os.path.abspath(path), as_attachment=True, download_name=download_name)
