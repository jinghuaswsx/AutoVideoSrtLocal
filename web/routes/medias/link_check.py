from __future__ import annotations

from flask import abort, request, jsonify
from flask_login import current_user, login_required

from appcore import medias
from web.services.media_link_check import (
    build_product_link_availability_get_response,
    build_product_link_availability_run_response,
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
        domain=request.args.get("domain"),
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
        domain=request.args.get("domain"),
    )
    return routes._media_link_check_flask_response(result)


@bp.route("/api/products/<int:pid>/link-availability/<lang>", methods=["GET"])
@login_required
def api_product_link_availability_get(pid: int, lang: str):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    result = build_product_link_availability_get_response(product=p, lang=lang)
    return routes._media_link_check_flask_response(result)


@bp.route("/api/products/<int:pid>/link-availability/<lang>", methods=["POST"])
@login_required
def api_product_link_availability_run(pid: int, lang: str):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    result = build_product_link_availability_run_response(
        product=p,
        lang=lang,
        body=body,
    )
    return routes._media_link_check_flask_response(result)


@bp.route("/api/products/probe-link", methods=["POST"])
@login_required
def api_product_probe_link():
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return jsonify({"ok": False, "error": "请输入有效的链接"}), 400

    from appcore.link_availability import probe
    try:
        res = probe(url)
        return jsonify({
            "ok": res.get("ok", False),
            "http_status": res.get("http_status"),
            "error": res.get("error"),
            "elapsed_ms": res.get("elapsed_ms")
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/products/list-for-link-check", methods=["GET"])
@login_required
def api_list_for_link_check():
    created_from = request.args.get("created_from", "").strip()
    created_to = request.args.get("created_to", "").strip()

    if not created_from or not created_to:
        return jsonify({"error": "created_from and created_to are required"}), 400

    from appcore.db import query
    from appcore.product_link_domains import resolve_product_page_url_rows

    sql = """
        SELECT id, product_code, name, localized_links_json
        FROM media_products
        WHERE deleted_at IS NULL AND archived = 0
          AND created_at >= %s AND created_at <= %s
        ORDER BY created_at DESC
    """
    params = (f"{created_from} 00:00:00", f"{created_to} 23:59:59")
    try:
        rows = query(sql, params)
    except Exception as e:
        return jsonify({"error": f"Database query failed: {str(e)}"}), 500

    results = []
    for r in rows:
        product_dict = dict(r)
        url_rows = resolve_product_page_url_rows(product_dict, "en")
        if url_rows:
            results.append({
                "id": product_dict["id"],
                "product_code": product_dict["product_code"],
                "name": product_dict["name"],
                "urls": url_rows
            })

    return jsonify({"products": results}), 200

