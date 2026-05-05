from __future__ import annotations

from flask import abort, jsonify, request
from flask_login import current_user, login_required

from appcore import medias
from web.services.media_shopify_image import (
    build_shopify_image_clear_response,
    build_shopify_image_confirm_response,
    build_shopify_image_requeue_response,
    build_shopify_image_unavailable_response,
    normalize_shopify_image_lang,
)

from . import bp


def _routes_module():
    from web.routes import medias as routes

    return routes


def _shopify_image_lang_or_404(pid: int, lang: str) -> tuple[dict, str]:
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    normalized_lang = normalize_shopify_image_lang(lang)
    if not normalized_lang:
        abort(404)
    return p, normalized_lang


@bp.route("/api/products/<int:pid>/shopify-image/<lang>/confirm", methods=["POST"])
@login_required
def api_product_shopify_image_confirm(pid: int, lang: str):
    _p, normalized_lang = _shopify_image_lang_or_404(pid, lang)
    result = build_shopify_image_confirm_response(
        product_id=pid,
        lang=normalized_lang,
        user_id=getattr(current_user, "id", None),
    )
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/shopify-image/<lang>/unavailable", methods=["POST"])
@login_required
def api_product_shopify_image_unavailable(pid: int, lang: str):
    _p, normalized_lang = _shopify_image_lang_or_404(pid, lang)
    body = request.get_json(silent=True) or {}
    result = build_shopify_image_unavailable_response(
        product_id=pid,
        lang=normalized_lang,
        body=body,
    )
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/shopify-image/<lang>/clear", methods=["POST"])
@login_required
def api_product_shopify_image_clear(pid: int, lang: str):
    _p, normalized_lang = _shopify_image_lang_or_404(pid, lang)
    result = build_shopify_image_clear_response(product_id=pid, lang=normalized_lang)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/shopify-image/<lang>/requeue", methods=["POST"])
@login_required
def api_product_shopify_image_requeue(pid: int, lang: str):
    _p, normalized_lang = _shopify_image_lang_or_404(pid, lang)
    result = build_shopify_image_requeue_response(product_id=pid, lang=normalized_lang)
    return jsonify(result.payload), result.status_code
