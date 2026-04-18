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
        push_target_configured=bool((config.PUSH_TARGET_URL or "").strip()),
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
    status_filter = request.args.getlist("status")
    langs = [l for l in request.args.getlist("lang") if l]
    keyword = (request.args.get("keyword") or "").strip()
    product_term = (request.args.get("product") or "").strip()
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None

    page = max(1, int(request.args.get("page") or 1))
    limit = _PAGE_SIZE_DEFAULT

    rows, total = pushes.list_items_for_push(
        langs=langs or None,
        keyword=keyword,
        product_term=product_term,
        date_from=date_from,
        date_to=date_to,
        offset=(page - 1) * limit,
        limit=limit,
    )
    items = [_serialize_row(r) for r in rows]
    if status_filter:
        items = [it for it in items if it["status"] in status_filter]

    return jsonify({
        "items": items,
        "total": total,
        "page": page,
        "page_size": limit,
    })
