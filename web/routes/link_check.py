from __future__ import annotations

import uuid
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request, send_file
from flask_login import current_user, login_required

from appcore import medias
from config import OUTPUT_DIR
from web import store
from web.services import link_check_runner

bp = Blueprint("link_check", __name__)

_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _get_owned_task(task_id: str) -> dict:
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id or task.get("type") != "link_check":
        abort(404)
    return task


@bp.route("/link-check")
@login_required
def page():
    return render_template("link_check.html")


@bp.route("/api/link-check/tasks", methods=["POST"])
@login_required
def create_task():
    link_url = (request.form.get("link_url") or "").strip()
    target_language = (request.form.get("target_language") or "").strip().lower()
    if not link_url or not target_language:
        return jsonify({"error": "link_url 和 target_language 必填"}), 400

    language = medias.get_language(target_language)
    if not language or not language.get("enabled"):
        return jsonify({"error": "target_language 非法"}), 400

    task_id = str(uuid.uuid4())
    task_dir = Path(OUTPUT_DIR) / "link_check" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    references = []
    for index, storage in enumerate(request.files.getlist("reference_images")):
        if not storage or not storage.filename:
            continue
        suffix = Path(storage.filename).suffix.lower()
        if suffix and suffix not in _ALLOWED_EXT:
            return jsonify({"error": f"不支持的参考图片格式: {storage.filename}"}), 400
        local_path = task_dir / "reference" / f"ref_{index:03d}{suffix or '.jpg'}"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        storage.save(local_path)
        references.append(
            {
                "id": f"ref-{index}",
                "filename": storage.filename,
                "local_path": str(local_path),
            }
        )

    store.create_link_check(
        task_id,
        str(task_dir),
        user_id=current_user.id,
        link_url=link_url,
        target_language=target_language,
        target_language_name=language.get("name_zh") or target_language,
        reference_images=references,
    )
    link_check_runner.start(task_id)
    return jsonify({"task_id": task_id}), 202


@bp.route("/api/link-check/tasks/<task_id>")
@login_required
def get_task(task_id: str):
    task = _get_owned_task(task_id)
    payload = {
        "id": task["id"],
        "type": task["type"],
        "status": task["status"],
        "link_url": task["link_url"],
        "resolved_url": task.get("resolved_url", ""),
        "page_language": task.get("page_language", ""),
        "target_language": task["target_language"],
        "target_language_name": task["target_language_name"],
        "progress": dict(task.get("progress") or {}),
        "summary": dict(task.get("summary") or {}),
        "error": task.get("error", ""),
        "reference_images": [
            {
                "id": ref["id"],
                "filename": ref["filename"],
                "preview_url": f"/api/link-check/tasks/{task_id}/images/reference/{ref['id']}",
            }
            for ref in task.get("reference_images", [])
        ],
        "items": [
            {
                "id": item["id"],
                "kind": item["kind"],
                "source_url": item["source_url"],
                "site_preview_url": f"/api/link-check/tasks/{task_id}/images/site/{item['id']}",
                "analysis": dict(item.get("analysis") or {}),
                "reference_match": dict(item.get("reference_match") or {}),
                "status": item.get("status") or "pending",
                "error": item.get("error") or "",
            }
            for item in task.get("items", [])
        ],
    }
    return jsonify(payload)


@bp.route("/api/link-check/tasks/<task_id>/images/site/<image_id>")
@login_required
def get_site_image(task_id: str, image_id: str):
    task = _get_owned_task(task_id)
    item = next((it for it in task.get("items", []) if it["id"] == image_id), None)
    if not item:
        abort(404)
    return send_file(item["_local_path"])


@bp.route("/api/link-check/tasks/<task_id>/images/reference/<reference_id>")
@login_required
def get_reference_image(task_id: str, reference_id: str):
    task = _get_owned_task(task_id)
    ref = next((it for it in task.get("reference_images", []) if it["id"] == reference_id), None)
    if not ref:
        abort(404)
    return send_file(ref["local_path"])
