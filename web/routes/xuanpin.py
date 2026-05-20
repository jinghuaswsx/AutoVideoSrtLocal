from __future__ import annotations

from flask import Blueprint, abort, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from appcore.tabcut_selection.categories import goods_category_options


bp = Blueprint("xuanpin", __name__, url_prefix="/xuanpin")


def _is_admin() -> bool:
    return getattr(current_user, "role", "") in ("admin", "superadmin") or bool(
        getattr(current_user, "is_admin", False)
    )


def _has_permission(code: str) -> bool:
    checker = getattr(current_user, "has_permission", None)
    if checker is None:
        return False
    return bool(checker(code))


def _can_access_meta_hot_posts() -> bool:
    return _is_admin() or _has_permission("meta_hot_posts")


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


def _meta_hot_posts():
    from appcore.meta_hot_posts import service

    return service


def _mingkong_materials():
    from appcore import mingkong_materials as service

    return service


@bp.route("/", methods=["GET"])
@login_required
def index():
    if _is_admin():
        return redirect(url_for("xuanpin.mk_selection_page"))
    if _can_access_meta_hot_posts():
        return redirect(url_for("xuanpin.meta_hot_posts_page"))
    abort(403)


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


@bp.route("/meta-hot-posts", methods=["GET"])
@login_required
def meta_hot_posts_page():
    if not _can_access_meta_hot_posts():
        abort(403)
    return render_template(
        "meta_hot_posts.html",
        meta_hot_post_categories=_meta_hot_posts().category_options(),
        meta_hot_posts_ai_visibility=_meta_hot_posts().ai_analysis_visibility_for_user(
            getattr(current_user, "id", None)
        ),
    )


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


@bp.route("/api/mk-selection/snapshots", methods=["GET"])
@login_required
def api_mk_selection_snapshots():
    return _medias_routes().api_mk_selection_snapshots()


@bp.route("/api/mk-selection/refresh", methods=["POST"])
@login_required
def api_mk_selection_refresh():
    return _medias_routes().api_mk_selection_refresh()


@bp.route("/api/mk-video-materials", methods=["GET"])
@login_required
def api_mk_video_materials():
    return _medias_routes().api_mk_video_materials()


@bp.route("/api/mk-material-library", methods=["GET"])
@login_required
def api_mk_material_library():
    if not _is_admin():
        return jsonify({"error": "forbidden"}), 403
    result = _mingkong_materials().list_material_library(
        snapshot_date=(request.args.get("snapshot") or "").strip() or None,
        snapshot_at=(request.args.get("snapshot_at") or "").strip() or None,
        range_key=(request.args.get("range") or "").strip() or None,
        keyword=(request.args.get("keyword") or "").strip(),
        page=request.args.get("page") or 1,
        page_size=request.args.get("page_size") or 100,
    )
    return jsonify(result)


@bp.route("/api/mk-yesterday-top100", methods=["GET"])
@login_required
def api_mk_yesterday_top100():
    if not _is_admin():
        return jsonify({"error": "forbidden"}), 403
    result = _mingkong_materials().list_yesterday_top100(
        snapshot_date=(request.args.get("snapshot") or "").strip() or None,
        snapshot_at=(request.args.get("snapshot_at") or "").strip() or None,
        page=request.args.get("page") or 1,
        page_size=request.args.get("page_size") or 100,
    )
    return jsonify(result)


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


@bp.route("/api/tabcut/videos/<path:video_id>/mark", methods=["POST"])
@login_required
def api_tabcut_video_mark(video_id: str):
    return _tabcut_routes().api_tabcut_selection_video_mark(video_id)


@bp.route("/api/tabcut/goods/<path:item_id>/mark", methods=["POST"])
@login_required
def api_tabcut_goods_mark(item_id: str):
    return _tabcut_routes().api_tabcut_selection_goods_mark(item_id)


@bp.route("/api/meta-hot-posts", methods=["GET"])
@login_required
def api_meta_hot_posts():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().build_list_response(
        request.args,
        user_id=getattr(current_user, "id", None),
    )
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/categories", methods=["GET"])
@login_required
def api_meta_hot_posts_categories():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().build_category_options_response()
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/products", methods=["GET"])
@login_required
def api_meta_hot_posts_products():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().build_product_list_response(request.args)
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/category-prompt", methods=["GET"])
@login_required
def api_meta_hot_posts_category_prompt():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().build_category_prompt_response()
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/failures", methods=["GET"])
@login_required
def api_meta_hot_posts_failures():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().build_failures_response(request.args)
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/europe-top", methods=["GET"])
@login_required
def api_meta_hot_posts_europe_top():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().build_europe_top_response(
        request.args,
        user_id=getattr(current_user, "id", None),
    )
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/today-new", methods=["GET"])
@login_required
def api_meta_hot_posts_today_new():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().build_today_new_response(
        request.args,
        user_id=getattr(current_user, "id", None),
    )
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/favorites", methods=["GET"])
@login_required
def api_meta_hot_posts_favorites():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().build_favorites_response(
        request.args,
        user_id=getattr(current_user, "id", None),
    )
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/<int:post_id>/mark", methods=["POST"])
@login_required
def api_meta_hot_posts_mark(post_id: int):
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    result = _meta_hot_posts().build_mark_response(
        post_id,
        payload,
        user_id=getattr(current_user, "id", None),
    )
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/<int:post_id>/favorite", methods=["POST"])
@login_required
def api_meta_hot_posts_favorite(post_id: int):
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    result = _meta_hot_posts().build_favorite_response(
        post_id,
        payload,
        user_id=getattr(current_user, "id", None),
    )
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/ai-analysis-visibility", methods=["GET", "POST"])
@login_required
def api_meta_hot_posts_ai_analysis_visibility():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        result = _meta_hot_posts().build_ai_analysis_visibility_update_response(
            payload,
            user_id=getattr(current_user, "id", None),
        )
    else:
        result = _meta_hot_posts().build_ai_analysis_visibility_response(
            getattr(current_user, "id", None),
        )
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/refresh", methods=["POST"])
@login_required
def api_meta_hot_posts_refresh():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().build_refresh_response()
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/analyze", methods=["POST"])
@login_required
def api_meta_hot_posts_analyze():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    payload["user_id"] = getattr(current_user, "id", None)
    result = _meta_hot_posts().build_analyze_response(payload)
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/translate-messages", methods=["POST"])
@login_required
def api_meta_hot_posts_translate_messages():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    payload["user_id"] = getattr(current_user, "id", None)
    result = _meta_hot_posts().build_translate_response(payload)
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/localize-videos", methods=["POST"])
@login_required
def api_meta_hot_posts_localize_videos():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    result = _meta_hot_posts().build_localize_videos_response(payload)
    return jsonify(result.payload), result.status_code

@bp.route("/api/meta-hot-posts/europe-fit", methods=["POST"])
@login_required
def api_meta_hot_posts_europe_fit():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    payload["user_id"] = getattr(current_user, "id", None)
    result = _meta_hot_posts().build_europe_fit_response(payload)
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/analyze-videos", methods=["POST"])
@login_required
def api_meta_hot_posts_analyze_videos():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    payload["user_id"] = getattr(current_user, "id", None)
    result = _meta_hot_posts().build_video_copyability_response(payload)
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/video-copyability/top50", methods=["GET"])
@login_required
def api_meta_hot_posts_video_copyability_top50():
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().build_video_copyability_top50_response(
        request.args,
        user_id=getattr(current_user, "id", None),
    )
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/<int:post_id>/ai-analysis/<mode>/request-preview", methods=["GET"])
@login_required
def api_meta_hot_posts_ai_analysis_request_preview(post_id: int, mode: str):
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().build_ai_analysis_request_preview_response(post_id, mode)
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/<int:post_id>/ai-analysis/<mode>/request-payload", methods=["GET"])
@login_required
def api_meta_hot_posts_ai_analysis_request_payload(post_id: int, mode: str):
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().build_ai_analysis_request_payload_response(post_id, mode)
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/<int:post_id>/ai-analysis/<mode>/result", methods=["GET"])
@login_required
def api_meta_hot_posts_ai_analysis_result(post_id: int, mode: str):
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().build_ai_analysis_result_response(post_id, mode)
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/<int:post_id>/ai-analysis/<mode>/translate-zh", methods=["POST"])
@login_required
def api_meta_hot_posts_ai_analysis_translate_zh(post_id: int, mode: str):
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().build_ai_analysis_translate_zh_response(
        post_id,
        mode,
        user_id=getattr(current_user, "id", None),
    )
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/<int:post_id>/product-title/translate-zh", methods=["POST"])
@login_required
def api_meta_hot_posts_product_title_translate_zh(post_id: int):
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().build_product_title_translate_zh_response(
        post_id,
        user_id=getattr(current_user, "id", None),
    )
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/<int:post_id>/ai-analysis/<mode>", methods=["POST"])
@login_required
def api_meta_hot_posts_ai_analysis_run(post_id: int, mode: str):
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    result = _meta_hot_posts().build_ai_analysis_run_response(
        post_id,
        mode,
        payload,
        user_id=getattr(current_user, "id", None),
    )
    return jsonify(result.payload), result.status_code


@bp.route("/api/meta-hot-posts/<int:post_id>/local-video", methods=["GET"])
@login_required
def api_meta_hot_posts_local_video(post_id: int):
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().resolve_local_video_response(post_id)
    if result.path is None:
        return jsonify({"error": result.error or "not_found"}), result.status_code
    return send_file(str(result.path), mimetype="video/mp4", conditional=True)


@bp.route("/api/meta-hot-posts/<int:post_id>/local-video-cover", methods=["GET"])
@login_required
def api_meta_hot_posts_local_video_cover(post_id: int):
    if not _can_access_meta_hot_posts():
        return jsonify({"error": "forbidden"}), 403
    result = _meta_hot_posts().resolve_local_video_cover_response(post_id)
    if result.path is None:
        return jsonify({"error": result.error or "not_found"}), result.status_code
    return send_file(str(result.path), mimetype="image/jpeg", conditional=True)


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
