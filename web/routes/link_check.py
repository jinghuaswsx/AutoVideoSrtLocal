from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from appcore import cleanup, medias
from appcore.db import execute, query, query_one
from appcore.link_check_locale import build_link_check_display_name, detect_target_language_from_url
from appcore.task_recovery import recover_all_interrupted_tasks, recover_project_if_needed
from config import OUTPUT_DIR
from web import store
from web.services import link_check_runner

bp = Blueprint("link_check", __name__)

_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
_DEFAULT_TARGET_LANGUAGE = "en"


def _enabled_language_map() -> dict[str, dict]:
    try:
        rows = medias.list_languages() or []
    except Exception:
        rows = []
    mapping: dict[str, dict] = {}
    for row in rows:
        code = (row.get("code") or "").strip().lower()
        if code and row.get("enabled", 1):
            mapping[code] = row
    return mapping


def _load_task_from_row(row: dict | None) -> dict | None:
    if not row:
        return None

    task_id = row.get("id") or ""
    state: dict = {}
    raw = row.get("state_json") or ""
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                state = loaded
        except Exception:
            state = {}

    state.setdefault("id", task_id)
    state.setdefault("type", "link_check")
    state.setdefault("status", row.get("status") or "queued")
    state.setdefault("display_name", row.get("display_name") or "")
    state.setdefault("original_filename", row.get("original_filename") or "")
    state.setdefault("task_dir", row.get("task_dir") or "")
    state.setdefault("link_url", "")
    state.setdefault("resolved_url", "")
    state.setdefault("page_language", "")
    state.setdefault("target_language", "")
    state.setdefault("target_language_name", "")
    state.setdefault("progress", {})
    state.setdefault("summary", {})
    state.setdefault("error", "")
    state.setdefault("reference_images", [])
    state.setdefault("items", [])
    return state


def _get_project_row(task_id: str) -> dict:
    row = query_one(
        "SELECT * FROM projects WHERE id = %s AND type = 'link_check' AND deleted_at IS NULL",
        (task_id,),
    )
    if not row:
        abort(404)
    return row


def _get_task(task_id: str) -> tuple[dict, dict]:
    row = _get_project_row(task_id)
    row_task = _load_task_from_row(row)
    if not row_task:
        abort(404)

    store_task = store.get(task_id)
    if store_task and store_task.get("type") != "link_check":
        store_task = None

    if store_task and (row.get("status") or "") == (store_task.get("status") or ""):
        merged_task = dict(store_task)
        if row.get("display_name") is not None:
            merged_task["display_name"] = row.get("display_name") or ""
        if row.get("original_filename") is not None:
            merged_task["original_filename"] = row.get("original_filename") or ""
        return row, merged_task

    return row, row_task


def _serialize_task(task_id: str, task: dict) -> dict:
    return {
        "id": task["id"],
        "type": task["type"],
        "status": task["status"],
        "display_name": task.get("display_name", ""),
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
                "binary_quick_check": dict(item.get("binary_quick_check") or {}),
                "same_image_llm": dict(item.get("same_image_llm") or {}),
                "status": item.get("status") or "pending",
                "error": item.get("error") or "",
            }
            for item in task.get("items", [])
        ],
    }


@bp.route("/link-check")
@login_required
def page():
    recover_all_interrupted_tasks()
    try:
        rows = query(
            """SELECT id, display_name, original_filename, status, created_at
               FROM projects
               WHERE type = 'link_check' AND deleted_at IS NULL
               ORDER BY created_at DESC
               LIMIT 200""",
            (),
        )
    except Exception:
        rows = []
    return render_template("link_check.html", projects=rows or [])


@bp.route("/link-check/<task_id>")
@login_required
def detail_page(task_id: str):
    recover_project_if_needed(task_id, "link_check")
    row, task = _get_task(task_id)
    return render_template(
        "link_check_detail.html",
        project=row,
        task=task,
        initial_task=task,
    )


@bp.route("/api/link-check/tasks", methods=["POST"])
@login_required
def create_task():
    link_url = (request.form.get("link_url") or "").strip()
    target_language = (request.form.get("target_language") or "").strip().lower()
    if not link_url:
        return jsonify({"error": "link_url 必填"}), 400

    enabled_languages = _enabled_language_map()
    if not target_language:
        target_language = detect_target_language_from_url(link_url, set(enabled_languages))
    if not target_language:
        target_language = _DEFAULT_TARGET_LANGUAGE

    language = enabled_languages.get(target_language)
    if not language:
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
        display_name=build_link_check_display_name(link_url, target_language),
    )
    link_check_runner.start(task_id)
    return jsonify(
        {
            "task_id": task_id,
            "detail_url": url_for("link_check.detail_page", task_id=task_id),
        }
    ), 202


@bp.route("/api/link-check/tasks/<task_id>")
@login_required
def get_task(task_id: str):
    recover_project_if_needed(task_id, "link_check")
    _row, task = _get_task(task_id)
    return jsonify(_serialize_task(task_id, task))


@bp.route("/api/link-check/tasks/<task_id>", methods=["PATCH"])
@login_required
def rename_task(task_id: str):
    row = query_one(
        "SELECT id FROM projects WHERE id = %s AND type = 'link_check' AND deleted_at IS NULL",
        (task_id,),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    new_name = (body.get("display_name") or "").strip()
    if not new_name:
        return jsonify({"error": "display_name required"}), 400
    if len(new_name) > 50:
        return jsonify({"error": "名称不能超过50个字符"}), 400

    execute("UPDATE projects SET display_name=%s WHERE id=%s", (new_name, task_id))
    task = store.get(task_id)
    if task and task.get("type") == "link_check":
        store.update(task_id, display_name=new_name)
    return jsonify({"status": "ok", "display_name": new_name})


@bp.route("/api/link-check/tasks/<task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id: str):
    row = query_one(
        "SELECT id, task_dir, state_json FROM projects WHERE id=%s AND type = 'link_check' AND deleted_at IS NULL",
        (task_id,),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404

    task = store.get(task_id) or {}
    cleanup_payload = dict(task)
    cleanup_payload["task_dir"] = row.get("task_dir") or cleanup_payload.get("task_dir", "")
    cleanup_payload["state_json"] = row.get("state_json") or ""
    cleanup_payload["tos_keys"] = cleanup.collect_task_tos_keys(cleanup_payload)
    try:
        cleanup.delete_task_storage(cleanup_payload)
    except Exception:
        pass

    execute(
        "UPDATE projects SET deleted_at=%s WHERE id=%s",
        (datetime.now(timezone.utc), task_id),
    )
    if task and task.get("type") == "link_check":
        store.update(task_id, status="deleted")
    return jsonify({"status": "ok"})


@bp.route("/api/link-check/tasks/<task_id>/images/site/<image_id>")
@login_required
def get_site_image(task_id: str, image_id: str):
    _row, task = _get_task(task_id)
    item = next((it for it in task.get("items", []) if it["id"] == image_id), None)
    if not item:
        abort(404)
    return send_file(item["_local_path"])


@bp.route("/api/link-check/tasks/<task_id>/images/reference/<reference_id>")
@login_required
def get_reference_image(task_id: str, reference_id: str):
    _row, task = _get_task(task_id)
    ref = next((it for it in task.get("reference_images", []) if it["id"] == reference_id), None)
    if not ref:
        abort(404)
    return send_file(ref["local_path"])
