"""推送管理 Blueprint。列表 + 推送工作流 API。"""
from __future__ import annotations

import logging
from functools import wraps

import requests
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user

import config

log = logging.getLogger(__name__)
bp = Blueprint("pushes", __name__, url_prefix="/pushes")


def _is_admin() -> bool:
    return getattr(current_user, "is_admin", False)


def admin_required(fn):
    @wraps(fn)
    def _wrap(*a, **kw):
        if not _is_admin():
            return jsonify({"error": "仅管理员可操作"}), 403
        return fn(*a, **kw)
    return _wrap


@bp.route("/")
@login_required
def index():
    return render_template(
        "pushes_list.html",
        is_admin=_is_admin(),
        active="list",
    )


from appcore import medias, pushes

_PAGE_SIZE_DEFAULT = 20


def _serialize_ai_score(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _item_cover_url(item_id: int, item: dict) -> str | None:
    if (item or {}).get("cover_object_key"):
        return f"/medias/item-cover/{item_id}"
    return None


def _serialize_row(row: dict) -> dict:
    item_shape = dict(row)
    product_shape = {
        "id": row.get("product_id"),
        "name": row.get("product_name"),
        "product_code": row.get("product_code"),
        "localized_links_json": row.get("localized_links_json"),
        "ad_supported_langs": row.get("ad_supported_langs"),
        "shopify_image_status_json": row.get("shopify_image_status_json"),
        "selling_points": row.get("selling_points"),
        "importance": row.get("importance"),
        "remark": row.get("remark"),
        "ai_score": row.get("ai_score"),
        "ai_evaluation_result": row.get("ai_evaluation_result"),
        "ai_evaluation_detail": row.get("ai_evaluation_detail"),
        "listing_status": row.get("listing_status"),
    }
    readiness = pushes.compute_readiness(item_shape, product_shape)
    status = pushes.compute_status(item_shape, product_shape)
    item_id = row["id"]
    cover_url = _item_cover_url(item_id, row)
    return {
        "id": item_id,
        "product_id": row["product_id"],
        "product_name": row.get("product_name"),
        "product_code": row.get("product_code"),
        "product_owner_name": row.get("owner_name") or "",
        "mk_id": row.get("mk_id"),
        "product_page_url": pushes.resolve_product_page_url(
            row.get("lang") or "en",
            product_shape,
        ),
        "lang": row.get("lang"),
        "filename": row.get("filename"),
        "display_name": row.get("display_name"),
        "duration_seconds": row.get("duration_seconds"),
        "file_size": row.get("file_size"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "pushed_at": row["pushed_at"].isoformat() if row.get("pushed_at") else None,
        "status": status,
        "readiness": readiness,
        "remark": row.get("remark") or "",
        "ai_score": _serialize_ai_score(row.get("ai_score")),
        "ai_evaluation_result": row.get("ai_evaluation_result") or "",
        "ai_evaluation_detail": row.get("ai_evaluation_detail") or "",
        "listing_status": row.get("listing_status") or "上架",
        "cover_url": cover_url,
    }


@bp.route("/api/items", methods=["GET"])
@login_required
def api_list():
    status_filter = [s for s in request.args.getlist("status") if s]
    langs = [l for l in request.args.getlist("lang") if l]
    keyword = (request.args.get("keyword") or "").strip()
    product_term = (request.args.get("product") or "").strip()
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None

    page = max(1, int(request.args.get("page") or 1))
    limit = _PAGE_SIZE_DEFAULT

    # 状态由 Python 计算而非 SQL，因此先取全量行、在内存过滤后再分页，
    # 避免"前 N 行都不符合状态"导致页面显示空数据但 total 正常的错觉。
    rows, _ = pushes.list_items_for_push(
        langs=langs or None,
        keyword=keyword,
        product_term=product_term,
        date_from=date_from,
        date_to=date_to,
        offset=0,
        limit=None,
    )
    items = [_serialize_row(r) for r in rows]
    if status_filter:
        items = [it for it in items if it["status"] in status_filter]

    total = len(items)
    start = (page - 1) * limit
    page_items = items[start:start + limit]

    return jsonify({
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": limit,
    })


@bp.route("/api/items/<int:item_id>/payload", methods=["GET"])
@login_required
@admin_required
def api_build_payload(item_id: int):
    item = medias.get_item(item_id)
    if not item:
        return jsonify({"error": "item_not_found"}), 404
    product = medias.get_product(item["product_id"])
    if not product:
        return jsonify({"error": "product_not_found"}), 404
    if item.get("pushed_at"):
        return jsonify({"error": "already_pushed"}), 409

    readiness = pushes.compute_readiness(item, product)
    if not pushes.is_ready(readiness):
        missing = [k for k, v in readiness.items() if not v]
        return jsonify({"error": "not_ready", "missing": missing}), 400

    lang = item.get("lang") or "en"
    product_code = (product.get("product_code") or "").strip().lower()
    ad_url = pushes.build_product_link(lang, product_code)
    ok, err = pushes.probe_ad_url(ad_url)
    if not ok:
        return jsonify({
            "error": "link_not_adapted",
            "url": ad_url,
            "detail": err,
        }), 400

    payload = pushes.build_item_payload(item, product)
    mk_id = product.get("mk_id")
    localized_text = pushes.resolve_localized_text_payload(item)
    localized_texts_request = pushes.build_localized_texts_request(item)
    preview_cover_url = _item_cover_url(item_id, item)
    return jsonify({
        "payload": payload,
        "push_url": pushes.get_push_target_url(),
        "mk_id": mk_id,
        "localized_text": localized_text,
        "localized_texts_request": localized_texts_request,
        "localized_push_target_url": pushes.build_localized_texts_target_url(mk_id),
        "preview_cover_url": preview_cover_url,
    })


@bp.route("/api/items/<int:item_id>/push", methods=["POST"])
@login_required
@admin_required
def api_push(item_id: int):
    """推送入口：进程内组装 payload + 写日志/状态，只对下游外部系统发一次 HTTP。"""
    push_url = pushes.get_push_target_url()
    if not push_url:
        return jsonify({"error": "push_target_not_configured"}), 500

    item = medias.get_item(item_id)
    if not item:
        return jsonify({"error": "item_not_found"}), 404
    product = medias.get_product(item["product_id"])
    if not product:
        return jsonify({"error": "product_not_found"}), 404
    if item.get("pushed_at"):
        return jsonify({"error": "already_pushed"}), 409

    readiness = pushes.compute_readiness(item, product)
    if not pushes.is_ready(readiness):
        missing = [k for k, v in readiness.items() if not v]
        return jsonify({"error": "not_ready", "missing": missing}), 400

    lang = item.get("lang") or "en"
    product_code = (product.get("product_code") or "").strip().lower()
    ad_url = pushes.build_product_link(lang, product_code)
    ok, err = pushes.probe_ad_url(ad_url)
    if not ok:
        return jsonify({"error": "link_not_adapted", "url": ad_url, "detail": err}), 400

    try:
        payload = pushes.build_item_payload(item, product)
    except pushes.ProductNotListedError as exc:
        return jsonify({"error": "product_not_listed", "detail": str(exc)}), 409
    except (pushes.CopywritingMissingError, pushes.CopywritingParseError) as exc:
        return jsonify({"error": "copywriting_invalid", "detail": str(exc)}), 400

    try:
        resp = requests.post(
            push_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        pushes.record_push_failure(
            item_id=item_id,
            operator_user_id=current_user.id,
            payload=payload,
            error_message=f"network_error: {exc}",
            response_body=None,
        )
        return jsonify({"error": "downstream_unreachable", "detail": str(exc)}), 502

    body_text = resp.text or ""
    if resp.ok:
        pushes.record_push_success(
            item_id=item_id,
            operator_user_id=current_user.id,
            payload=payload,
            response_body=body_text,
        )

        # 推送成功后，回填 mk_id（失败不阻塞主响应，只附在 mk_id_match 里告诉前端）
        mk_id_match: dict[str, Any] = {"status": "skipped", "mk_id": None}
        try:
            matched_mk_id, status = pushes.lookup_mk_id(product_code)
        except Exception as exc:  # defensive — 任何异常都不能破坏推送成功响应
            log.warning("lookup_mk_id unexpected error: %s", exc)
            matched_mk_id, status = None, "request_failed"

        mk_id_match["status"] = status
        mk_id_match["mk_id"] = matched_mk_id
        if matched_mk_id:
            try:
                medias.update_product(product["id"], mk_id=int(matched_mk_id))
            except Exception as exc:
                # 唯一键冲突（已被其他产品占用）或别的 DB 错误
                log.warning("update_product mk_id failed: %s", exc)
                mk_id_match["status"] = "db_conflict"
                mk_id_match["detail"] = str(exc)
            # 返回新 mk_id 对应的 wedev target_url，让前端刷新"推送小语种文案"胶囊
            mk_id_match["localized_push_target_url"] = pushes.build_localized_texts_target_url(
                int(matched_mk_id)
            )

        return jsonify({
            "ok": True,
            "upstream_status": resp.status_code,
            "response_body": body_text[:4000],
            "mk_id_match": mk_id_match,
        })

    pushes.record_push_failure(
        item_id=item_id,
        operator_user_id=current_user.id,
        payload=payload,
        error_message=f"HTTP {resp.status_code}",
        response_body=body_text,
    )
    return jsonify({
        "error": "downstream_error",
        "upstream_status": resp.status_code,
        "response_body": body_text[:4000],
    }), 502


@bp.route("/api/items/<int:item_id>/mark-pushed", methods=["POST"])
@login_required
@admin_required
def api_mark_pushed(item_id: int):
    body = request.get_json(silent=True) or {}
    payload = body.get("request_payload") or {}
    response_body = body.get("response_body")
    pushes.record_push_success(
        item_id=item_id,
        operator_user_id=current_user.id,
        payload=payload,
        response_body=response_body,
    )
    return ("", 204)


@bp.route("/api/items/<int:item_id>/mark-failed", methods=["POST"])
@login_required
@admin_required
def api_mark_failed(item_id: int):
    body = request.get_json(silent=True) or {}
    payload = body.get("request_payload") or {}
    pushes.record_push_failure(
        item_id=item_id,
        operator_user_id=current_user.id,
        payload=payload,
        error_message=body.get("error_message"),
        response_body=body.get("response_body"),
    )
    return ("", 204)


@bp.route("/api/items/<int:item_id>/reset", methods=["POST"])
@login_required
@admin_required
def api_reset(item_id: int):
    pushes.reset_push_state(item_id)
    return ("", 204)


@bp.route("/api/items/<int:item_id>/logs", methods=["GET"])
@login_required
def api_logs(item_id: int):
    logs = pushes.list_item_logs(item_id)
    serialized = []
    for row in logs:
        serialized.append({
            "id": row["id"],
            "operator_user_id": row["operator_user_id"],
            "status": row["status"],
            "request_payload": row["request_payload"],
            "response_body": row["response_body"],
            "error_message": row["error_message"],
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        })
    return jsonify({"logs": serialized})


# ================================================================
# 任务统计 Tab：/pushes/stats（页面） + /pushes/api/stats（JSON）
# 仅 admin。
# ================================================================


@bp.route("/stats")
@login_required
@admin_required
def stats():
    return render_template(
        "pushes_stats.html",
        is_admin=True,
        active="stats",
    )


@bp.route("/api/stats", methods=["GET"])
@login_required
@admin_required
def api_stats():
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None
    try:
        result = pushes.aggregate_stats_by_owner(date_from, date_to)
    except ValueError as exc:
        return jsonify({"error": "invalid_date_range", "detail": str(exc)}), 400
    return jsonify(result)


# ================================================================
# 小语种文案推送：进程内组装 → 一次 HTTP POST 到 wedev
# ================================================================


@bp.route("/api/items/<int:item_id>/push-localized-texts", methods=["POST"])
@login_required
@admin_required
def api_push_localized_texts(item_id: int):
    item = medias.get_item(item_id)
    if not item:
        return jsonify({"error": "item_not_found"}), 404
    product = medias.get_product(item["product_id"])
    if not product:
        return jsonify({"error": "product_not_found"}), 404
    if not medias.is_product_listed(product):
        return jsonify({"error": "product_not_listed"}), 409
    mk_id = product.get("mk_id")
    if not mk_id:
        return jsonify({"error": "mk_id_missing", "detail": "产品缺少 mk_id"}), 400

    target_url = pushes.build_localized_texts_target_url(mk_id)
    if not target_url:
        return jsonify({"error": "push_localized_texts_base_url_missing"}), 500

    headers = pushes.build_localized_texts_headers()
    if "Authorization" not in headers and "Cookie" not in headers:
        return jsonify({"error": "push_localized_texts_credentials_missing"}), 500

    body = pushes.build_localized_texts_request(item)
    if not body.get("texts"):
        return jsonify({"error": "localized_texts_empty"}), 400

    try:
        resp = requests.post(target_url, json=body, headers=headers, timeout=30)
    except requests.RequestException as exc:
        return jsonify({
            "error": "downstream_unreachable",
            "detail": str(exc),
            "target_url": target_url,
        }), 502

    body_text = resp.text or ""
    if resp.ok:
        return jsonify({
            "ok": True,
            "upstream_status": resp.status_code,
            "response_body": body_text[:4000],
            "target_url": target_url,
        })
    return jsonify({
        "error": "downstream_error",
        "upstream_status": resp.status_code,
        "response_body": body_text[:4000],
        "target_url": target_url,
    }), 502


# ================================================================
# 推送凭据读写（admin only）
# 在 /settings?tab=push 页面维护；或通过 tools/wedev_sync.py 自动同步。
# ================================================================


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 12:
        return "*" * len(value)
    return f"{value[:6]}…{value[-4:]}  (len={len(value)})"


@bp.route("/api/push-credentials", methods=["GET"])
@login_required
@admin_required
def api_get_push_credentials():
    """返回当前凭据（auth / cookie 脱敏）+ 目标地址。"""
    auth = pushes.get_localized_texts_authorization()
    cookie = pushes.get_localized_texts_cookie()
    return jsonify({
        "push_target_url": pushes.get_push_target_url(),
        "push_localized_texts_base_url": pushes.get_localized_texts_base_url(),
        "push_localized_texts_authorization_masked": _mask_secret(auth),
        "push_localized_texts_authorization_present": bool(auth),
        "push_localized_texts_cookie_masked": _mask_secret(cookie),
        "push_localized_texts_cookie_present": bool(cookie),
    })


_ALLOWED_PUSH_SETTING_KEYS = {
    "push_target_url",
    "push_localized_texts_base_url",
    "push_localized_texts_authorization",
    "push_localized_texts_cookie",
}


@bp.route("/api/push-credentials", methods=["POST"])
@login_required
@admin_required
def api_set_push_credentials():
    """admin 保存凭据。支持部分更新：未传或空字符串的键不会覆盖（除非显式带 clear=true）。"""
    from appcore.settings import set_setting
    body = request.get_json(silent=True) or {}
    clear_flags = body.get("clear") or {}
    if not isinstance(clear_flags, dict):
        clear_flags = {}

    updated: list[str] = []
    for key in _ALLOWED_PUSH_SETTING_KEYS:
        if key in body:
            value = (body.get(key) or "").strip()
            if value or clear_flags.get(key):
                set_setting(key, value)
                updated.append(key)
    return jsonify({"ok": True, "updated": updated})
