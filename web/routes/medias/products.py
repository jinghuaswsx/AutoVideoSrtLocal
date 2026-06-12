from __future__ import annotations

from flask import abort, current_app, render_template, request
from flask_login import current_user, login_required

from appcore import (
    dianxiaomi_mingkong_pairing,
    dianxiaomi_yuncang,
    mingkong_request_monitor,
    mingkong_pairing_ai,
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


def _mingkong_pairing_action_error_payload(action_label: str, exc: Exception) -> dict:
    detail = str(exc) or exc.__class__.__name__
    message = f"{action_label} 失败：{detail}"
    return {
        "ok": False,
        "error": "mingkong_pairing_internal_error",
        "message": message,
        "logs": [
            {"level": "info", "message": f"{action_label}请求已进入后端"},
            {"level": "error", "message": message},
        ],
        "items": [],
    }


def _mk_copywriting_http_get(*args, **kwargs):
    url = args[0] if args else kwargs.pop("url")
    return mingkong_request_monitor.tracked_get(
        url,
        source="medias.mk_copywriting",
        request_fn=_routes_module().requests.get,
        **kwargs,
    )


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
        list_shopify_product_ids_fn=medias.list_shopify_product_ids,
    )


def _refresh_shopify_sku_flask_response(result):
    return _refresh_shopify_sku_flask_response_impl(result)


def _build_mingkong_pairing_workbench_response(pid: int, product: dict, force: bool = False):
    sku_rows = medias.list_product_skus(pid)
    return dianxiaomi_mingkong_pairing.build_workbench_payload(
        product,
        sku_rows,
        include_mingkong_reference=True,
        force_refresh=force,
    )


def _build_mingkong_pairing_import_skus_response(pid: int, product: dict):
    existing_rows = medias.list_product_skus(pid)
    if existing_rows:
        message = "我们系统已存在 SKU 行，未覆盖明空 SKU"
        return {
            "ok": False,
            "error": "local_skus_exist",
            "message": message,
            "existing_count": len(existing_rows),
            "logs": [{
                "level": "warn",
                "message": f"{message}；当前我们系统 SKU 行 {len(existing_rows)}",
            }],
        }
    payload = dianxiaomi_mingkong_pairing.build_mingkong_library_sku_import_payload(product)
    pairs = payload.get("pairs") or []
    if not pairs:
        message = payload.get("message") or "明空产品库未找到可同步的 SKU 行"
        return {
            "ok": False,
            "error": "mingkong_skus_missing",
            "message": message,
            "realtime_refresh": payload.get("realtime_refresh"),
            "logs": [
                {"level": "info", "message": "已读取明空产品库 SKU 候选"},
                {"level": "error", "message": message},
            ],
        }
    stats = medias.replace_product_skus(pid, pairs, source="mingkong_library")
    imported_rows = medias.list_product_skus(pid)
    message = f"已同步 {len(imported_rows)} 行明空 SKU 到我们系统"
    return {
        "ok": True,
        "message": message,
        "stats": stats,
        "items": imported_rows,
        "mingkong_items": payload.get("items") or [],
        "realtime_refresh": payload.get("realtime_refresh"),
        "logs": [
            {"level": "info", "message": f"明空产品库返回 {len(pairs)} 行可同步 SKU"},
            {"level": "ok", "message": message},
            {
                "level": "info",
                "message": (
                    "我们系统 SKU 写入统计："
                    f"新增 {stats.get('inserted', 0)}，"
                    f"更新 {stats.get('updated', 0)}，"
                    f"删除 {stats.get('deleted', 0)}，"
                    f"保留 {stats.get('preserved', 0)}"
                ),
            },
        ],
    }


def _build_mingkong_pairing_confirm_response(pid: int, product: dict, body: dict):
    sku_rows = medias.list_product_skus(pid)
    confirm_result = dianxiaomi_mingkong_pairing.confirm_dxm03_pairing(
        product,
        sku_rows,
        selections=body.get("items") if isinstance(body, dict) else None,
    )
    yuncang_pairing_items = _successful_pairing_items_for_yuncang(confirm_result)
    if not confirm_result.get("ok"):
        if not yuncang_pairing_items:
            return confirm_result
        yuncang_result = dianxiaomi_yuncang.add_product_skus_to_yuncang(
            product,
            sku_rows,
            pairing_items=yuncang_pairing_items,
        )
        return {
            "ok": False,
            "message": (
                f"{confirm_result.get('message') or '采购配对存在阻断'}；"
                f"已对可用 SKU 执行云仓：{yuncang_result.get('message')}"
            ),
            "logs": _stage_logs("确认 DXM03 采购配对", confirm_result)
            + _stage_logs("添加 DXM03 小秘云仓商品", yuncang_result),
            "items": yuncang_result.get("items") or confirm_result.get("items") or [],
            "confirm": confirm_result,
            "yuncang": yuncang_result,
        }
    yuncang_result = dianxiaomi_yuncang.add_product_skus_to_yuncang(
        product,
        sku_rows,
        pairing_items=yuncang_pairing_items or confirm_result.get("items") or sku_rows,
    )
    ok = bool(confirm_result.get("ok")) and bool(yuncang_result.get("ok"))
    return {
        "ok": ok,
        "message": yuncang_result.get("message") if not ok else (
            f"{confirm_result.get('message') or '采购配对完成'}；{yuncang_result.get('message')}"
        ),
        "logs": _stage_logs("确认 DXM03 采购配对", confirm_result)
        + _stage_logs("添加 DXM03 小秘云仓商品", yuncang_result),
        "items": yuncang_result.get("items") or confirm_result.get("items") or [],
        "confirm": confirm_result,
        "yuncang": yuncang_result,
    }


def _build_mingkong_pairing_replicate_response(pid: int, product: dict, body: dict):
    sku_rows = medias.list_product_skus(pid)
    return dianxiaomi_mingkong_pairing.replicate_mingkong_skus_to_dxm03(
        product,
        sku_rows,
        selections=body.get("items") if isinstance(body, dict) else None,
    )


def _stage_logs(stage: str, payload: dict) -> list[dict]:
    logs: list[dict] = [{"level": "info", "message": f"{stage}：开始"}]
    for entry in payload.get("logs") or []:
        if isinstance(entry, str):
            logs.append({"level": "info", "message": entry})
        elif isinstance(entry, dict):
            logs.append({
                "level": entry.get("level") or "info",
                "message": entry.get("message") or entry.get("text") or "",
            })
    return logs


_YUNCANG_PAIRING_SUCCESS_STATUSES = {
    "already_configured_preserved",
    "confirmed",
    "already_paired",
    "already_paired_combo_components",
}


def _successful_pairing_items_for_yuncang(confirm_result: dict) -> list[dict]:
    items = confirm_result.get("items") if isinstance(confirm_result, dict) else []
    return [
        item
        for item in items or []
        if isinstance(item, dict)
        and str(item.get("status") or "").strip() in _YUNCANG_PAIRING_SUCCESS_STATUSES
    ]


def _payload_items(payload: dict | None) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    return [item for item in payload.get("items") or [] if isinstance(item, dict)]


def _payload_summary_count(payload: dict | None, key: str) -> int:
    if not isinstance(payload, dict):
        return 0
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    try:
        return int(summary.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _payload_stage_state(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return "not_started"
    items = _payload_items(payload)
    if any(str(item.get("status") or "") == "error" for item in items):
        return "failed"
    if _payload_summary_count(payload, "error_count") > 0:
        return "failed"
    if any(str(item.get("status") or "") == "blocked" for item in items):
        return "blocked"
    if _payload_summary_count(payload, "blocked_count") > 0:
        return "blocked"
    if payload.get("ok"):
        return "completed"
    if payload.get("error"):
        return "failed"
    return "blocked"


def _purchase_price_stage(yuncang_result: dict | None) -> dict:
    if not isinstance(yuncang_result, dict):
        return {
            "stage": "purchase_price_refresh",
            "status": "not_started",
            "message": "云仓阶段未执行，采购价未刷新",
        }
    if not yuncang_result.get("ok"):
        return {
            "stage": "purchase_price_refresh",
            "status": "skipped",
            "message": "云仓阶段未完整完成，采购价刷新跳过",
        }
    summary = yuncang_result.get("summary") if isinstance(yuncang_result.get("summary"), dict) else {}
    local_refresh = summary.get("local_refresh") if isinstance(summary.get("local_refresh"), dict) else {}
    if local_refresh.get("purchase_price") is None:
        return {
            "stage": "purchase_price_refresh",
            "status": "purchase_price_missing",
            "message": "云仓已添加/已存在，但本地采购价没有可用云仓单价可回写",
        }
    return {
        "stage": "purchase_price_refresh",
        "status": "completed",
        "message": "已根据小秘云仓单价刷新本地采购价",
        "purchase_price": local_refresh.get("purchase_price"),
    }


def _stage_entry(
    stage: str,
    status: str,
    message: str,
    *,
    payload: dict | None = None,
    stats: dict | None = None,
) -> dict:
    entry = {"stage": stage, "status": status, "message": message}
    if stats:
        entry["stats"] = stats
    if isinstance(payload, dict):
        summary = payload.get("summary")
        if isinstance(summary, dict):
            entry["summary"] = summary
        error = payload.get("error")
        if error:
            entry["error"] = error
    return entry


def _mingkong_sync_status_payload(
    *,
    import_payload: dict | None = None,
    import_stats: dict | None = None,
    replicate_result: dict | None = None,
    confirm_result: dict | None = None,
    yuncang_result: dict | None = None,
) -> dict:
    stage_summary: list[dict] = []
    if import_stats is None:
        import_message = (
            (import_payload or {}).get("message")
            or "本地 SKU 尚未写入，目标计划没有可写入 SKU 行"
        )
        stage_summary.append(_stage_entry("local_import", "blocked", import_message))
    else:
        stage_summary.append(_stage_entry(
            "local_import",
            "completed",
            "本地 SKU 已按人工确认目标写入",
            stats=import_stats,
        ))

    if replicate_result is None:
        stage_summary.append(_stage_entry("dxm03_replicate", "not_started", "DXM03 商品 SKU 复刻尚未执行"))
    else:
        stage_summary.append(_stage_entry(
            "dxm03_replicate",
            _payload_stage_state(replicate_result),
            replicate_result.get("message") or "DXM03 商品 SKU 复刻已返回结果",
            payload=replicate_result,
        ))

    if confirm_result is None:
        stage_summary.append(_stage_entry("purchase_pairing", "not_started", "DXM03 1688 采购配对尚未执行"))
    else:
        stage_summary.append(_stage_entry(
            "purchase_pairing",
            _payload_stage_state(confirm_result),
            confirm_result.get("message") or "DXM03 1688 采购配对已返回结果",
            payload=confirm_result,
        ))

    if yuncang_result is None:
        yuncang_message = "DXM03 小秘云仓添加尚未执行"
        if confirm_result is not None:
            yuncang_message = "没有采购配对成功的基础 SKU，小秘云仓阶段跳过"
        stage_summary.append(_stage_entry("yuncang_add", "not_started", yuncang_message))
    else:
        stage_summary.append(_stage_entry(
            "yuncang_add",
            _payload_stage_state(yuncang_result),
            yuncang_result.get("message") or "DXM03 小秘云仓添加已返回结果",
            payload=yuncang_result,
        ))

    stage_summary.append(_purchase_price_stage(yuncang_result))

    statuses = [entry["status"] for entry in stage_summary]
    completed_stages = sum(1 for status in statuses if status == "completed")
    if "failed" in statuses:
        state = "failed"
    elif "blocked" in statuses:
        state = "partial_blocked" if completed_stages else "blocked"
    elif "purchase_price_missing" in statuses:
        state = "completed_with_warnings"
    elif all(status in {"completed"} for status in statuses):
        state = "completed"
    else:
        state = "blocked"

    blocked_items = [
        item for payload in (replicate_result, confirm_result, yuncang_result)
        for item in _payload_items(payload)
        if str(item.get("status") or "") in {"blocked", "error"}
    ]
    return {
        "state": state,
        "stage_summary": stage_summary,
        "blocked_items": blocked_items,
        "needs_manual": state in {"blocked", "partial_blocked", "failed", "completed_with_warnings"},
    }


def _with_mingkong_sync_status(payload: dict, **kwargs) -> dict:
    return {**payload, **_mingkong_sync_status_payload(**kwargs)}


def _build_mingkong_pairing_sync_response(pid: int, product: dict, body: dict):
    targets = body.get("items") if isinstance(body, dict) else []
    targets = targets if isinstance(targets, list) else []
    logs: list[dict] = [
        {"level": "info", "message": "同步明空店小秘SKU：已收到人工确认的目标计划"}
    ]

    import_payload = dianxiaomi_mingkong_pairing.build_mingkong_library_sku_import_payload(product)
    library_items = import_payload.get("items") or []
    pairs = dianxiaomi_mingkong_pairing.build_target_sku_import_pairs(
        product,
        library_items,
        targets,
    )
    pair_variants = {
        str(pair.get("shopify_variant_id") or "").strip()
        for pair in pairs
        if str(pair.get("shopify_variant_id") or "").strip()
    }
    effective_targets: list[dict] = [
        target for target in targets
        if isinstance(target, dict)
        and str(target.get("shopify_variant_id") or "").strip() in pair_variants
    ]
    if not pairs:
        message = import_payload.get("message") or "目标计划中没有可写入的 SKU 行"
        return _with_mingkong_sync_status(
            {
                "ok": False,
                "error": "missing_target_sku_rows",
                "message": message,
                "logs": logs + [
                    {"level": "error", "message": message},
                ],
                "items": [],
                "realtime_refresh": import_payload.get("realtime_refresh"),
            },
            import_payload=import_payload,
        )

    stats = medias.replace_product_skus(pid, pairs, source="mingkong_replicated")
    purchase_url = dianxiaomi_mingkong_pairing.first_purchase_url_from_targets(
        product,
        library_items,
        effective_targets,
    )
    if purchase_url:
        medias.update_product(pid, purchase_1688_url=purchase_url)
    logs.append({
        "level": "ok",
        "message": (
            "本地 SKU 已按目标计划写入："
            f"新增 {stats.get('inserted', 0)}，"
            f"更新 {stats.get('updated', 0)}，"
            f"删除 {stats.get('deleted', 0)}，"
            f"保留人工维护 {stats.get('preserved', 0)}"
        ),
    })

    product_after_import = medias.get_product(pid) or product
    local_rows = medias.list_product_skus(pid)
    try:
        replicate_result = dianxiaomi_mingkong_pairing.replicate_mingkong_skus_to_dxm03(
            product_after_import,
            local_rows,
            selections=effective_targets,
            replace_product_skus_fn=medias.replace_product_skus,
            update_product_fn=medias.update_product,
        )
    except Exception as exc:  # noqa: BLE001 - keep modal JSON-readable
        current_app.logger.exception("mingkong pairing full sync replicate failed product_id=%s", pid)
        error_payload = _mingkong_pairing_action_error_payload("复刻明空 SKU", exc)
        return _with_mingkong_sync_status(
            {
                "ok": False,
                "error": "replicate_failed",
                "message": error_payload["message"],
                "logs": logs + _stage_logs("复刻 DXM03 SKU", error_payload),
                "items": error_payload.get("items") or [],
                "import": {"stats": stats},
                "replicate": error_payload,
                "realtime_refresh": import_payload.get("realtime_refresh"),
            },
            import_payload=import_payload,
            import_stats=stats,
            replicate_result=error_payload,
        )
    logs.extend(_stage_logs("复刻 DXM03 SKU", replicate_result))
    if not replicate_result.get("ok"):
        return _with_mingkong_sync_status(
            {
                "ok": False,
                "error": "replicate_blocked",
                "message": replicate_result.get("message") or "复刻 DXM03 SKU 未完成",
                "logs": logs,
                "items": replicate_result.get("items") or [],
                "import": {"stats": stats},
                "replicate": replicate_result,
                "realtime_refresh": import_payload.get("realtime_refresh"),
            },
            import_payload=import_payload,
            import_stats=stats,
            replicate_result=replicate_result,
        )

    product_after_replicate = medias.get_product(pid) or product_after_import
    local_rows = medias.list_product_skus(pid)
    try:
        confirm_result = dianxiaomi_mingkong_pairing.confirm_dxm03_pairing(
            product_after_replicate,
            local_rows,
            selections=effective_targets,
        )
    except Exception as exc:  # noqa: BLE001 - keep modal JSON-readable
        current_app.logger.exception("mingkong pairing full sync confirm failed product_id=%s", pid)
        error_payload = _mingkong_pairing_action_error_payload("同步明空店小秘SKU", exc)
        return _with_mingkong_sync_status(
            {
                "ok": False,
                "error": "confirm_failed",
                "message": error_payload["message"],
                "logs": logs + _stage_logs("确认 DXM03 采购配对", error_payload),
                "items": error_payload.get("items") or [],
                "import": {"stats": stats},
                "replicate": replicate_result,
                "confirm": error_payload,
                "realtime_refresh": import_payload.get("realtime_refresh"),
            },
            import_payload=import_payload,
            import_stats=stats,
            replicate_result=replicate_result,
            confirm_result=error_payload,
        )
    logs.extend(_stage_logs("确认 DXM03 采购配对", confirm_result))
    if not confirm_result.get("ok"):
        yuncang_pairing_items = _successful_pairing_items_for_yuncang(confirm_result)
        if yuncang_pairing_items:
            try:
                yuncang_result = dianxiaomi_yuncang.add_product_skus_to_yuncang(
                    product_after_replicate,
                    local_rows,
                    pairing_items=yuncang_pairing_items,
                )
            except Exception as exc:  # noqa: BLE001 - keep modal JSON-readable
                current_app.logger.exception(
                    "mingkong pairing partial yuncang add failed product_id=%s",
                    pid,
                )
                error_payload = _mingkong_pairing_action_error_payload(
                    "添加 DXM03 小秘云仓商品",
                    exc,
                )
                return _with_mingkong_sync_status(
                    {
                        "ok": False,
                        "error": "yuncang_failed",
                        "message": error_payload["message"],
                        "logs": logs + _stage_logs("添加 DXM03 小秘云仓商品", error_payload),
                        "items": error_payload.get("items") or confirm_result.get("items") or [],
                        "import": {"stats": stats},
                        "replicate": replicate_result,
                        "confirm": confirm_result,
                        "yuncang": error_payload,
                        "realtime_refresh": import_payload.get("realtime_refresh"),
                    },
                    import_payload=import_payload,
                    import_stats=stats,
                    replicate_result=replicate_result,
                    confirm_result=confirm_result,
                    yuncang_result=error_payload,
                )
            logs.extend(_stage_logs("添加 DXM03 小秘云仓商品", yuncang_result))
            return _with_mingkong_sync_status(
                {
                    "ok": False,
                    "message": (
                        f"{confirm_result.get('message') or '同步明空店小秘SKU存在阻断'}；"
                        f"已对可用 SKU 执行云仓：{yuncang_result.get('message')}"
                    ),
                    "logs": logs,
                    "items": yuncang_result.get("items") or confirm_result.get("items") or [],
                    "import": {"stats": stats},
                    "replicate": replicate_result,
                    "confirm": confirm_result,
                    "yuncang": yuncang_result,
                    "realtime_refresh": import_payload.get("realtime_refresh"),
                },
                import_payload=import_payload,
                import_stats=stats,
                replicate_result=replicate_result,
                confirm_result=confirm_result,
                yuncang_result=yuncang_result,
            )
        return _with_mingkong_sync_status(
            {
                "ok": False,
                "message": confirm_result.get("message") or "同步明空店小秘SKU存在阻断",
                "logs": logs,
                "items": confirm_result.get("items") or replicate_result.get("items") or [],
                "import": {"stats": stats},
                "replicate": replicate_result,
                "confirm": confirm_result,
                "realtime_refresh": import_payload.get("realtime_refresh"),
            },
            import_payload=import_payload,
            import_stats=stats,
            replicate_result=replicate_result,
            confirm_result=confirm_result,
        )

    try:
        yuncang_result = dianxiaomi_yuncang.add_product_skus_to_yuncang(
            product_after_replicate,
            local_rows,
            pairing_items=_successful_pairing_items_for_yuncang(confirm_result)
            or confirm_result.get("items")
            or local_rows,
        )
    except Exception as exc:  # noqa: BLE001 - keep modal JSON-readable
        current_app.logger.exception("mingkong pairing yuncang add failed product_id=%s", pid)
        error_payload = _mingkong_pairing_action_error_payload("添加 DXM03 小秘云仓商品", exc)
        return _with_mingkong_sync_status(
            {
                "ok": False,
                "error": "yuncang_failed",
                "message": error_payload["message"],
                "logs": logs + _stage_logs("添加 DXM03 小秘云仓商品", error_payload),
                "items": error_payload.get("items") or [],
                "import": {"stats": stats},
                "replicate": replicate_result,
                "confirm": confirm_result,
                "yuncang": error_payload,
                "realtime_refresh": import_payload.get("realtime_refresh"),
            },
            import_payload=import_payload,
            import_stats=stats,
            replicate_result=replicate_result,
            confirm_result=confirm_result,
            yuncang_result=error_payload,
        )
    logs.extend(_stage_logs("添加 DXM03 小秘云仓商品", yuncang_result))
    ok = bool(yuncang_result.get("ok"))
    return _with_mingkong_sync_status(
        {
            "ok": ok,
            "message": (
                f"{confirm_result.get('message') or '采购配对完成'}；{yuncang_result.get('message')}"
                if ok
                else yuncang_result.get("message") or "同步明空店小秘SKU存在云仓阻断"
            ),
            "logs": logs,
            "items": yuncang_result.get("items") or confirm_result.get("items") or replicate_result.get("items") or [],
            "import": {"stats": stats},
            "replicate": replicate_result,
            "confirm": confirm_result,
            "yuncang": yuncang_result,
            "realtime_refresh": import_payload.get("realtime_refresh"),
        },
        import_payload=import_payload,
        import_stats=stats,
        replicate_result=replicate_result,
        confirm_result=confirm_result,
        yuncang_result=yuncang_result,
    )


def _build_mingkong_pairing_ai_review_response(
    pid: int,
    product: dict,
    body: dict,
    *,
    user_id: int | None,
):
    items = body.get("workbench_items") if isinstance(body, dict) else None
    if not isinstance(items, list) or not items:
        sku_rows = medias.list_product_skus(pid)
        payload = dianxiaomi_mingkong_pairing.build_workbench_payload(
            product,
            sku_rows,
            include_live=False,
            include_mingkong_reference=True,
        )
        items = payload.get("items") or []
    return mingkong_pairing_ai.review_pairing_candidates(
        product,
        items,
        user_id=user_id,
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
@permission_required("medias")
def api_mingkong_pairing_workbench(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    force = request.args.get("force") == "1"
    return routes._build_mingkong_pairing_workbench_response(pid, p, force=force)


@bp.route("/api/products/<int:pid>/mingkong-pairing/import-skus", methods=["POST"])
@login_required
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
@permission_required("medias")
def api_mingkong_pairing_replicate(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    try:
        result = routes._build_mingkong_pairing_replicate_response(pid, p, body)
    except Exception as exc:  # noqa: BLE001 - keep workbench modal JSON-readable
        current_app.logger.exception(
            "mingkong pairing replicate failed product_id=%s", pid
        )
        result = _mingkong_pairing_action_error_payload("复刻明空 SKU", exc)
        return result, 500
    status = 200 if result.get("ok") else 409
    return result, status


@bp.route("/api/products/<int:pid>/mingkong-pairing/sync", methods=["POST"])
@login_required
@permission_required("medias")
def api_mingkong_pairing_sync(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    result = routes._build_mingkong_pairing_sync_response(pid, p, body)
    status = 200 if result.get("ok") else 409
    return result, status


@bp.route("/api/products/<int:pid>/mingkong-pairing/ai-review", methods=["POST"])
@login_required
@permission_required("medias")
def api_mingkong_pairing_ai_review(pid: int):
    routes = _routes_module()
    p = medias.get_product(pid)
    if not routes._can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    result = routes._build_mingkong_pairing_ai_review_response(
        pid,
        p,
        body,
        user_id=int(current_user.id) if getattr(current_user, "id", None) else None,
    )
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
