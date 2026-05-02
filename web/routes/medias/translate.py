"""产品级翻译入口路由。

由 web.routes.medias package 在 PR 2.11 抽出；行为不变。
拆分时同步 master 211004f2: 改用 media_product_translate service。
"""
from __future__ import annotations

import logging

from flask import abort, jsonify, request
from flask_login import current_user, login_required

from appcore import medias
from web.services import media_product_translate

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
    result = media_product_translate.start_product_translation(
        user_id=current_user.id,
        product_id=pid,
        user_name=getattr(current_user, "username", "") or "",
        body=body,
        ip=request.remote_addr or "",
        user_agent=request.headers.get("User-Agent", "") or "",
    )
    if not result.ok:
        return jsonify({"error": result.error}), result.status_code
    return jsonify({"task_id": result.task_id}), result.status_code


@bp.route("/api/products/<int:pid>/translation-tasks", methods=["GET"])
@login_required
def api_product_translation_tasks(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    from appcore.bulk_translate_projection import list_product_task_ids, list_product_tasks
    from appcore.bulk_translate_runtime import sync_task_with_children_once

    from web.routes import medias as _routes
    scope_user_id = None if _routes._is_admin() else current_user.id

    for task_id in list_product_task_ids(scope_user_id, pid):
        try:
            sync_task_with_children_once(task_id, user_id=scope_user_id)
        except Exception:
            log.warning("bulk translation child sync failed task_id=%s", task_id, exc_info=True)

    return jsonify({"items": list_product_tasks(scope_user_id, pid)})
