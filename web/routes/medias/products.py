from __future__ import annotations

import requests
import pymysql.err
from flask import abort, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore import medias, parcel_cost_suggest, product_roas, pushes, sku_aggregates, supply_pairing, xmyc_storage
from . import bp
from ._serializers import (
    _int_or_none,
    _serialize_item,
    _serialize_product,
    _serialize_product_skus,
)


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

    xmyc_match = (request.args.get("xmyc_match") or "all").strip().lower()
    if xmyc_match not in medias.XMYC_MATCH_FILTERS:
        xmyc_match = "all"
    roas_status = (request.args.get("roas_status") or "all").strip().lower()
    if roas_status not in medias.ROAS_STATUS_FILTERS:
        roas_status = "all"

    rows, total = medias.list_products(None, keyword=keyword, archived=archived,
                                       offset=offset, limit=limit,
                                       xmyc_match=xmyc_match,
                                       roas_status=roas_status)
    pids = [r["id"] for r in rows]
    counts = medias.count_items_by_product(pids)
    raw_counts = medias.count_raw_sources_by_product(pids)
    thumb_covers = medias.first_thumb_item_by_product(pids)
    filenames = medias.list_item_filenames_by_product(pids, limit_per=5)
    coverage = medias.lang_coverage_by_product(pids)
    covers_map = medias.get_product_covers_batch(pids)
    skus_map = medias.list_product_skus_batch(pids)
    all_dxm_skus = sorted({
        (s.get("dianxiaomi_sku") or "").strip()
        for sku_rows in skus_map.values()
        for s in sku_rows
        if (s.get("dianxiaomi_sku") or "").strip()
    })
    xmyc_index = medias.list_xmyc_unit_prices(all_dxm_skus)
    roas_rmb_per_usd = product_roas.get_configured_rmb_per_usd()
    data = [
        _serialize_product(
            r, counts.get(r["id"], 0), thumb_covers.get(r["id"]),
            items_filenames=filenames.get(r["id"], []),
            lang_coverage=coverage.get(r["id"], {}),
            covers=covers_map.get(r["id"], {}),
            raw_sources_count=raw_counts.get(r["id"], 0),
            roas_rmb_per_usd=roas_rmb_per_usd,
            skus=skus_map.get(r["id"], []),
            xmyc_index=xmyc_index,
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
    skus = medias.list_product_skus(pid)
    xmyc_index = medias.list_xmyc_unit_prices(
        [s.get("dianxiaomi_sku") or "" for s in skus]
    )
    return jsonify({
        "product": _serialize_product(
            p,
            None,
            covers=covers,
            roas_rmb_per_usd=product_roas.get_configured_rmb_per_usd(),
            skus=skus,
            xmyc_index=xmyc_index,
        ),
        "covers": covers,
        "copywritings": medias.list_copywritings(pid),
        "items": [_serialize_item(i, raw_sources_by_id) for i in items],
    })


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
