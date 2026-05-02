"""产品级翻译入口路由。

由 ``web.routes.medias`` package 在 PR 2.11 抽出；行为不变。
"""
from __future__ import annotations

import logging

from flask import abort, jsonify, request
from flask_login import current_user, login_required

from appcore import medias
from web.background import start_background_task

from . import bp
from ._helpers import _can_access_product, _ensure_product_listed

log = logging.getLogger(__name__)


@bp.route("/api/products/<int:pid>/translate", methods=["POST"])
@login_required
def api_product_translate(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    blocked = _ensure_product_listed(p)
    if blocked:
        return blocked

    body = request.get_json(silent=True) or {}
    raw_ids = body.get("raw_ids") or []
    target_langs = body.get("target_langs") or []
    content_types = body.get("content_types") or ["copywriting", "detail_images", "video_covers", "videos"]
    allowed_content_types = {"copywriting", "detail_images", "video_covers", "videos"}

    if ("videos" in content_types or "video_covers" in content_types) and not raw_ids:
        return jsonify({"error": "raw_ids 涓嶈兘涓虹┖"}), 400
    if not target_langs:
        return jsonify({"error": "target_langs 涓嶈兘涓虹┖"}), 400

    if not isinstance(content_types, list) or not content_types:
        return jsonify({"error": "content_types 娑撳秷鍏樻稉铏光敄"}), 400

    try:
        raw_ids_int = [int(x) for x in raw_ids]
    except (TypeError, ValueError):
        return jsonify({"error": "raw_ids must be integers"}), 400

    rows = medias.list_raw_sources(pid)
    valid_ids = {int(r["id"]) for r in rows}
    bad = [rid for rid in raw_ids_int if rid not in valid_ids]
    if bad:
        return jsonify({"error": f"raw_ids 涓嶅睘浜庤浜у搧鎴栧凡鍒犻櫎: {bad}"}), 400

    for lang in target_langs:
        if lang == "en" or not medias.is_valid_language(lang):
            return jsonify({"error": f"target_langs 闈炴硶: {lang}"}), 400

    for content_type in content_types:
        if content_type not in allowed_content_types:
            return jsonify({"error": f"content_types 闂堢偞纭? {content_type}"}), 400

    from appcore.bulk_translate_runtime import create_bulk_translate_task, start_task
    from web.routes.bulk_translate import _spawn_scheduler

    initiator = {
        "user_id": current_user.id,
        "user_name": getattr(current_user, "username", "") or "",
        "ip": request.remote_addr or "",
        "user_agent": request.headers.get("User-Agent", "") or "",
        "source": "medias_raw_translate",
    }
    task_id = create_bulk_translate_task(
        user_id=current_user.id,
        product_id=pid,
        target_langs=target_langs,
        content_types=content_types,
        force_retranslate=bool(body.get("force_retranslate")),
        video_params=body.get("video_params") or {},
        initiator=initiator,
        raw_source_ids=raw_ids_int,
    )
    start_task(task_id, current_user.id)
    start_background_task(_spawn_scheduler, task_id)
    return jsonify({"task_id": task_id}), 202


@bp.route("/api/products/<int:pid>/translation-tasks", methods=["GET"])
@login_required
def api_product_translation_tasks(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    from appcore.bulk_translate_projection import list_product_task_ids, list_product_tasks
    from appcore.bulk_translate_runtime import sync_task_with_children_once

    # _is_admin 仍在 facade，通过 routes 拿
    from web.routes import medias as _routes
    scope_user_id = None if _routes._is_admin() else current_user.id

    for task_id in list_product_task_ids(scope_user_id, pid):
        try:
            sync_task_with_children_once(task_id, user_id=scope_user_id)
        except Exception:
            log.warning("bulk translation child sync failed task_id=%s", task_id, exc_info=True)

    return jsonify({"items": list_product_tasks(scope_user_id, pid)})
