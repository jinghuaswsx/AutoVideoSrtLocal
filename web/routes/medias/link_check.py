from __future__ import annotations

from flask import abort, request
from flask_login import current_user, login_required

from appcore import medias
from web.services.media_link_check import (
    build_product_link_check_create_response,
    build_product_link_check_detail_response,
    build_product_link_check_summary_response,
    media_link_check_flask_response as _media_link_check_flask_response_impl,
)

from . import bp


def _routes_module():
    from web.routes import medias as routes

    return routes


def _start_link_check_task(task_id: str):
    return _routes_module().link_check_runner.start(task_id)


def _media_link_check_flask_response(result):
    return _media_link_check_flask_response_impl(result)


@bp.route("/api/products/<int:pid>/link-check", methods=["POST"])
@login_required
def api_product_link_check_create(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)

    body = request.get_json(silent=True) or {}
    result = build_product_link_check_create_response(
        product_id=pid,
        body=body,
        user_id=current_user.id,
        output_dir=routes.OUTPUT_DIR,
        store_obj=routes.store,
        start_runner_fn=_start_link_check_task,
        download_media_object_fn=routes._download_media_object,
    )
    return routes._media_link_check_flask_response(result)


@bp.route("/api/products/<int:pid>/link-check/<lang>", methods=["GET"])
@login_required
def api_product_link_check_get(pid: int, lang: str):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    result = build_product_link_check_summary_response(
        product=p,
        lang=lang,
        user_id=current_user.id,
        store_obj=routes.store,
    )
    return routes._media_link_check_flask_response(result)


@bp.route("/api/products/<int:pid>/link-check/<lang>/detail", methods=["GET"])
@login_required
def api_product_link_check_detail(pid: int, lang: str):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    result = build_product_link_check_detail_response(
        product=p,
        lang=lang,
        user_id=current_user.id,
        store_obj=routes.store,
        serialize_task_fn=routes._serialize_link_check_task,
    )
    return routes._media_link_check_flask_response(result)
