from __future__ import annotations

from flask import abort, jsonify, request
from flask_login import current_user, login_required

from appcore import medias
from . import bp


def _routes_module():
    from web.routes import medias as routes

    return routes


def _shopify_image_lang_or_404(pid: int, lang: str) -> tuple[dict, str]:
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    normalized_lang = (lang or "").strip().lower()
    if not normalized_lang or normalized_lang == "en" or not medias.is_valid_language(normalized_lang):
        abort(404)
    return p, normalized_lang


@bp.route("/api/products/<int:pid>/shopify-image/<lang>/confirm", methods=["POST"])
@login_required
def api_product_shopify_image_confirm(pid: int, lang: str):
    routes = _routes_module()
    _p, normalized_lang = _shopify_image_lang_or_404(pid, lang)
    status = routes.shopify_image_tasks.confirm_lang(
        pid,
        normalized_lang,
        getattr(current_user, "id", None),
    )
    return jsonify({"ok": True, "status": status})


@bp.route("/api/products/<int:pid>/shopify-image/<lang>/unavailable", methods=["POST"])
@login_required
def api_product_shopify_image_unavailable(pid: int, lang: str):
    routes = _routes_module()
    _p, normalized_lang = _shopify_image_lang_or_404(pid, lang)
    body = request.get_json(silent=True) or {}
    status = routes.shopify_image_tasks.mark_link_unavailable(
        pid,
        normalized_lang,
        (body.get("reason") or "").strip(),
    )
    return jsonify({"ok": True, "status": status})


@bp.route("/api/products/<int:pid>/shopify-image/<lang>/clear", methods=["POST"])
@login_required
def api_product_shopify_image_clear(pid: int, lang: str):
    routes = _routes_module()
    _p, normalized_lang = _shopify_image_lang_or_404(pid, lang)
    status = routes.shopify_image_tasks.reset_lang(pid, normalized_lang)
    return jsonify({"ok": True, "status": status})


@bp.route("/api/products/<int:pid>/shopify-image/<lang>/requeue", methods=["POST"])
@login_required
def api_product_shopify_image_requeue(pid: int, lang: str):
    routes = _routes_module()
    _p, normalized_lang = _shopify_image_lang_or_404(pid, lang)
    routes.shopify_image_tasks.reset_lang(pid, normalized_lang)
    task = routes.shopify_image_tasks.create_or_reuse_task(pid, normalized_lang)
    status_code = 202 if task.get("status") != routes.shopify_image_tasks.TASK_BLOCKED else 409
    return jsonify({"ok": status_code == 202, "task": task}), status_code
