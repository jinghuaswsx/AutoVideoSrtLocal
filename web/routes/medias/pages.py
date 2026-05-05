from __future__ import annotations

from flask import abort, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from appcore import medias
from web.services.media_pages import (
    build_admin_required_response,
    build_active_users_response,
    build_languages_response,
    build_medias_page_context,
)
from . import bp


def _routes_module():
    from web.routes import medias as routes

    return routes


def _medias_page_context(**extra):
    return build_medias_page_context(request.args, extra)


@bp.route("/")
@login_required
def index():
    return render_template("medias_list.html", **_medias_page_context())


@bp.route("/<product_code>")
@login_required
def product_detail_page(product_code: str):
    code = (product_code or "").strip().lower()
    if not code:
        abort(404)
    if code != product_code:
        return redirect(url_for("medias.product_detail_page", product_code=code))
    product = medias.get_product_by_code(code)
    if not _routes_module()._can_access_product(product):
        abort(404)
    return render_template(
        "medias_list.html",
        **_medias_page_context(
            medias_product_detail=True,
            medias_product_id=int(product["id"]),
            medias_product_code=code,
            medias_product_name=product.get("name") or code,
            initial_query=code,
        ),
    )


@bp.route("/products/<int:pid>/translation-tasks", methods=["GET"])
@login_required
def translation_tasks_page(pid: int):
    product = medias.get_product(pid)
    if not _routes_module()._can_access_product(product):
        abort(404)
    return render_template(
        "medias_translation_tasks.html",
        product=product,
        product_id=pid,
    )


@bp.route("/api/users/active", methods=["GET"])
@login_required
def api_list_active_users():
    if not _routes_module()._is_admin():
        return jsonify(build_admin_required_response()), 403
    return jsonify(build_active_users_response())


@bp.route("/api/languages", methods=["GET"])
@login_required
def api_list_languages():
    return jsonify(build_languages_response())


@bp.route("/mk-selection")
@login_required
def mk_selection_page():
    if not _routes_module()._is_admin():
        abort(403)
    return render_template("mk_selection.html")
