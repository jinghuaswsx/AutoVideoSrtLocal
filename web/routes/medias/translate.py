"""产品级翻译入口路由。

由 web.routes.medias package 在 PR 2.11 抽出；行为不变。
拆分时同步 master 211004f2: 改用 media_product_translate service。
"""
from __future__ import annotations

from flask import abort, request
from flask_login import current_user, login_required

from appcore import medias
from web.services import media_product_translate

from . import bp
from ._helpers import _can_access_product


def _routes_module():
    from web.routes import medias as routes

    return routes


def _build_product_translation_tasks_response(pid: int, *, scope_user_id: int | None):
    return media_product_translate.build_product_translation_tasks_response(
        product_id=pid,
        scope_user_id=scope_user_id,
    )


def _build_product_translate_response(result):
    return media_product_translate.build_product_translate_response(result)


def _product_translate_flask_response(response):
    return media_product_translate.product_translate_flask_response(response)


@bp.route("/api/products/<int:pid>/translate", methods=["POST"])
@login_required
def api_product_translate(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    body = request.get_json(silent=True) or {}
    result = media_product_translate.start_product_translation(
        user_id=current_user.id,
        product_id=pid,
        product=p or {},
        user_name=getattr(current_user, "username", "") or "",
        body=body,
        ip=request.remote_addr or "",
        user_agent=request.headers.get("User-Agent", "") or "",
    )
    routes = _routes_module()
    response = routes._build_product_translate_response(result)
    return routes._product_translate_flask_response(response)


@bp.route("/api/products/<int:pid>/translation-tasks", methods=["GET"])
@login_required
def api_product_translation_tasks(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    scope_user_id = None if _routes_module()._is_admin() else current_user.id
    routes = _routes_module()
    result = routes._build_product_translation_tasks_response(
        pid,
        scope_user_id=scope_user_id,
    )

    return routes._product_translate_flask_response(result)
