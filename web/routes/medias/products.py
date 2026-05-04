from __future__ import annotations

import requests
import pymysql.err
from flask import abort, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore import medias, product_roas, pushes
from . import bp
from ._serializers import _int_or_none, _serialize_item, _serialize_product


_ROAS_PRODUCT_FIELDS = (
    "purchase_1688_url",
    "purchase_price",
    "packet_cost_estimated",
    "packet_cost_actual",
    "package_length_cm",
    "package_width_cm",
    "package_height_cm",
    "tk_sea_cost",
    "tk_air_cost",
    "tk_sale_price",
    "standalone_price",
    "standalone_shipping_fee",
)


def _routes_module():
    from web.routes import medias as routes

    return routes


def _normalize_mk_copywriting_query(product_code: str) -> str:
    code = (product_code or "").strip().lower()
    if code.endswith("-rjc"):
        code = code[:-4]
    return code


def _mk_product_link_tail(item: dict) -> str:
    links = item.get("product_links") or []
    if not isinstance(links, list) or not links:
        return ""
    first_link = links[0]
    if not isinstance(first_link, str):
        return ""
    return first_link.rstrip("/").rsplit("/", 1)[-1].strip().lower()


def _format_mk_copywriting_text(text: dict) -> str:
    title = str(text.get("title") or "").strip()
    message = str(text.get("message") or "").strip()
    description = str(text.get("description") or "").strip()
    if not any((title, message, description)):
        return ""
    return "\n".join((
        f"标题: {title}",
        f"文案: {message}",
        f"描述: {description}",
    ))


def _extract_mk_copywriting(data: dict, product_code: str) -> tuple[int | None, str]:
    items = ((data.get("data") or {}).get("items") or [])
    if not isinstance(items, list):
        return None, ""
    for item in items:
        if not isinstance(item, dict):
            continue
        if _mk_product_link_tail(item) != product_code:
            continue
        texts = item.get("texts") or []
        if not isinstance(texts, list):
            return item.get("id"), ""
        for text in texts:
            if not isinstance(text, dict):
                continue
            copywriting = _format_mk_copywriting_text(text)
            if copywriting:
                return item.get("id"), copywriting
        return item.get("id"), ""
    return None, ""


@bp.route("/api/mk-copywriting", methods=["GET"])
@login_required
def api_mk_copywriting():
    routes = _routes_module()
    query = _normalize_mk_copywriting_query(
        request.args.get("product_code") or request.args.get("q") or ""
    )
    if not query:
        return jsonify({"error": "product_code_required", "message": "请先填写产品 ID"}), 400

    headers = routes._build_mk_request_headers()
    if "Authorization" not in headers and "Cookie" not in headers:
        return jsonify({
            "error": "mk_credentials_missing",
            "message": "明空凭据未配置，请先在设置页同步 wedev 凭据",
        }), 500

    url = f"{routes._get_mk_api_base_url()}/api/marketing/medias"
    params = {"page": 1, "q": query, "source": "", "level": "", "show_attention": 0}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
    except requests.RequestException as exc:
        return jsonify({"error": "mk_request_failed", "message": str(exc)}), 502

    if not resp.ok:
        return jsonify({
            "error": "mk_request_failed",
            "message": f"明空接口返回 HTTP {resp.status_code}",
        }), 502

    try:
        data = resp.json() or {}
    except ValueError:
        return jsonify({"error": "mk_response_invalid", "message": "明空返回数据格式异常"}), 502

    if routes._is_mk_login_expired(data):
        return jsonify({"error": "mk_credentials_expired", "message": "明空登录已失效，请重新同步 wedev 凭据"}), 401

    source_item_id, copywriting = _extract_mk_copywriting(data, query)
    if source_item_id is None:
        return jsonify({
            "error": "mk_copywriting_not_found",
            "message": f"明空系统未找到产品 ID 为 {query} 的文案",
            "query": query,
        }), 404
    if not copywriting:
        return jsonify({
            "error": "mk_copywriting_empty",
            "message": f"明空产品 {query} 没有可用文案",
            "query": query,
            "source_item_id": source_item_id,
        }), 404

    return jsonify({
        "ok": True,
        "query": query,
        "source_item_id": source_item_id,
        "copywriting": copywriting,
    })


@bp.route("/api/products", methods=["GET"])
@login_required
def api_list_products():
    keyword = (request.args.get("keyword") or "").strip()
    archived = request.args.get("archived") in ("1", "true", "yes")
    page = max(1, int(request.args.get("page") or 1))
    limit = 20
    offset = (page - 1) * limit

    rows, total = medias.list_products(None, keyword=keyword, archived=archived,
                                       offset=offset, limit=limit)
    pids = [r["id"] for r in rows]
    counts = medias.count_items_by_product(pids)
    raw_counts = medias.count_raw_sources_by_product(pids)
    thumb_covers = medias.first_thumb_item_by_product(pids)
    filenames = medias.list_item_filenames_by_product(pids, limit_per=5)
    coverage = medias.lang_coverage_by_product(pids)
    covers_map = medias.get_product_covers_batch(pids)
    roas_rmb_per_usd = product_roas.get_configured_rmb_per_usd()
    data = [
        _serialize_product(
            r, counts.get(r["id"], 0), thumb_covers.get(r["id"]),
            items_filenames=filenames.get(r["id"], []),
            lang_coverage=coverage.get(r["id"], {}),
            covers=covers_map.get(r["id"], {}),
            raw_sources_count=raw_counts.get(r["id"], 0),
            roas_rmb_per_usd=roas_rmb_per_usd,
        )
        for r in rows
    ]
    return jsonify({"items": data, "total": total, "page": page, "page_size": limit})


@bp.route("/api/products", methods=["POST"])
@login_required
def api_create_product():
    routes = _routes_module()
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    product_code = (body.get("product_code") or "").strip().lower() or None
    if product_code is not None:
        ok, err = routes._validate_product_code(product_code)
        if not ok:
            return jsonify({"error": err}), 400
        if medias.get_product_by_code(product_code):
            return jsonify({"error": "product_code already exists"}), 409
    pid = medias.create_product(
        current_user.id, name,
        product_code=product_code,
    )
    return jsonify({"id": pid}), 201


@bp.route("/api/products/<int:pid>", methods=["GET"])
@login_required
def api_get_product(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    covers = medias.get_product_covers(pid)
    items = medias.list_items(pid)
    needs_raw_sources = any(
        _int_or_none(item.get("source_raw_id"))
        or (item.get("auto_translated") and _int_or_none(item.get("source_ref_id")))
        for item in items
    )
    raw_sources_by_id = {}
    if needs_raw_sources:
        raw_sources_by_id = {
            int(row["id"]): row
            for row in medias.list_raw_sources(pid)
            if row.get("id") is not None
        }
    return jsonify({
        "product": _serialize_product(
            p,
            None,
            covers=covers,
            roas_rmb_per_usd=product_roas.get_configured_rmb_per_usd(),
        ),
        "covers": covers,
        "copywritings": medias.list_copywritings(pid),
        "items": [_serialize_item(i, raw_sources_by_id) for i in items],
    })


@bp.route("/api/products/<int:pid>", methods=["PUT"])
@login_required
def api_update_product(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}

    update_fields: dict = {}

    if "name" in body:
        name = (body.get("name") or "").strip() or p["name"]
        update_fields["name"] = name

    if "product_code" in body:
        product_code = (body.get("product_code") or "").strip().lower()
        ok, err = routes._validate_product_code(product_code)
        if not ok:
            return jsonify({"error": err}), 400
        exist = medias.get_product_by_code(product_code)
        if exist and exist["id"] != pid:
            return jsonify({"error": "product_code already exists"}), 409
        update_fields["product_code"] = product_code

    if "mk_id" in body:
        update_fields["mk_id"] = body.get("mk_id")

    if "shopifyid" in body:
        update_fields["shopifyid"] = body.get("shopifyid")

    for key in (
        "remark",
        "ai_score",
        "ai_evaluation_result",
        "ai_evaluation_detail",
        "listing_status",
    ):
        if key in body:
            update_fields[key] = body.get(key)

    for key in _ROAS_PRODUCT_FIELDS:
        if key in body:
            update_fields[key] = body.get(key)

    if isinstance(body.get("localized_links"), dict):
        cleaned = {}
        for lang, url in body["localized_links"].items():
            url = (url or "").strip()
            if url and medias.is_valid_language(lang):
                cleaned[lang] = url
        update_fields["localized_links_json"] = cleaned

    if "ad_supported_langs" in body:
        raw = body.get("ad_supported_langs") or ""
        if isinstance(raw, list):
            parts = [str(x).strip().lower() for x in raw if str(x).strip()]
        else:
            parts = [p.strip().lower() for p in str(raw).split(",") if p.strip()]
        seen: set[str] = set()
        kept: list[str] = []
        for code in parts:
            if code == "en" or code in seen:
                continue
            if not medias.is_valid_language(code):
                continue
            seen.add(code)
            kept.append(code)
        update_fields["ad_supported_langs"] = ",".join(kept) if kept else None
    try:
        medias.update_product(pid, **update_fields)
    except ValueError as e:
        return jsonify({"error": "invalid_product_field", "message": str(e)}), 400
    except pymysql.err.IntegrityError as e:
        code = e.args[0] if e.args else None
        if code == 1062 and "uk_media_products_mk_id" in str(e):
            return jsonify({
                "error": "mk_id_conflict",
                "message": "明空 ID 已被其他产品占用",
            }), 409
        raise

    if {"name", "product_code", "localized_links_json"} & set(update_fields):
        routes._schedule_material_evaluation(pid, force=True)

    if isinstance(body.get("copywritings"), dict):
        for lang_code, lang_items in body["copywritings"].items():
            if not medias.is_valid_language(lang_code):
                continue
            if isinstance(lang_items, list):
                medias.replace_copywritings(pid, lang_items, lang=lang_code)
    return jsonify({"ok": True})


@bp.route("/api/products/<int:pid>/owner", methods=["PATCH"])
@login_required
def api_update_product_owner(pid: int):
    routes = _routes_module()
    if not routes._is_admin():
        return jsonify({"error": "仅管理员可操作"}), 403
    body = request.get_json(silent=True) or {}
    raw_uid = body.get("user_id")
    try:
        new_uid = int(raw_uid)
    except (TypeError, ValueError):
        return jsonify({"error": "user_id required"}), 400

    product = medias.get_product(pid)
    if not product or product.get("deleted_at") is not None:
        abort(404)

    try:
        medias.update_product_owner(pid, new_uid)
    except ValueError as exc:
        msg = str(exc)
        if msg == "product not found":
            abort(404)
        return jsonify({"error": msg}), 400

    owner_name = medias.get_user_display_name(new_uid)
    return jsonify({"user_id": new_uid, "owner_name": owner_name})


@bp.route("/api/products/<int:pid>", methods=["DELETE"])
@login_required
def api_delete_product(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    medias.soft_delete_product(pid)
    return jsonify({"ok": True})


@bp.route("/<int:pid>/roas")
@login_required
def roas_page(pid: int):
    product = medias.get_product(pid)
    routes = _routes_module()
    if not product or not routes._can_access_product(product):
        abort(404)
    # covers={} skips the DB-touching get_product_covers lookup; the page does
    # not render the cover server-side and the controller loads it client-side.
    return render_template(
        "medias/roas.html",
        product=_serialize_product(product, covers={}),
        roas_rmb_per_usd=product_roas.get_configured_rmb_per_usd(),
    )
