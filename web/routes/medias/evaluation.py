"""AI 评估路由。

由 ``web.routes.medias`` package 在 PR 2.9 抽出；行为不变。
"""
from __future__ import annotations

from flask import abort
from flask_login import login_required

from appcore import material_evaluation, medias

from . import bp
from ._helpers import _can_access_product
from web.services.media_evaluation import (
    build_product_evaluation_payload_response as _build_product_evaluation_payload_response_impl,
    build_product_evaluation_preview_response as _build_product_evaluation_preview_response_impl,
    build_product_evaluation_response as _build_product_evaluation_response_impl,
    media_evaluation_flask_response as _media_evaluation_flask_response_impl,
)


def _routes_module():
    from web.routes import medias as routes

    return routes


def _build_product_evaluation_response(pid: int):
    return _build_product_evaluation_response_impl(
        pid,
        evaluate_product_fn=material_evaluation.evaluate_product_if_ready,
        material_evaluation_message_fn=_routes_module()._material_evaluation_message,
    )


def _build_product_evaluation_preview_response(pid: int):
    return _build_product_evaluation_preview_response_impl(
        pid,
        build_request_debug_payload_fn=material_evaluation.build_request_debug_payload,
    )


def _build_product_evaluation_payload_response(pid: int):
    return _build_product_evaluation_payload_response_impl(
        pid,
        build_request_debug_payload_fn=material_evaluation.build_request_debug_payload,
    )


def _media_evaluation_flask_response(result):
    return _media_evaluation_flask_response_impl(result)


@bp.route("/api/products/<int:pid>/evaluate", methods=["POST"])
@login_required
def api_product_evaluate(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    routes = _routes_module()
    result = routes._build_product_evaluation_response(pid)
    return routes._media_evaluation_flask_response(result)


@bp.route("/api/products/<int:pid>/evaluate/request-preview", methods=["GET"])
@login_required
def api_product_evaluate_request_preview(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    routes = _routes_module()
    result = routes._build_product_evaluation_preview_response(pid)
    return routes._media_evaluation_flask_response(result)


@bp.route("/api/products/<int:pid>/evaluate/request-payload", methods=["GET"])
@login_required
def api_product_evaluate_request_payload(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    routes = _routes_module()
    result = routes._build_product_evaluation_payload_response(pid)
    return routes._media_evaluation_flask_response(result)
