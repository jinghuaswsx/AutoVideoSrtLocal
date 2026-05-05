from __future__ import annotations

from flask import abort, jsonify, request
from flask_login import login_required

from appcore import medias
from web.services.media_pushes import (
    build_product_push_admin_required_response,
    build_product_links_push_preview_response,
    build_product_links_push_error_response,
    build_product_links_push_response,
    build_product_localized_texts_push_preview_response,
    build_product_localized_texts_push_error_response,
    build_product_localized_texts_push_response,
    build_product_unsuitable_push_preview_response,
    build_product_unsuitable_push_error_response,
    build_product_unsuitable_push_response,
)

from . import bp


def _routes_module():
    from web.routes import medias as routes

    return routes


def _product_links_push_error_response(exc: Exception):
    result = build_product_links_push_error_response(exc)
    return jsonify(result.payload), result.status_code


def _product_localized_texts_push_error_response(exc: Exception):
    result = build_product_localized_texts_push_error_response(exc)
    return jsonify(result.payload), result.status_code


def _product_unsuitable_push_error_response(exc: Exception):
    result = build_product_unsuitable_push_error_response(exc)
    return jsonify(result.payload), result.status_code


def _product_push_admin_required_response():
    result = build_product_push_admin_required_response()
    return jsonify(result.payload), result.status_code


def _build_product_links_push_preview_response(product: dict):
    return build_product_links_push_preview_response(
        product,
        build_preview_fn=_routes_module().pushes.build_product_links_push_preview,
    )


def _build_product_links_push_response(product: dict):
    return build_product_links_push_response(
        product,
        push_product_links_fn=_routes_module().pushes.push_product_links,
    )


def _build_product_unsuitable_push_preview_response(product: dict):
    return build_product_unsuitable_push_preview_response(
        product,
        build_preview_fn=_routes_module().pushes.build_unsuitable_product_push_preview,
    )


def _build_product_unsuitable_push_response(product: dict, body: dict | None):
    return build_product_unsuitable_push_response(
        product,
        body,
        push_unsuitable_product_fn=_routes_module().pushes.push_unsuitable_product,
    )


def _build_product_localized_texts_push_preview_response(product: dict):
    return build_product_localized_texts_push_preview_response(
        product,
        build_preview_fn=_routes_module().pushes.build_product_localized_texts_push_preview,
    )


def _build_product_localized_texts_push_response(product: dict):
    return build_product_localized_texts_push_response(
        product,
        push_localized_texts_fn=_routes_module().pushes.push_product_localized_texts,
    )


@bp.route("/api/products/<int:pid>/product-links-push/payload", methods=["GET"])
@login_required
def api_product_links_push_payload(pid: int):
    routes = _routes_module()
    if not routes._is_admin():
        return routes._product_push_admin_required_response()
    product = medias.get_product(pid)
    if not routes._can_access_product(product):
        abort(404)
    result = routes._build_product_links_push_preview_response(product)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/product-links-push", methods=["POST"])
@login_required
def api_product_links_push(pid: int):
    routes = _routes_module()
    if not routes._is_admin():
        return routes._product_push_admin_required_response()
    product = medias.get_product(pid)
    if not routes._can_access_product(product):
        abort(404)
    result = routes._build_product_links_push_response(product)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/product-unsuitable-push/payload", methods=["GET"])
@login_required
def api_product_unsuitable_push_payload(pid: int):
    routes = _routes_module()
    if not routes._is_admin():
        return routes._product_push_admin_required_response()
    product = medias.get_product(pid)
    if not routes._can_access_product(product):
        abort(404)
    result = routes._build_product_unsuitable_push_preview_response(product)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/product-unsuitable-push", methods=["POST"])
@login_required
def api_product_unsuitable_push(pid: int):
    routes = _routes_module()
    if not routes._is_admin():
        return routes._product_push_admin_required_response()
    product = medias.get_product(pid)
    if not routes._can_access_product(product):
        abort(404)
    body = request.get_json(silent=True) or {}
    result = routes._build_product_unsuitable_push_response(product, body)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/product-localized-texts-push/payload", methods=["GET"])
@login_required
def api_product_localized_texts_push_payload(pid: int):
    routes = _routes_module()
    if not routes._is_admin():
        return routes._product_push_admin_required_response()
    product = medias.get_product(pid)
    if not routes._can_access_product(product):
        abort(404)
    result = routes._build_product_localized_texts_push_preview_response(product)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/product-localized-texts-push", methods=["POST"])
@login_required
def api_product_localized_texts_push(pid: int):
    routes = _routes_module()
    if not routes._is_admin():
        return routes._product_push_admin_required_response()
    product = medias.get_product(pid)
    if not routes._can_access_product(product):
        abort(404)
    result = routes._build_product_localized_texts_push_response(product)
    return jsonify(result.payload), result.status_code
