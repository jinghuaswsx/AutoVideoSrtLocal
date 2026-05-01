from __future__ import annotations

from flask import abort, jsonify, request
from flask_login import login_required

from appcore import medias
from . import bp


def _routes_module():
    from web.routes import medias as routes

    return routes


def _product_links_push_error_response(exc: Exception):
    routes = _routes_module()
    app_pushes = routes.pushes
    message = str(exc)
    if isinstance(exc, app_pushes.ProductNotListedError):
        return jsonify({"error": "product_not_listed", "message": "产品已下架，不能推送投放链接"}), 409
    if isinstance(exc, app_pushes.ProductLinksPushConfigError):
        return jsonify({"error": message or "push_product_links_config_missing"}), 500
    if isinstance(exc, app_pushes.ProductLinksPayloadError):
        status = 400
        return jsonify({"error": message or "product_links_payload_invalid"}), status
    return jsonify({"error": "product_links_push_failed", "message": message}), 500


def _product_localized_texts_push_error_response(exc: Exception):
    routes = _routes_module()
    app_pushes = routes.pushes
    message = str(exc)
    if isinstance(exc, app_pushes.ProductNotListedError):
        return jsonify({"error": "product_not_listed", "message": "产品已下架，不能推送小语种文案"}), 409
    if isinstance(exc, app_pushes.ProductLocalizedTextsPushConfigError):
        return jsonify({"error": message or "push_localized_texts_config_missing"}), 500
    if isinstance(exc, app_pushes.ProductLocalizedTextsPayloadError):
        return jsonify({"error": message or "localized_texts_payload_invalid"}), 400
    return jsonify({"error": "product_localized_texts_push_failed", "message": message}), 500


def _product_unsuitable_push_error_response(exc: Exception):
    routes = _routes_module()
    app_pushes = routes.pushes
    message = str(exc)
    if isinstance(exc, app_pushes.ProductNotListedError):
        return jsonify({"error": "product_not_listed", "message": "产品已下架，不能推送不合适标注"}), 409
    if isinstance(exc, app_pushes.ProductLocalizedTextsPushConfigError):
        return jsonify({"error": message or "push_localized_texts_config_missing"}), 500
    if isinstance(exc, app_pushes.ProductLinksPushConfigError):
        return jsonify({"error": message or "push_product_links_config_missing"}), 500
    if isinstance(exc, app_pushes.ProductLocalizedTextsPayloadError):
        return jsonify({"error": message or "localized_texts_payload_invalid"}), 400
    if isinstance(exc, app_pushes.ProductLinksPayloadError):
        return jsonify({"error": message or "product_links_payload_invalid"}), 400
    return jsonify({"error": "product_unsuitable_push_failed", "message": message}), 500


@bp.route("/api/products/<int:pid>/product-links-push/payload", methods=["GET"])
@login_required
def api_product_links_push_payload(pid: int):
    routes = _routes_module()
    if not routes._is_admin():
        return jsonify({"error": "仅管理员可操作"}), 403
    product = medias.get_product(pid)
    if not routes._can_access_product(product):
        abort(404)
    try:
        return jsonify(routes.pushes.build_product_links_push_preview(product))
    except Exception as exc:
        return _product_links_push_error_response(exc)


@bp.route("/api/products/<int:pid>/product-links-push", methods=["POST"])
@login_required
def api_product_links_push(pid: int):
    routes = _routes_module()
    if not routes._is_admin():
        return jsonify({"error": "仅管理员可操作"}), 403
    product = medias.get_product(pid)
    if not routes._can_access_product(product):
        abort(404)
    try:
        result = routes.pushes.push_product_links(product)
    except Exception as exc:
        return _product_links_push_error_response(exc)
    status = 200 if result.get("ok") else 502
    return jsonify(result), status


@bp.route("/api/products/<int:pid>/product-unsuitable-push/payload", methods=["GET"])
@login_required
def api_product_unsuitable_push_payload(pid: int):
    routes = _routes_module()
    if not routes._is_admin():
        return jsonify({"error": "仅管理员可操作"}), 403
    product = medias.get_product(pid)
    if not routes._can_access_product(product):
        abort(404)
    try:
        return jsonify(routes.pushes.build_unsuitable_product_push_preview(product))
    except Exception as exc:
        return _product_unsuitable_push_error_response(exc)


@bp.route("/api/products/<int:pid>/product-unsuitable-push", methods=["POST"])
@login_required
def api_product_unsuitable_push(pid: int):
    routes = _routes_module()
    if not routes._is_admin():
        return jsonify({"error": "仅管理员可操作"}), 403
    product = medias.get_product(pid)
    if not routes._can_access_product(product):
        abort(404)
    body = request.get_json(silent=True) or {}
    raw_type = (body.get("type") or "").strip().lower() if isinstance(body, dict) else ""
    only_type = raw_type if raw_type in {"copy", "links"} else None
    try:
        if only_type:
            result = routes.pushes.push_unsuitable_product(product, only_type=only_type)
        else:
            result = routes.pushes.push_unsuitable_product(product)
    except Exception as exc:
        return _product_unsuitable_push_error_response(exc)
    status = 200 if result.get("ok") else 502
    return jsonify(result), status


@bp.route("/api/products/<int:pid>/product-localized-texts-push/payload", methods=["GET"])
@login_required
def api_product_localized_texts_push_payload(pid: int):
    routes = _routes_module()
    if not routes._is_admin():
        return jsonify({"error": "仅管理员可操作"}), 403
    product = medias.get_product(pid)
    if not routes._can_access_product(product):
        abort(404)
    try:
        return jsonify(routes.pushes.build_product_localized_texts_push_preview(product))
    except Exception as exc:
        return _product_localized_texts_push_error_response(exc)


@bp.route("/api/products/<int:pid>/product-localized-texts-push", methods=["POST"])
@login_required
def api_product_localized_texts_push(pid: int):
    routes = _routes_module()
    if not routes._is_admin():
        return jsonify({"error": "仅管理员可操作"}), 403
    product = medias.get_product(pid)
    if not routes._can_access_product(product):
        abort(404)
    try:
        result = routes.pushes.push_product_localized_texts(product)
    except Exception as exc:
        return _product_localized_texts_push_error_response(exc)
    status = 200 if result.get("ok") else 502
    return jsonify(result), status
