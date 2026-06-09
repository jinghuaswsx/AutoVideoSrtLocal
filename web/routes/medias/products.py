from __future__ import annotations

from flask import abort, render_template, request
from flask_login import current_user, login_required

from appcore import (
    dianxiaomi_mingkong_pairing,
    medias,
    parcel_cost_suggest,
    product_link_domains,
    product_roas,
    pushes,
    scheduled_tasks,
    supply_pairing,
)
from web.auth import admin_required, permission_required
from . import bp
from ._serializers import _serialize_item, _serialize_product, _serialize_product_skus
from web.services.media_products_listing import (
    build_products_list_response as _build_products_list_response_impl,
    products_list_flask_response as _products_list_flask_response_impl,
)
from web.services.media_product_detail import (
    build_product_detail_response as _build_product_detail_response_impl,
    product_detail_flask_response as _product_detail_flask_response_impl,
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
from web.services.media_product_sku_manual_edit import (
    build_product_sku_create_response as _build_product_sku_create_response_impl,
    build_product_sku_update_response as _build_product_sku_update_response_impl,
    product_sku_update_flask_response as _product_sku_update_flask_response_impl,
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
from web.services.media_roas_page import (
    build_roas_page_context as _build_roas_page_context_impl,
)


def _routes_module():
    from web.routes import medias as routes

    return routes


def _mk_copywriting_http_get(*args, **kwargs):
    return _routes_module().requests.get(*args, **kwargs)


def _build_products_list_response(args):
    return _build_products_list_response_impl(
        args,
        serialize_product_fn=lambda *a, **kw: _serialize_product(
            *a, **kw, include_product_link_domains=True
        ),
    )


def _products_list_flask_response(payload: dict):
    return _products_list_flask_response_impl(payload)


def _build_product_detail_response(pid: int, product: dict):
    return _build_product_detail_response_impl(
        pid,
        product=product,
        serialize_product_fn=lambda *args, **kwargs: _serialize_product(
            *args,
            **kwargs,
            include_product_link_domains=True,
        ),
        serialize_item_fn=_serialize_item,
    )


def _product_detail_flask_response(payload: dict):
    return _product_detail_flask_response_impl(payload)


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
        http_get_fn=_mk_copywriting_http_get,
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


def _build_product_sku_update_response(pid: int, sku_id: int, product: dict, body: dict, *, edited_by: int | None):
    return _build_product_sku_update_response_impl(
        pid,
        sku_id,
        product,
        body,
        edited_by=edited_by,
        update_product_sku_fn=medias.update_product_sku_manual,
        normalize_fields_fn=medias.normalize_product_sku_manual_update,
        can_edit_variant_title_fn=medias.can_edit_product_sku_variant_title,
        list_yuncang_unit_prices_fn=medias.list_yuncang_unit_prices,
        get_configured_rmb_per_usd_fn=product_roas.get_configured_rmb_per_usd,
        serialize_product_skus_fn=_serialize_product_skus,
    )


def _build_product_sku_create_response(pid: int, product: dict, body: dict, *, edited_by: int | None):
    return _build_product_sku_create_response_impl(
        pid,
        product,
        body,
        edited_by=edited_by,
        create_product_sku_fn=medias.create_product_sku_manual,
        normalize_fields_fn=medias.normalize_product_sku_manual_update,
        list_yuncang_unit_prices_fn=medias.list_yuncang_unit_prices,
        get_configured_rmb_per_usd_fn=product_roas.get_configured_rmb_per_usd,
        serialize_product_skus_fn=_serialize_product_skus,
    )


def _product_sku_update_flask_response(result):
    return _product_sku_update_flask_response_impl(result)


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
        list_yuncang_unit_prices_fn=medias.list_yuncang_unit_prices,
        get_configured_rmb_per_usd_fn=product_roas.get_configured_rmb_per_usd,
        serialize_product_skus_fn=_serialize_product_skus,
        record_fetch_failure_fn=scheduled_tasks.record_failure,
    )


def _refresh_shopify_sku_flask_response(result):
    return _refresh_shopify_sku_flask_response_impl(result)


def _build_mingkong_pairing_workbench_response(pid: int, product: dict):
    sku_rows = medias.list_product_skus(pid)
    return dianxiaomi_mingkong_pairing.build_workbench_payload(
        product,
        sku_rows,
        include_mingkong_reference=True,
    )


def _build_mingkong_pairing_import_skus_response(pid: int, product: dict):
    existing_rows = medias.list_product_skus(pid)
    if existing_rows:
        return {
            "ok": False,
            "error": "local_skus_exist",
            "message": "我们系统已存在 SKU 行，未覆盖明空 SKU",
            "existing_count": len(existing_rows),
        }
    payload = dianxiaomi_mingkong_pairing.build_mingkong_library_sku_import_payload(product)
    pairs = payload.get("pairs") or []
    if not pairs:
        return {
            "ok": False,
            "error": "mingkong_skus_missing",
            "message": payload.get("message") or "明空产品库未找到可同步的 SKU 行",
            "realtime_refresh": payload.get("realtime_refresh"),
        }
    stats = medias.replace_product_skus(pid, pairs, source="mingkong_library")
    imported_rows = medias.list_product_skus(pid)
    return {
        "ok": True,
        "message": f"已同步 {len(imported_rows)} 行明空 SKU 到我们系统",
        "stats": stats,
        "items": imported_rows,
        "mingkong_items": payload.get("items") or [],
        "realtime_refresh": payload.get("realtime_refresh"),
    }


def _build_mingkong_pairing_confirm_response(pid: int, product: dict, body: dict):
    sku_rows = medias.list_product_skus(pid)
    return dianxiaomi_mingkong_pairing.confirm_dxm03_pairing(
        product,
        sku_rows,
        selections=body.get("items") if isinstance(body, dict) else None,
    )


def _build_mingkong_pairing_replicate_response(pid: int, product: dict, body: dict):
    sku_rows = medias.list_product_skus(pid)
    return dianxiaomi_mingkong_pairing.replicate_mingkong_skus_to_dxm03(
        product,
        sku_rows,
        selections=body.get("items") if isinstance(body, dict) else None,
    )


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
    return routes._products_list_flask_response(
        routes._build_products_list_response(request.args)
    )


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
    return routes._product_detail_flask_response(
        routes._build_product_detail_response(pid, p)
    )


def _parse_enabled_domain_ids(body: dict) -> list[int]:
    raw_ids = body.get("enabled_domain_ids") if isinstance(body, dict) else []
    if not isinstance(raw_ids, list):
        return []
    ids: list[int] = []
    seen: set[int] = set()
    for value in raw_ids:
        try:
            domain_id = int(value)
        except (TypeError, ValueError):
            continue
        if domain_id <= 0 or domain_id in seen:
            continue
        seen.add(domain_id)
        ids.append(domain_id)
    return ids


@bp.route("/api/products/<int:pid>/product-link-domains", methods=["GET"])
@login_required
def api_get_product_link_domains(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    return {
        "product": p,
        "domains": product_link_domains.list_product_domain_options(pid),
    }


@bp.route("/api/products/<int:pid>/product-link-domains", methods=["POST"])
@login_required
def api_set_product_link_domains(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    enabled_ids = _parse_enabled_domain_ids(body)
    product_link_domains.set_product_domain_enabled_ids(pid, enabled_ids)

    # 重新加载最新的产品状态，确保能根据最新的生效域名解析完整的产品页面链接
    p = medias.get_product(pid)
    from appcore import mk_import as mk_import_svc

    probe_results = []
    product_urls = product_link_domains.resolve_product_page_url_rows(p, "en")
    for row in product_urls:
        domain = row.get("domain")
        url = row.get("url")
        if not url:
            continue
        ok, detail = mk_import_svc._probe_product_link(url)
        status = "done" if ok else "warning"
        message = "商品链接探测通过" if ok else "商品链接可能不可访问"
        probe_results.append({
            "key": f"domain_link_probe_{domain}",
            "title": f"发布域名链接探测 ({domain})",
            "status": status,
            "message": message,
            "logs": [url, detail or "探测通过"],
        })

    return {
        "ok": True,
        "domains": product_link_domains.list_product_domain_options(pid),
        "probe_results": probe_results,
    }



@bp.route("/api/products/<int:pid>/parcel-cost-suggest", methods=["GET"])
@login_required
def api_parcel_cost_suggest(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    result = routes._build_parcel_cost_suggest_response(pid, request.args)
    return routes._parcel_cost_suggest_flask_response(result)


@bp.route("/api/products/<int:pid>/skus/<int:sku_id>", methods=["PATCH"])
@login_required
def api_update_product_sku(pid: int, sku_id: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    edited_by = int(current_user.id) if getattr(current_user, "id", None) else None
    result = routes._build_product_sku_update_response(
        pid,
        sku_id,
        p,
        body,
        edited_by=edited_by,
    )
    if result.not_found:
        abort(404)
    return routes._product_sku_update_flask_response(result)


@bp.route("/api/products/<int:pid>/skus", methods=["POST"])
@login_required
def api_create_product_sku(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    edited_by = int(current_user.id) if getattr(current_user, "id", None) else None
    result = routes._build_product_sku_create_response(
        pid,
        p,
        body,
        edited_by=edited_by,
    )
    return routes._product_sku_update_flask_response(result)


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


@bp.route("/api/products/<int:pid>/mingkong-pairing", methods=["GET"])
@login_required
@admin_required
@permission_required("medias")
def api_mingkong_pairing_workbench(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    return routes._build_mingkong_pairing_workbench_response(pid, p)


@bp.route("/api/products/<int:pid>/mingkong-pairing/import-skus", methods=["POST"])
@login_required
@admin_required
@permission_required("medias")
def api_mingkong_pairing_import_skus(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    result = routes._build_mingkong_pairing_import_skus_response(pid, p)
    status = 200 if result.get("ok") else 409
    return result, status


@bp.route("/api/products/<int:pid>/mingkong-pairing/confirm", methods=["POST"])
@login_required
@admin_required
@permission_required("medias")
def api_mingkong_pairing_confirm(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    result = routes._build_mingkong_pairing_confirm_response(pid, p, body)
    status = 200 if result.get("ok") else 409
    return result, status


@bp.route("/api/products/<int:pid>/mingkong-pairing/replicate", methods=["POST"])
@login_required
@admin_required
@permission_required("medias")
def api_mingkong_pairing_replicate(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    result = routes._build_mingkong_pairing_replicate_response(pid, p, body)
    status = 200 if result.get("ok") else 409
    return result, status


@bp.route("/api/products/<int:pid>/ad-orders-report", methods=["GET"])
@login_required
def api_product_ad_orders_report(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    from appcore.media_product_ad_orders_report import get_product_ad_orders_report
    return get_product_ad_orders_report(pid)


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
