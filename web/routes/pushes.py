"""推送管理 Blueprint。列表 + 推送工作流 API。"""
from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user

import config

log = logging.getLogger(__name__)
bp = Blueprint("pushes", __name__, url_prefix="/pushes")


def _is_admin() -> bool:
    return getattr(current_user, "role", "") == "admin"


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
    )


from appcore import medias, pushes, tos_clients

_PAGE_SIZE_DEFAULT = 20


def _serialize_row(row: dict) -> dict:
    item_shape = dict(row)
    product_shape = {
        "id": row.get("product_id"),
        "name": row.get("product_name"),
        "product_code": row.get("product_code"),
        "ad_supported_langs": row.get("ad_supported_langs"),
        "selling_points": row.get("selling_points"),
        "importance": row.get("importance"),
    }
    readiness = pushes.compute_readiness(item_shape, product_shape)
    status = pushes.compute_status(item_shape, product_shape)
    cover_key = row.get("cover_object_key")
    return {
        "id": row["id"],
        "product_id": row["product_id"],
        "product_name": row.get("product_name"),
        "product_code": row.get("product_code"),
        "lang": row.get("lang"),
        "filename": row.get("filename"),
        "display_name": row.get("display_name"),
        "duration_seconds": row.get("duration_seconds"),
        "file_size": row.get("file_size"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "pushed_at": row["pushed_at"].isoformat() if row.get("pushed_at") else None,
        "status": status,
        "readiness": readiness,
        "cover_url": (
            tos_clients.generate_signed_media_download_url(cover_key) if cover_key else None
        ),
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
    return jsonify({
        "payload": payload,
        "push_url": config.PUSH_TARGET_URL,
    })


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
