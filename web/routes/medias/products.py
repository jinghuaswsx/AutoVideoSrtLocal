from __future__ import annotations

from flask import abort, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore import medias, parcel_cost_suggest, product_roas, pushes, sku_aggregates, supply_pairing, xmyc_storage
from . import bp
from ._serializers import _serialize_item, _serialize_product, _serialize_product_skus
from web.services.media_products_listing import (
    build_products_list_response as _build_products_list_response_impl,
)
from web.services.media_product_detail import (
    build_product_detail_response as _build_product_detail_response_impl,
)
from web.services.media_product_owner import (
    build_product_owner_update_response as _build_product_owner_update_response_impl,
)
from web.services.media_product_mutations import (
    build_product_create_response as _build_product_create_response_impl,
    build_product_delete_response as _build_product_delete_response_impl,
    build_product_update_response as _build_product_update_response_impl,
)
from web.services.media_mk_copywriting import (
    build_mk_copywriting_response as _build_mk_copywriting_response_impl,
    extract_mk_copywriting as _extract_mk_copywriting,
    format_mk_copywriting_text as _format_mk_copywriting_text,
    mk_product_link_tail as _mk_product_link_tail,
    normalize_mk_copywriting_query as _normalize_mk_copywriting_query,
)


def _routes_module():
    from web.routes import medias as routes

    return routes


def _build_products_list_response(args):
    return _build_products_list_response_impl(
        args,
        serialize_product_fn=_serialize_product,
    )


def _build_product_detail_response(pid: int, product: dict):
    return _build_product_detail_response_impl(
        pid,
        product=product,
        serialize_product_fn=_serialize_product,
        serialize_item_fn=_serialize_item,
    )


def _build_product_owner_update_response(pid: int, body: dict, *, is_admin: bool):
    return _build_product_owner_update_response_impl(
        pid,
        body,
        is_admin=is_admin,
    )


def _build_product_create_response(body: dict, *, user_id: int):
    return _build_product_create_response_impl(
        body,
        user_id=user_id,
    )


def _build_product_update_response(pid: int, product: dict, body: dict):
    return _build_product_update_response_impl(
        pid,
        product,
        body,
        schedule_material_evaluation_fn=_routes_module()._schedule_material_evaluation,
    )


def _build_product_delete_response(pid: int):
    return _build_product_delete_response_impl(pid)

def _build_mk_copywriting_response(args):
    routes = _routes_module()
    return _build_mk_copywriting_response_impl(
        args,
        build_headers_fn=routes._build_mk_request_headers,
        get_base_url_fn=routes._get_mk_api_base_url,
        is_login_expired_fn=routes._is_mk_login_expired,
        http_get_fn=routes.requests.get,
    )


@bp.route("/api/mk-copywriting", methods=["GET"])
@login_required
def api_mk_copywriting():
    routes = _routes_module()
    result = routes._build_mk_copywriting_response(request.args)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products", methods=["GET"])
@login_required
def api_list_products():
    routes = _routes_module()
    return jsonify(routes._build_products_list_response(request.args))


@bp.route("/api/products", methods=["POST"])
@login_required
def api_create_product():
    routes = _routes_module()
    body = request.get_json(silent=True) or {}
    result = routes._build_product_create_response(
        body,
        user_id=current_user.id,
    )
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>", methods=["GET"])
@login_required
def api_get_product(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    return jsonify(routes._build_product_detail_response(pid, p))


@bp.route("/api/products/<int:pid>/parcel-cost-suggest", methods=["GET"])
@login_required
def api_parcel_cost_suggest(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    try:
        days = int(request.args.get("days") or parcel_cost_suggest.DEFAULT_LOOKBACK_DAYS)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_days"}), 400
    days = max(7, min(90, days))
    try:
        suggestion = parcel_cost_suggest.suggest_parcel_cost(pid, days=days)
    except parcel_cost_suggest.ParcelCostSuggestError as exc:
        msg = str(exc)
        if msg == "no_orders":
            return jsonify({
                "error": "no_orders",
                "message": "该产品在店小秘还没有订单数据，无法估算实际小包成本",
            }), 404
        return jsonify({"error": "dxm_failed", "message": msg}), 502
    except Exception as exc:  # pragma: no cover - safety net for browser glue
        return jsonify({"error": "dxm_failed", "message": str(exc)}), 502
    return jsonify({"ok": True, "suggestion": suggestion})


@bp.route("/api/xmyc-skus", methods=["GET"])
@login_required
def api_list_xmyc_skus():
    keyword = (request.args.get("keyword") or "").strip() or None
    matched_filter = (request.args.get("matched") or "all").strip().lower()
    if matched_filter not in ("all", "matched", "unmatched"):
        matched_filter = "all"
    try:
        limit = max(1, min(500, int(request.args.get("limit") or 200)))
        offset = max(0, int(request.args.get("offset") or 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_pagination"}), 400
    rows = xmyc_storage.list_skus(
        keyword=keyword,
        matched_filter=matched_filter,
        limit=limit,
        offset=offset,
    )
    rate = product_roas.get_configured_rmb_per_usd()
    rows = sku_aggregates.enrich_skus_with_roas(rows, rate)
    return jsonify({"ok": True, "items": rows, "limit": limit, "offset": offset})


@bp.route("/api/products/<int:pid>/xmyc-skus", methods=["GET"])
@login_required
def api_get_product_xmyc_skus(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    rows = xmyc_storage.get_skus_for_product(pid)
    rate = product_roas.get_configured_rmb_per_usd()
    rows = sku_aggregates.enrich_skus_with_roas(rows, rate)
    return jsonify({"ok": True, "items": rows})


@bp.route("/api/products/<int:pid>/xmyc-skus", methods=["POST"])
@login_required
def api_set_product_xmyc_skus(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    raw_skus = body.get("skus") or []
    if not isinstance(raw_skus, list):
        return jsonify({"error": "skus_must_be_list"}), 400
    skus = [str(s).strip() for s in raw_skus if str(s).strip()]
    matched_by = int(current_user.id) if getattr(current_user, "id", None) else None
    result = xmyc_storage.set_product_skus(pid, skus, matched_by=matched_by)
    return jsonify({"ok": True, **result})


@bp.route("/api/xmyc-skus/<int:sku_id>", methods=["PATCH"])
@login_required
def api_update_xmyc_sku(sku_id: int):
    body = request.get_json(silent=True) or {}
    try:
        row = xmyc_storage.update_sku(sku_id, body)
    except ValueError as exc:
        return jsonify({"error": "invalid_fields", "message": str(exc)}), 400
    except LookupError:
        abort(404)
    rate = product_roas.get_configured_rmb_per_usd()
    enriched = sku_aggregates.enrich_skus_with_roas([row], rate)
    return jsonify({"ok": True, "item": enriched[0]})


@bp.route("/api/supply-pairing/search", methods=["GET"])
@login_required
def api_supply_pairing_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "missing_query", "message": "请提供 SKU 或关键词"}), 400
    # Default status="" hits both waiting (status=1) and paired (status=2)
    # records, ~378 total on MKTT — the waiting list rows carry an
    # alibabaProductId we can turn into a real 1688 link via
    # supply_pairing.extract_1688_url. Callers can still pin status=2 to
    # only see user-confirmed pairings.
    raw_status = request.args.get("status")
    status = "" if raw_status is None else str(raw_status)
    try:
        result = supply_pairing.search_supply_pairing(q, status=status)
    except Exception as exc:
        return jsonify({"error": "dxm_failed", "message": str(exc)}), 502
    items = result.get("items") or []
    enriched = []
    for it in items:
        url_1688 = supply_pairing.extract_1688_url(it)
        copy = dict(it)
        copy["extracted_1688_url"] = (
            url_1688 if url_1688 and "1688.com" in url_1688 else None
        )
        enriched.append(copy)
    result["items"] = enriched
    return jsonify({"ok": True, **result})


@bp.route("/api/products/<int:pid>", methods=["PUT"])
@login_required
def api_update_product(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    result = routes._build_product_update_response(pid, p, body)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/owner", methods=["PATCH"])
@login_required
def api_update_product_owner(pid: int):
    routes = _routes_module()
    body = request.get_json(silent=True) or {}
    result = routes._build_product_owner_update_response(
        pid,
        body,
        is_admin=routes._is_admin(),
    )
    if result.not_found:
        abort(404)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>", methods=["DELETE"])
@login_required
def api_delete_product(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    result = routes._build_product_delete_response(pid)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/refresh-shopify-sku", methods=["POST"])
@login_required
def api_refresh_product_shopify_sku(pid: int):
    """单产品手动同步：连接常驻 CDP 浏览器，从店小秘拉一次全量数据，
    回填该产品的 shopify_title 和 media_product_skus 配对行。

    需要服务器上 9222 端口的常驻 chrome 已经登录店小秘（与
    tools/shopifyid_dianxiaomi_sync.py 共用）。
    """
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    shopify_id = (p.get("shopifyid") or "").strip()
    if not shopify_id:
        return jsonify({
            "error": "missing_shopifyid",
            "message": "该产品尚未关联 Shopify ID，无法刷新 SKU/英文名",
        }), 400

    from tools import dianxiaomi_sku_sync as sync_mod

    try:
        shopify_products, dxm_index = sync_mod.fetch_shopify_and_dxm_via_cdp()
    except Exception as exc:
        return jsonify({
            "error": "fetch_failed",
            "message": f"店小秘数据拉取失败：{exc}",
        }), 502

    pair_index = sync_mod.build_pair_rows(shopify_products, dxm_index)
    title_map = {
        (item.get("shopify_product_id") or ""): (item.get("shopify_title") or "")
        for item in shopify_products
    }
    pairs = pair_index.get(shopify_id)
    if pairs is None:
        return jsonify({
            "error": "shopify_product_not_found",
            "message": f"店小秘 Shopify 商品库未找到 shopifyid={shopify_id}",
        }), 404

    new_title = (title_map.get(shopify_id) or "").strip() or None
    medias.update_product(pid, shopify_title=new_title)
    medias.replace_product_skus(pid, pairs, source="manual")

    fresh_skus = medias.list_product_skus(pid)
    xmyc_index = medias.list_xmyc_unit_prices(
        [s.get("dianxiaomi_sku") or "" for s in fresh_skus]
    )
    cost_inputs = {
        "purchase_price": p.get("purchase_price"),
        "packet_cost_estimated": p.get("packet_cost_estimated"),
        "packet_cost_actual": p.get("packet_cost_actual"),
        "standalone_shipping_fee": p.get("standalone_shipping_fee"),
    }
    return jsonify({
        "ok": True,
        "shopify_title": new_title or "",
        "skus": _serialize_product_skus(
            fresh_skus,
            cost_inputs=cost_inputs,
            rmb_per_usd=product_roas.get_configured_rmb_per_usd(),
            xmyc_index=xmyc_index,
        ),
        "summary": {
            "variant_pairs": len(pairs),
            "pairs_with_dxm": sum(1 for r in pairs if r.get("dianxiaomi_sku_code")),
        },
    })


@bp.route("/<int:pid>/roas")
@login_required
def roas_page(pid: int):
    product = medias.get_product(pid)
    routes = _routes_module()
    if not product or not routes._can_access_product(product):
        abort(404)
    return render_template(
        "medias/roas.html",
        product=_serialize_product(product),
        roas_rmb_per_usd=product_roas.get_configured_rmb_per_usd(),
    )
