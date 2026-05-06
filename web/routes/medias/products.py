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
    product_owner_update_flask_response as _product_owner_update_flask_response_impl,
)
from web.services.media_product_mutations import (
    build_product_create_response as _build_product_create_response_impl,
    build_product_delete_response as _build_product_delete_response_impl,
    build_product_update_response as _build_product_update_response_impl,
    product_mutation_flask_response as _product_mutation_flask_response_impl,
)
from web.services.media_shopify_sku_refresh import (
    build_refresh_product_shopify_sku_response as _build_refresh_product_shopify_sku_response_impl,
    refresh_shopify_sku_flask_response as _refresh_shopify_sku_flask_response_impl,
)
from web.services.media_mk_copywriting import (
    build_mk_copywriting_response as _build_mk_copywriting_response_impl,
    extract_mk_copywriting as _extract_mk_copywriting,
    format_mk_copywriting_text as _format_mk_copywriting_text,
    mk_copywriting_flask_response as _mk_copywriting_flask_response_impl,
    mk_product_link_tail as _mk_product_link_tail,
    normalize_mk_copywriting_query as _normalize_mk_copywriting_query,
)
from web.services.media_parcel_cost import (
    build_parcel_cost_suggest_response as _build_parcel_cost_suggest_response_impl,
    parcel_cost_suggest_flask_response as _parcel_cost_suggest_flask_response_impl,
)
from web.services.media_supply_pairing import (
    build_supply_pairing_search_response as _build_supply_pairing_search_response_impl,
    supply_pairing_search_flask_response as _supply_pairing_search_flask_response_impl,
)
from web.services.media_xmyc_skus import (
    build_product_xmyc_skus_response as _build_product_xmyc_skus_response_impl,
    build_product_xmyc_skus_set_response as _build_product_xmyc_skus_set_response_impl,
    build_xmyc_sku_update_response as _build_xmyc_sku_update_response_impl,
    build_xmyc_skus_list_response as _build_xmyc_skus_list_response_impl,
    xmyc_sku_flask_response as _xmyc_sku_flask_response_impl,
)
from web.services.media_roas_page import (
    build_roas_page_context as _build_roas_page_context_impl,
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


def _product_owner_update_flask_response(result):
    return _product_owner_update_flask_response_impl(result)


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


def _product_mutation_flask_response(result):
    return _product_mutation_flask_response_impl(result)

def _build_mk_copywriting_response(args):
    routes = _routes_module()
    return _build_mk_copywriting_response_impl(
        args,
        build_headers_fn=routes._build_mk_request_headers,
        get_base_url_fn=routes._get_mk_api_base_url,
        is_login_expired_fn=routes._is_mk_login_expired,
        http_get_fn=routes.requests.get,
    )


def _mk_copywriting_flask_response(result):
    return _mk_copywriting_flask_response_impl(result)


def _build_supply_pairing_search_response(args):
    return _build_supply_pairing_search_response_impl(
        args,
        search_supply_pairing_fn=supply_pairing.search_supply_pairing,
        extract_1688_url_fn=supply_pairing.extract_1688_url,
    )


def _supply_pairing_search_flask_response(result):
    return _supply_pairing_search_flask_response_impl(result)


def _build_xmyc_skus_list_response(args):
    return _build_xmyc_skus_list_response_impl(
        args,
        list_skus_fn=xmyc_storage.list_skus,
        get_configured_rmb_per_usd_fn=product_roas.get_configured_rmb_per_usd,
        enrich_skus_with_roas_fn=sku_aggregates.enrich_skus_with_roas,
    )


def _build_product_xmyc_skus_response(pid: int):
    return _build_product_xmyc_skus_response_impl(
        pid,
        get_skus_for_product_fn=xmyc_storage.get_skus_for_product,
        get_configured_rmb_per_usd_fn=product_roas.get_configured_rmb_per_usd,
        enrich_skus_with_roas_fn=sku_aggregates.enrich_skus_with_roas,
    )


def _build_product_xmyc_skus_set_response(pid: int, body: dict, *, matched_by: int | None):
    return _build_product_xmyc_skus_set_response_impl(
        pid,
        body,
        matched_by=matched_by,
        set_product_skus_fn=xmyc_storage.set_product_skus,
    )


def _build_xmyc_sku_update_response(sku_id: int, body: dict):
    return _build_xmyc_sku_update_response_impl(
        sku_id,
        body,
        update_sku_fn=xmyc_storage.update_sku,
        get_configured_rmb_per_usd_fn=product_roas.get_configured_rmb_per_usd,
        enrich_skus_with_roas_fn=sku_aggregates.enrich_skus_with_roas,
    )


def _xmyc_sku_flask_response(result):
    return _xmyc_sku_flask_response_impl(result)


def _build_parcel_cost_suggest_response(pid: int, args):
    return _build_parcel_cost_suggest_response_impl(
        pid,
        args,
        default_lookback_days=parcel_cost_suggest.DEFAULT_LOOKBACK_DAYS,
        error_type=parcel_cost_suggest.ParcelCostSuggestError,
        suggest_parcel_cost_fn=parcel_cost_suggest.suggest_parcel_cost,
    )


def _parcel_cost_suggest_flask_response(result):
    return _parcel_cost_suggest_flask_response_impl(result)


def _build_refresh_product_shopify_sku_response(pid: int, product: dict):
    from tools import dianxiaomi_sku_sync as sync_mod

    return _build_refresh_product_shopify_sku_response_impl(
        pid,
        product,
        fetch_shopify_and_dxm_fn=sync_mod.fetch_shopify_and_dxm_via_cdp,
        build_pair_rows_fn=sync_mod.build_pair_rows,
        update_product_fn=medias.update_product,
        replace_product_skus_fn=medias.replace_product_skus,
        list_product_skus_fn=medias.list_product_skus,
        list_xmyc_unit_prices_fn=medias.list_xmyc_unit_prices,
        get_configured_rmb_per_usd_fn=product_roas.get_configured_rmb_per_usd,
        serialize_product_skus_fn=_serialize_product_skus,
    )


def _refresh_shopify_sku_flask_response(result):
    return _refresh_shopify_sku_flask_response_impl(result)


def _build_roas_page_context(product: dict):
    return _build_roas_page_context_impl(
        product,
        serialize_product_fn=_serialize_product,
    )


@bp.route("/api/mk-copywriting", methods=["GET"])
@login_required
def api_mk_copywriting():
    routes = _routes_module()
    result = routes._build_mk_copywriting_response(request.args)
    return routes._mk_copywriting_flask_response(result)


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
    return routes._product_mutation_flask_response(result)


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
    result = routes._build_parcel_cost_suggest_response(pid, request.args)
    return routes._parcel_cost_suggest_flask_response(result)


@bp.route("/api/xmyc-skus", methods=["GET"])
@login_required
def api_list_xmyc_skus():
    routes = _routes_module()
    result = routes._build_xmyc_skus_list_response(request.args)
    return routes._xmyc_sku_flask_response(result)


@bp.route("/api/products/<int:pid>/xmyc-skus", methods=["GET"])
@login_required
def api_get_product_xmyc_skus(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    result = routes._build_product_xmyc_skus_response(pid)
    return routes._xmyc_sku_flask_response(result)


@bp.route("/api/products/<int:pid>/xmyc-skus", methods=["POST"])
@login_required
def api_set_product_xmyc_skus(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    matched_by = int(current_user.id) if getattr(current_user, "id", None) else None
    result = routes._build_product_xmyc_skus_set_response(
        pid,
        body,
        matched_by=matched_by,
    )
    return routes._xmyc_sku_flask_response(result)


@bp.route("/api/xmyc-skus/<int:sku_id>", methods=["PATCH"])
@login_required
def api_update_xmyc_sku(sku_id: int):
    routes = _routes_module()
    body = request.get_json(silent=True) or {}
    result = routes._build_xmyc_sku_update_response(sku_id, body)
    if result.not_found:
        abort(404)
    return routes._xmyc_sku_flask_response(result)


@bp.route("/api/supply-pairing/search", methods=["GET"])
@login_required
def api_supply_pairing_search():
    routes = _routes_module()
    result = routes._build_supply_pairing_search_response(request.args)
    return routes._supply_pairing_search_flask_response(result)


@bp.route("/api/products/<int:pid>", methods=["PUT"])
@login_required
def api_update_product(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    result = routes._build_product_update_response(pid, p, body)
    return routes._product_mutation_flask_response(result)


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
    return routes._product_owner_update_flask_response(result)


@bp.route("/api/products/<int:pid>", methods=["DELETE"])
@login_required
def api_delete_product(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    result = routes._build_product_delete_response(pid)
    return routes._product_mutation_flask_response(result)


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
    result = routes._build_refresh_product_shopify_sku_response(pid, p)
    return routes._refresh_shopify_sku_flask_response(result)


@bp.route("/<int:pid>/roas")
@login_required
def roas_page(pid: int):
    product = medias.get_product(pid)
    routes = _routes_module()
    if not product or not routes._can_access_product(product):
        abort(404)
    return render_template(
        "medias/roas.html",
        **routes._build_roas_page_context(product),
    )
