from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from flask import abort, jsonify, request
from flask_login import current_user, login_required

from appcore import medias
from . import bp


def _routes_module():
    from web.routes import medias as routes

    return routes


def _collect_link_check_reference_images(pid: int, lang: str, task_dir: Path) -> list[dict]:
    routes = _routes_module()
    references: list[dict] = []
    ref_dir = task_dir / "reference"
    ref_dir.mkdir(parents=True, exist_ok=True)

    cover_key = medias.get_product_covers(pid).get(lang)
    if cover_key:
        cover_suffix = Path(cover_key).suffix or ".jpg"
        cover_local = ref_dir / f"cover_{lang}{cover_suffix}"
        routes._download_media_object(cover_key, cover_local)
        references.append({
            "id": f"cover-{lang}",
            "filename": f"cover_{lang}{cover_suffix}",
            "local_path": str(cover_local),
        })

    for idx, row in enumerate(medias.list_detail_images(pid, lang), start=1):
        object_key = row.get("object_key") or ""
        detail_suffix = Path(object_key).suffix or ".jpg"
        detail_local = ref_dir / f"detail_{idx:03d}{detail_suffix}"
        routes._download_media_object(object_key, detail_local)
        references.append({
            "id": f"detail-{row['id']}",
            "filename": f"detail_{idx:03d}{detail_suffix}",
            "local_path": str(detail_local),
        })

    return references


@bp.route("/api/products/<int:pid>/link-check", methods=["POST"])
@login_required
def api_product_link_check_create(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)

    body = request.get_json(silent=True) or {}
    lang = (body.get("lang") or "").strip().lower()
    if not lang or not medias.is_valid_language(lang):
        return jsonify({"error": f"unsupported language: {lang}"}), 400

    link_url = (body.get("link_url") or "").strip()
    if not link_url.startswith(("http://", "https://")):
        return jsonify({"error": "valid product link_url required"}), 400

    language = medias.get_language(lang)
    if not language or not language.get("enabled"):
        return jsonify({"error": "target language is invalid"}), 400

    task_id = str(uuid.uuid4())
    task_dir = Path(routes.OUTPUT_DIR) / "link_check" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    references = _collect_link_check_reference_images(pid, lang, task_dir)
    if not references:
        return jsonify({"error": "当前语种缺少参考图"}), 400

    routes.store.create_link_check(
        task_id,
        str(task_dir),
        user_id=current_user.id,
        link_url=link_url,
        target_language=lang,
        target_language_name=language.get("name_zh") or lang,
        reference_images=references,
    )
    medias.set_product_link_check_task(pid, lang, {
        "task_id": task_id,
        "status": "queued",
        "link_url": link_url,
        "checked_at": datetime.now(UTC).isoformat(),
        "summary": {
            "overall_decision": "running",
            "pass_count": 0,
            "replace_count": 0,
            "review_count": 0,
        },
    })
    routes.link_check_runner.start(task_id)
    return jsonify({"task_id": task_id, "status": "queued", "reference_count": len(references)}), 202


@bp.route("/api/products/<int:pid>/link-check/<lang>", methods=["GET"])
@login_required
def api_product_link_check_get(pid: int, lang: str):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"不支持的语言: {lang}"}), 400

    tasks = medias.parse_link_check_tasks_json(p.get("link_check_tasks_json"))
    meta = tasks.get(lang)
    if not meta:
        return jsonify({"task": None})

    task = routes.store.get(meta.get("task_id", ""))
    if not task or task.get("_user_id") != current_user.id or task.get("type") != "link_check":
        return jsonify({"task": None})

    refreshed = {
        "task_id": meta.get("task_id", ""),
        "status": task.get("status", meta.get("status", "")),
        "link_url": meta.get("link_url", ""),
        "checked_at": meta.get("checked_at", ""),
        "summary": dict(task.get("summary") or meta.get("summary") or {}),
        "progress": dict(task.get("progress") or {}),
        "has_detail": True,
        "resolved_url": task.get("resolved_url", ""),
        "page_language": task.get("page_language", ""),
    }
    medias.set_product_link_check_task(pid, lang, {
        "task_id": refreshed["task_id"],
        "status": refreshed["status"],
        "link_url": refreshed["link_url"],
        "checked_at": refreshed["checked_at"],
        "summary": refreshed["summary"],
    })
    return jsonify({"task": refreshed})


@bp.route("/api/products/<int:pid>/link-check/<lang>/detail", methods=["GET"])
@login_required
def api_product_link_check_detail(pid: int, lang: str):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"不支持的语言: {lang}"}), 400

    tasks = medias.parse_link_check_tasks_json(p.get("link_check_tasks_json"))
    meta = tasks.get(lang)
    if not meta:
        return jsonify({"error": "task not found"}), 404

    task = routes.store.get(meta.get("task_id", ""))
    if not task or task.get("_user_id") != current_user.id or task.get("type") != "link_check":
        return jsonify({"error": "task not found"}), 404
    return jsonify(routes._serialize_link_check_task(task))
