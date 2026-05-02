from __future__ import annotations

from flask import abort, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from appcore import medias, product_roas, shopify_image_localizer_release
from . import bp


def _routes_module():
    from web.routes import medias as routes

    return routes


def _medias_page_context(**extra):
    roas_rmb_per_usd = product_roas.get_configured_rmb_per_usd()
    initial_query = (
        request.args.get("q")
        or request.args.get("keyword")
        or extra.get("initial_query")
        or ""
    )
    return {
        "shopify_image_localizer_release": shopify_image_localizer_release.get_release_info(),
        "material_roas_rmb_per_usd": float(roas_rmb_per_usd),
        "medias_initial_query": str(initial_query).strip(),
        **extra,
    }


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
        return jsonify({"error": "仅管理员可访问"}), 403
    return jsonify({"users": medias.list_active_users()})


@bp.route("/api/languages", methods=["GET"])
@login_required
def api_list_languages():
    return jsonify({"items": medias.list_languages()})


@bp.route("/mk-selection")
@login_required
def mk_selection_page():
    if not _routes_module()._is_admin():
        abort(403)
    return render_template("mk_selection.html")
