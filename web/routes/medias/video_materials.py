from __future__ import annotations

from flask import jsonify, request
from flask_login import current_user, login_required

from appcore import media_video_materials, system_audit

from . import bp
from ._helpers import _is_admin


def _json(payload: dict, status: int = 200):
    return jsonify(payload), status


@bp.route("/api/video-materials", methods=["GET"])
@login_required
def api_video_materials():
    return _json(
        media_video_materials.list_video_materials(
            keyword=request.args.get("keyword") or request.args.get("q") or "",
            lang=request.args.get("lang") or "",
            ad_plan_status=request.args.get("ad_plan_status") or "all",
            page=request.args.get("page") or 1,
            page_size=request.args.get("page_size") or 100,
        )
    )


@bp.route("/api/video-materials/mk-search", methods=["GET"])
@login_required
def api_video_materials_mk_search():
    if not _is_admin():
        return _json({"error": "admin_required", "message": "仅管理员可搜索明空素材"}, 403)
    keyword = request.args.get("q") or request.args.get("keyword") or ""
    try:
        items = media_video_materials.search_mk_materials(
            keyword=keyword,
            limit=int(request.args.get("limit") or 50),
            page=int(request.args.get("page") or 1),
        )
    except Exception as exc:
        return _json({"error": "mk_search_failed", "message": str(exc)}, 500)
    return _json({"items": items})


@bp.route("/api/video-materials/<int:item_id>/mk-binding", methods=["POST"])
@login_required
def api_video_material_mk_binding(item_id: int):
    if not _is_admin():
        return _json({"error": "admin_required", "message": "仅管理员可绑定明空素材"}, 403)
    body = request.get_json(silent=True) or {}
    try:
        item = media_video_materials.bind_mk_material(
            media_item_id=item_id,
            mk_product_id=body.get("mk_product_id"),
            mk_product_name=body.get("mk_product_name"),
            mk_video_path=body.get("mk_video_path") or body.get("video_path") or "",
            mk_video_name=body.get("mk_video_name") or body.get("video_name") or "",
            mk_video_image_path=body.get("mk_video_image_path") or body.get("video_image_path") or "",
            mk_video_metadata=body.get("mk_video_metadata") or body.get("video_metadata") or {},
            bound_by=getattr(current_user, "id", None),
        )
    except ValueError as exc:
        return _json({"error": "invalid_binding", "message": str(exc)}, 400)
    except Exception as exc:
        return _json({"error": "binding_failed", "message": str(exc)}, 500)

    system_audit.record_from_request(
        user=current_user,
        request_obj=request,
        action="media_item_mk_bound",
        module="medias",
        target_type="media_item",
        target_id=item_id,
        target_label=item.get("filename") or str(item_id),
        detail={
            "mk_product_id": body.get("mk_product_id"),
            "mk_video_path": body.get("mk_video_path") or body.get("video_path") or "",
        },
    )
    return _json({"ok": True, "item": item})
