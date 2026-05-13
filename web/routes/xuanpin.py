from __future__ import annotations

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from appcore.tabcut_selection.categories import goods_category_options


bp = Blueprint("xuanpin", __name__, url_prefix="/xuanpin")


def _is_admin() -> bool:
    return getattr(current_user, "role", "") in ("admin", "superadmin") or bool(
        getattr(current_user, "is_admin", False)
    )


def _medias_routes():
    from web.routes import medias as routes

    return routes


def _tabcut_routes():
    from web.routes.medias import tabcut_selection as routes

    return routes


def _new_product_routes():
    from web.routes import new_product_review as routes

    return routes


def _today_recommendations():
    from appcore import today_recommendations as service

    return service


@bp.route("/", methods=["GET"])
@login_required
def index():
    return redirect(url_for("xuanpin.mk_selection_page"))


@bp.route("/mk", methods=["GET"])
@login_required
def mk_selection_page():
    if not _is_admin():
        abort(403)
    return render_template("mk_selection.html")


@bp.route("/tabcut", methods=["GET"])
@login_required
def tabcut_selection_page():
    if not _is_admin():
        abort(403)
    return render_template("tabcut_selection.html", tabcut_goods_categories=goods_category_options())


@bp.route("/new-products", methods=["GET"])
@login_required
def new_products_page():
    return _new_product_routes()._render_index_page()


@bp.route("/today-recommendations", methods=["GET"])
@login_required
def today_recommendations_page():
    if not _is_admin():
        abort(403)
    from appcore.users import list_translators

    service = _today_recommendations()
    return render_template(
        "today_recommendations.html",
        recommendations=service.list_recommendations(limit=100),
        run_summary=service.latest_run_summary(),
        translators=list_translators(),
    )


@bp.route("/api/mk-selection", methods=["GET"])
@login_required
def api_mk_selection():
    return _medias_routes().api_mk_selection()


@bp.route("/api/mk-selection/refresh", methods=["POST"])
@login_required
def api_mk_selection_refresh():
    return _medias_routes().api_mk_selection_refresh()


@bp.route("/api/mk-media", methods=["GET"])
@login_required
def api_mk_media_proxy():
    return _medias_routes().api_mk_media_proxy()


@bp.route("/api/mk-video", methods=["GET"])
@login_required
def api_mk_video_proxy():
    return _medias_routes().api_mk_video_proxy()


@bp.route("/api/mk-detail/<int:mk_id>", methods=["GET"])
@login_required
def api_mk_detail_proxy(mk_id: int):
    return _medias_routes().api_mk_detail_proxy(mk_id)


@bp.route("/api/tabcut/videos", methods=["GET"])
@login_required
def api_tabcut_videos():
    return _tabcut_routes().api_tabcut_selection_videos()


@bp.route("/api/tabcut/goods", methods=["GET"])
@login_required
def api_tabcut_goods():
    return _tabcut_routes().api_tabcut_selection_goods()


@bp.route("/api/tabcut/categories", methods=["GET"])
@login_required
def api_tabcut_categories():
    return _tabcut_routes().api_tabcut_selection_categories()


@bp.route("/api/tabcut/refresh", methods=["POST"])
@login_required
def api_tabcut_refresh():
    return _tabcut_routes().api_tabcut_selection_refresh()


@bp.route("/api/new-products/list", methods=["GET"])
@login_required
def api_new_products_list():
    return _new_product_routes().api_list()


@bp.route("/api/new-products/<int:product_id>/evaluate", methods=["POST"])
@login_required
def api_new_products_evaluate(product_id: int):
    return _new_product_routes().api_evaluate(product_id)


@bp.route("/api/new-products/<int:product_id>/decide", methods=["POST"])
@login_required
def api_new_products_decide(product_id: int):
    return _new_product_routes().api_decide(product_id)


@bp.route("/api/new-products/<int:product_id>/reject", methods=["POST"])
@login_required
def api_new_products_reject(product_id: int):
    return _new_product_routes().api_reject(product_id)


@bp.route("/api/today-recommendations/list", methods=["GET"])
@login_required
def api_today_recommendations_list():
    if not _is_admin():
        return jsonify({"error": "forbidden"}), 403
    include_adopted = (request.args.get("include_adopted") or "").strip() in {"1", "true", "yes"}
    recommendation_date = (request.args.get("date") or "").strip() or None
    service = _today_recommendations()
    return jsonify({
        "items": service.list_recommendations(
            recommendation_date=recommendation_date,
            include_adopted=include_adopted,
            limit=200 if include_adopted else 100,
        ),
        "run_summary": service.latest_run_summary(),
    })


@bp.route("/api/today-recommendations/adopt", methods=["POST"])
@login_required
def api_today_recommendations_adopt():
    if not _is_admin():
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        recommendation_ids = [int(item) for item in payload.get("recommendation_ids") or []]
        translator_id = int(payload.get("translator_id") or 0)
    except (TypeError, ValueError) as exc:
        return jsonify({"error": f"invalid payload: {exc}"}), 400
    try:
        result = _today_recommendations().adopt_recommendations(
            recommendation_ids=recommendation_ids,
            translator_id=translator_id,
            actor_user_id=int(current_user.id),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)
