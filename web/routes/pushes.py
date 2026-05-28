"""推送管理 Blueprint。列表 + 推送工作流 API。"""
from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, render_template, request
from flask_login import login_required, current_user

from web.auth import permission_required

import config
from web.services.pushes_responses import (
    build_pushes_payload_response,
    pushes_flask_response,
)

log = logging.getLogger(__name__)
bp = Blueprint("pushes", __name__, url_prefix="/pushes")


def _json_response(payload, status_code: int = 200):
    return pushes_flask_response(build_pushes_payload_response(payload, status_code))


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _manual_link_confirmed() -> bool:
    if request.method == "GET":
        return _truthy(request.args.get("manual_link_confirmed"))
    body = request.get_json(silent=True) or {}
    return _truthy(body.get("manual_link_confirmed"))


def _is_admin() -> bool:
    return getattr(current_user, "is_admin", False)


def admin_required(fn):
    @wraps(fn)
    def _wrap(*a, **kw):
        if not _is_admin():
            return _json_response({"error": "仅管理员可操作"}, 403)
        return fn(*a, **kw)
    return _wrap


def _product_links_push_error_response(exc: Exception):
    message = str(exc)
    if isinstance(exc, pushes.ProductNotListedError):
        return _json_response({"error": "product_not_listed", "message": "产品已下架，不能推送投放链接"}, 409)
    if isinstance(exc, pushes.ProductLinksPushConfigError):
        return _json_response({"error": message or "push_product_links_config_missing"}, 500)
    if isinstance(exc, pushes.ProductLinksPayloadError):
        return _json_response({"error": message or "product_links_payload_invalid"}, 400)
    return _json_response({"error": "product_links_push_failed", "message": message}, 500)


@bp.route("/")
@login_required
@permission_required("pushes")
def index():
    return render_template(
        "pushes_list.html",
        is_admin=_is_admin(),
        active="list",
    )


from appcore import medias, push_quality_checks, pushes, system_audit
from appcore import tasks as tasks_svc
from appcore.db import query_one

_PAGE_SIZE_DEFAULT = 20
_AUDIT_RESULT_FILTERS = {"适合推广", "部分适合推广", "不适合推广"}
_VALID_STATUS_FILTERS = {"not_ready", "pending", "pushed", "failed", "skipped"}


def _audit_push_action(
    item_id: int | None,
    action: str,
    *,
    target_type: str = "media_item",
    status: str = "success",
    detail: dict | None = None,
) -> None:
    system_audit.record_from_request(
        user=current_user,
        request_obj=request,
        action=action,
        module="pushes",
        target_type=target_type,
        target_id=item_id,
        status=status,
        detail=detail,
    )


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


def _positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _resolve_rework_task_id(item: dict) -> int | None:
    task_id = _positive_int((item or {}).get("task_id"))
    if task_id is not None:
        return task_id
    product_id = _positive_int((item or {}).get("product_id"))
    if product_id is None:
        return None
    lang = str((item or {}).get("lang") or "en").strip().lower() or "en"
    if lang == "en":
        return None
    try:
        inferred = tasks_svc.infer_single_child_task_id_for_media_item(product_id, lang)
        if inferred is not None:
            return inferred
        # Fallback for multiple matching tasks (ambiguity). We should reject to the latest task
        # matching product and language rather than disabling the rework button completely.
        row = query_one(
            "SELECT id FROM tasks "
            "WHERE media_product_id=%s "
            "AND LOWER(TRIM(COALESCE(country_code, '')))=%s "
            "AND parent_task_id IS NOT NULL "
            "AND status IN ('assigned', 'review', 'done') "
            "ORDER BY id DESC LIMIT 1",
            (product_id, lang),
        )
        if row:
            return _positive_int(row.get("id"))
    except Exception:
        log.debug(
            "infer rework task id failed product_id=%s lang=%s",
            product_id,
            lang,
            exc_info=True,
        )
    source_raw_id = _positive_int((item or {}).get("source_raw_id"))
    if source_raw_id is None and (item or {}).get("auto_translated"):
        source_raw_id = _positive_int((item or {}).get("source_ref_id"))
    if source_raw_id is not None:
        try:
            task_id = tasks_svc.infer_single_child_task_id_from_raw_source(
                product_id,
                lang,
                source_raw_id,
            )
            if task_id is not None:
                return task_id
        except Exception:
            log.debug(
                "infer rework task id from raw source failed product_id=%s lang=%s source_raw_id=%s",
                product_id,
                lang,
                source_raw_id,
                exc_info=True,
            )
    try:
        return tasks_svc.latest_child_task_id_for_media_item(product_id, lang)
    except Exception:
        log.debug(
            "infer latest rework task id failed product_id=%s lang=%s",
            product_id,
            lang,
            exc_info=True,
        )
        return None


def _quality_check_for_item(item_id: int) -> dict | None:
    try:
        return push_quality_checks.latest_for_item(item_id)
    except Exception:
        log.debug("load push quality check failed item_id=%s", item_id, exc_info=True)
        return None


def _compute_readiness_for_list(
    item_shape: dict,
    product_shape: dict,
    context: dict | None,
) -> dict:
    try:
        return pushes.compute_readiness(item_shape, product_shape, context=context)
    except TypeError as exc:
        if "context" not in str(exc):
            raise
        return pushes.compute_readiness(item_shape, product_shape)


def _serialize_row(
    row: dict,
    *,
    context: dict | None = None,
    status_cache: dict | None = None,
) -> dict:
    item_shape = dict(row)
    rework_task_id = _resolve_rework_task_id(item_shape)
    if rework_task_id is not None and not item_shape.get("task_id"):
        item_shape["task_id"] = rework_task_id
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
    cached_readiness = (status_cache or {}).get("readiness")
    cached_status = (status_cache or {}).get("status")
    if isinstance(cached_readiness, dict) and cached_status:
        readiness = dict(cached_readiness)
        status = str(cached_status)
    else:
        readiness = _compute_readiness_for_list(item_shape, product_shape, context)
        status = pushes.compute_status_from_readiness(
            item_shape,
            product_shape,
            readiness,
            context=context,
        )
    item_id = row["id"]
    cover_url = _item_cover_url(item_id, row)
    return {
        "id": item_id,
        "task_id": rework_task_id,
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
        "skip_push": bool(row.get("skip_push")),
        "skip_push_at": row["skip_push_at"].isoformat() if row.get("skip_push_at") else None,
    }


@bp.route("/api/items", methods=["GET"])
@login_required
def api_list():
    status_filter = [s for s in request.args.getlist("status") if s]
    langs = [l for l in request.args.getlist("lang") if l]
    keyword = (request.args.get("keyword") or "").strip()
    product_term = (request.args.get("product") or "").strip()
    owner_id_raw = (request.args.get("owner_id") or "").strip()
    audit_result = (request.args.get("audit_result") or "").strip()
    if audit_result not in _AUDIT_RESULT_FILTERS:
        audit_result = ""
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None
    sort = (request.args.get("sort") or "created_at_desc").strip()
    if sort not in {"created_at_asc", "created_at_desc"}:
        sort = "created_at_desc"

    owner_id = None
    if owner_id_raw:
        try:
            owner_id = int(owner_id_raw)
        except ValueError:
            return _json_response({"error": "invalid_owner_id"}, 400)

    page = max(1, int(request.args.get("page") or 1))
    limit = _PAGE_SIZE_DEFAULT

    # 状态由 Python 计算而非 SQL，因此先取全量行、在内存过滤后再分页，
    # 避免"前 N 行都不符合状态"导致页面显示空数据但 total 正常的错觉。
    rows, _ = pushes.list_items_for_push(
        langs=langs or None,
        keyword=keyword,
        product_term=product_term,
        owner_id=owner_id,
        audit_result=audit_result,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        offset=0,
        limit=None,
    )
    status_cache_by_item_id = pushes.status_cache_for_rows(rows)
    missing_cache_rows = [
        r for r in rows if not status_cache_by_item_id.get(int(r.get("id") or 0))
    ]
    context = pushes.build_push_list_context(missing_cache_rows) if missing_cache_rows else None
    items = [
        _serialize_row(
            r,
            context=context,
            status_cache=status_cache_by_item_id.get(int(r.get("id") or 0)),
        )
        for r in rows
    ]
    if status_filter:
        valid = [s for s in status_filter if s in _VALID_STATUS_FILTERS]
        if valid:
            items = [it for it in items if it["status"] in valid]

    total = len(items)
    start = (page - 1) * limit
    page_items = items[start:start + limit]

    return _json_response({
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
        return _json_response({"error": "item_not_found"}, 404)
    product = medias.get_product(item["product_id"])
    if not product:
        return _json_response({"error": "product_not_found"}, 404)
    readiness = pushes.compute_readiness(item, product)
    if not pushes.is_ready(readiness):
        missing = [k for k, v in readiness.items() if not v]
        return _json_response({"error": "not_ready", "missing": missing}, 400)

    lang = item.get("lang") or "en"
    product_code = (product.get("product_code") or "").strip().lower()
    ad_url = pushes.build_product_link(lang, product_code)
    manual_link_confirmed = _manual_link_confirmed()
    if not manual_link_confirmed:
        ok, err = pushes.probe_ad_url(ad_url)
        if not ok:
            return _json_response({
                "error": "link_not_adapted",
                "url": ad_url,
                "detail": err,
            }, 400)

    payload = pushes.build_item_payload(item, product)
    mk_id = product.get("mk_id")
    localized_text = pushes.resolve_localized_text_payload(item)
    localized_texts_request = pushes.build_localized_texts_request(item)
    preview_cover_url = _item_cover_url(item_id, item)
    try:
        product_links_push = pushes.build_product_links_push_preview(product)
    except Exception as exc:
        product_links_push = {
            "error": type(exc).__name__,
            "message": str(exc),
            "target_url": "",
            "payload": None,
            "links": [],
        }
    return _json_response({
        "payload": payload,
        "push_url": pushes.get_push_target_url(),
        "mk_id": mk_id,
        "localized_text": localized_text,
        "localized_texts_request": localized_texts_request,
        "localized_push_target_url": pushes.build_localized_texts_target_url(mk_id),
        "product_links_push": product_links_push,
        "preview_cover_url": preview_cover_url,
        "quality_check": _quality_check_for_item(item_id),
        "manual_link_confirmed": manual_link_confirmed,
    })


@bp.route("/api/items/<int:item_id>/reject-to-task", methods=["POST"])
@login_required
@admin_required
def api_reject_to_task(item_id: int):
    item = medias.get_item(item_id)
    if not item:
        return _json_response({"error": "item_not_found"}, 404)
    linked_task_id = _positive_int(item.get("task_id"))
    task_id = _resolve_rework_task_id(item)
    if not task_id:
        return _json_response({"error": "task_not_linked"}, 400)

    body = request.get_json(silent=True) or {}
    issue_keys = body.get("issue_keys") or body.get("issues") or []
    reason = str(body.get("reason") or "").strip()
    image_urls = body.get("image_urls") or []
    if linked_task_id is None:
        medias.update_item_task_id(item_id, int(task_id))
    try:
        result = tasks_svc.reject_child_from_push(
            task_id=int(task_id),
            actor_user_id=int(current_user.id),
            issue_keys=issue_keys,
            reason=reason,
            image_urls=image_urls,
        )
    except ValueError as exc:
        return _json_response({"error": "invalid_request", "message": str(exc)}, 400)
    except tasks_svc.StateError as exc:
        return _json_response({"error": "task_state_error", "message": str(exc)}, 409)

    _audit_push_action(
        item_id,
        "push_rework_rejected",
        detail={
            "task_id": int(task_id),
            "issue_keys": result.get("issue_keys") or [],
            "reason": reason,
            "image_urls": image_urls,
        },
    )
    try:
        pushes.refresh_push_status_cache_for_item(item_id)
    except Exception:
        log.debug("refresh push status cache failed item_id=%s", item_id, exc_info=True)
    return _json_response(result)


@bp.route("/api/items/<int:item_id>/upload-rework-screenshot", methods=["POST"])
@login_required
@admin_required
def api_upload_rework_screenshot(item_id: int):
    import os
    import uuid
    from pathlib import Path
    
    item = medias.get_item(item_id)
    if not item:
        return _json_response({"error": "item_not_found"}, 404)
        
    if "file" not in request.files:
        return _json_response({"error": "no_file_uploaded"}, 400)
        
    file = request.files["file"]
    if not file or not file.filename:
        return _json_response({"error": "empty_file"}, 400)
        
    from web.upload_util import validate_image_extension, save_uploaded_file_to_path
    if not validate_image_extension(file.filename):
        return _json_response({"error": "invalid_file_type", "message": "Only image files are allowed"}, 400)
        
    ext = os.path.splitext(file.filename)[1].lower()
    random_filename = f"{uuid.uuid4().hex}{ext}"
    
    screenshots_dir = Path(config.UPLOAD_DIR) / "rework_screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    
    target_path = screenshots_dir / random_filename
    save_uploaded_file_to_path(file, target_path)
    
    url = f"/pushes/api/rework-screenshot/{random_filename}"
    return _json_response({"url": url})


@bp.route("/api/rework-screenshot/<filename>", methods=["GET"])
@login_required
def api_get_rework_screenshot(filename: str):
    import os
    from werkzeug.utils import secure_filename
    from flask import send_from_directory
    
    safe_name = secure_filename(filename)
    if not safe_name or safe_name != filename:
        return _json_response({"error": "invalid_filename"}, 400)
        
    screenshots_dir = os.path.join(config.UPLOAD_DIR, "rework_screenshots")
    return send_from_directory(screenshots_dir, safe_name)


@bp.route("/api/items/<int:item_id>/quality-check/retry", methods=["POST"])
@login_required
@admin_required
def api_retry_quality_check(item_id: int):
    result = push_quality_checks.evaluate_item(item_id, source="manual")
    status = 200 if result.get("status") != "error" else 500
    _audit_push_action(
        item_id,
        "push_quality_check_retried",
        status="success" if status == 200 else "failed",
        detail={"result_status": result.get("status")},
    )
    return _json_response(result, status)


@bp.route("/api/items/<int:item_id>/push", methods=["POST"])
@login_required
@admin_required
def api_push(item_id: int):
    """推送入口：进程内组装 payload + 写日志/状态，只对下游外部系统发一次 HTTP。"""
    push_url = pushes.get_push_target_url()
    if not push_url:
        return _json_response({"error": "push_target_not_configured"}, 500)

    item = medias.get_item(item_id)
    if not item:
        return _json_response({"error": "item_not_found"}, 404)
    product = medias.get_product(item["product_id"])
    if not product:
        return _json_response({"error": "product_not_found"}, 404)
    if item.get("pushed_at"):
        return _json_response({"error": "already_pushed"}, 409)

    readiness = pushes.compute_readiness(item, product)
    if not pushes.is_ready(readiness):
        missing = [k for k, v in readiness.items() if not v]
        return _json_response({"error": "not_ready", "missing": missing}, 400)

    lang = item.get("lang") or "en"
    product_code = (product.get("product_code") or "").strip().lower()
    ad_url = pushes.build_product_link(lang, product_code)
    manual_link_confirmed = _manual_link_confirmed()
    if not manual_link_confirmed:
        ok, err = pushes.probe_ad_url(ad_url)
        if not ok:
            return _json_response({"error": "link_not_adapted", "url": ad_url, "detail": err}, 400)

    try:
        payload = pushes.build_item_payload(item, product)
    except pushes.ProductNotListedError as exc:
        return _json_response({"error": "product_not_listed", "detail": str(exc)}, 409)
    except (pushes.CopywritingMissingError, pushes.CopywritingParseError) as exc:
        return _json_response({"error": "copywriting_invalid", "detail": str(exc)}, 400)

    post_result = pushes.post_json_payload(
        push_url,
        payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    if post_result.get("error") == "downstream_unreachable":
        detail = str(post_result.get("detail") or "")
        pushes.record_push_failure(
            item_id=item_id,
            operator_user_id=current_user.id,
            payload=payload,
            error_message=f"network_error: {detail}",
            response_body=None,
        )
        _audit_push_action(
            item_id,
            "push_failed",
            status="failed",
            detail={
                "error": "downstream_unreachable",
                "manual_link_confirmed": manual_link_confirmed,
                "product_link_url": ad_url,
            },
        )
        return _json_response({"error": "downstream_unreachable", "detail": detail}, 502)

    body_text = str(post_result.get("response_body_full") or "")
    if post_result.get("ok"):
        pushes.record_push_success(
            item_id=item_id,
            operator_user_id=current_user.id,
            payload=payload,
            response_body=body_text,
        )
        _audit_push_action(
            item_id,
            "push_succeeded",
            detail={
                "upstream_status": post_result.get("upstream_status"),
                "manual_link_confirmed": manual_link_confirmed,
                "product_link_url": ad_url,
            },
        )
        task_id = item.get("task_id")
        if task_id:
            try:
                tasks_svc.record_push_material_approved(
                    task_id=int(task_id),
                    actor_user_id=int(current_user.id),
                    item_id=int(item_id),
                    product_code=product_code,
                    lang=lang,
                    upstream_status=post_result.get("upstream_status"),
                )
            except Exception:
                log.warning(
                    "record task push approved event failed item_id=%s task_id=%s",
                    item_id,
                    task_id,
                    exc_info=True,
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

        return _json_response({
            "ok": True,
            "upstream_status": post_result.get("upstream_status"),
            "response_body": post_result.get("response_body") or "",
            "mk_id_match": mk_id_match,
            "manual_link_confirmed": manual_link_confirmed,
        })

    pushes.record_push_failure(
        item_id=item_id,
        operator_user_id=current_user.id,
        payload=payload,
        error_message=f"HTTP {post_result.get('upstream_status')}",
        response_body=body_text,
    )
    _audit_push_action(
        item_id,
        "push_failed",
        status="failed",
        detail={
            "upstream_status": post_result.get("upstream_status"),
            "manual_link_confirmed": manual_link_confirmed,
            "product_link_url": ad_url,
        },
    )
    return _json_response({
        "error": "downstream_error",
        "upstream_status": post_result.get("upstream_status"),
        "response_body": post_result.get("response_body") or "",
    }, 502)


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
    _audit_push_action(item_id, "push_marked_succeeded")
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
    _audit_push_action(
        item_id,
        "push_marked_failed",
        status="failed",
        detail={"error_message": body.get("error_message")},
    )
    return ("", 204)


@bp.route("/api/items/<int:item_id>/reset", methods=["POST"])
@login_required
@admin_required
def api_reset(item_id: int):
    pushes.reset_push_state(item_id)
    _audit_push_action(item_id, "push_reset")
    return ("", 204)


@bp.route("/api/cache/clear", methods=["POST"])
@login_required
@admin_required
def api_clear_cache():
    from appcore.db import execute
    execute("DELETE FROM media_push_status_cache")
    _audit_push_action(None, "push_cache_cleared")
    return _json_response({"ok": True})


@bp.route("/api/items/<int:item_id>/skip", methods=["POST"])
@login_required
@admin_required
def api_skip(item_id: int):
    item = medias.get_item(item_id)
    if not item:
        return _json_response({"error": "item_not_found"}, 404)
    if item.get("pushed_at"):
        return _json_response({"error": "already_pushed"}, 409)
    pushes.mark_skip_push(item_id, current_user.id)
    _audit_push_action(item_id, "push_skipped")
    return ("", 204)


@bp.route("/api/items/<int:item_id>/unskip", methods=["POST"])
@login_required
@admin_required
def api_unskip(item_id: int):
    item = medias.get_item(item_id)
    if not item:
        return _json_response({"error": "item_not_found"}, 404)
    pushes.unmark_skip_push(item_id)
    _audit_push_action(item_id, "push_skip_cleared")
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
    return _json_response({"logs": serialized})


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
        return _json_response({"error": "invalid_date_range", "detail": str(exc)}, 400)
    return _json_response(result)


# ================================================================
# 小语种文案推送：进程内组装 → 一次 HTTP POST 到 wedev
# ================================================================


@bp.route("/api/items/<int:item_id>/push-localized-texts", methods=["POST"])
@login_required
@admin_required
def api_push_localized_texts(item_id: int):
    item = medias.get_item(item_id)
    if not item:
        return _json_response({"error": "item_not_found"}, 404)
    product = medias.get_product(item["product_id"])
    if not product:
        return _json_response({"error": "product_not_found"}, 404)
    if not medias.is_product_listed(product):
        return _json_response({"error": "product_not_listed"}, 409)
    try:
        mk_id = pushes.get_exact_product_mk_id(product)
    except Exception as exc:
        return _json_response({"error": "localized_texts_payload_invalid", "message": str(exc)}, 400)

    target_url = pushes.build_localized_texts_target_url(mk_id)
    if not target_url:
        return _json_response({"error": "push_localized_texts_base_url_missing"}, 500)

    headers = pushes.build_localized_texts_headers()
    if "Authorization" not in headers and "Cookie" not in headers:
        return _json_response({"error": "push_localized_texts_credentials_missing"}, 500)

    body = pushes.build_localized_texts_request(item)
    if not body.get("texts"):
        return _json_response({"error": "localized_texts_empty"}, 400)

    post_result = pushes.post_json_payload(target_url, body, headers=headers, timeout=30)
    if post_result.get("error") == "downstream_unreachable":
        detail = str(post_result.get("detail") or "")
        _audit_push_action(
            item_id,
            "push_localized_texts_failed",
            status="failed",
            detail={"error": "downstream_unreachable"},
        )
        return _json_response({
            "error": "downstream_unreachable",
            "detail": detail,
            "target_url": target_url,
        }, 502)

    if post_result.get("ok"):
        _audit_push_action(
            item_id,
            "push_localized_texts_succeeded",
            detail={"upstream_status": post_result.get("upstream_status")},
        )
        return _json_response({
            "ok": True,
            "upstream_status": post_result.get("upstream_status"),
            "response_body": post_result.get("response_body") or "",
            "target_url": target_url,
        })
    _audit_push_action(
        item_id,
        "push_localized_texts_failed",
        status="failed",
        detail={"upstream_status": post_result.get("upstream_status")},
    )
    return _json_response({
        "error": "downstream_error",
        "upstream_status": post_result.get("upstream_status"),
        "response_body": post_result.get("response_body") or "",
        "target_url": target_url,
    }, 502)


@bp.route("/api/items/<int:item_id>/product-links-push", methods=["POST"])
@login_required
@admin_required
def api_push_product_links(item_id: int):
    item = medias.get_item(item_id)
    if not item:
        return _json_response({"error": "item_not_found"}, 404)
    product = medias.get_product(item["product_id"])
    if not product:
        return _json_response({"error": "product_not_found"}, 404)
    try:
        result = pushes.push_product_links(product)
    except Exception as exc:
        return _product_links_push_error_response(exc)
    status = 200 if result.get("ok") else 502
    _audit_push_action(
        item_id,
        "push_product_links_succeeded" if result.get("ok") else "push_product_links_failed",
        status="success" if result.get("ok") else "failed",
        detail={"http_status": status},
    )
    return _json_response(result, status)


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
    return _json_response({
        "push_target_url": pushes.get_push_target_url(),
        "push_localized_texts_base_url": pushes.get_localized_texts_base_url(),
        "push_localized_texts_authorization_masked": _mask_secret(auth),
        "push_localized_texts_authorization_present": bool(auth),
        "push_localized_texts_cookie_masked": _mask_secret(cookie),
        "push_localized_texts_cookie_present": bool(cookie),
        "push_product_links_base_url": pushes.get_product_links_base_url(),
        "push_product_links_username": pushes.get_product_links_username(),
        "push_product_links_password_present": bool(pushes.get_product_links_password()),
    })


_ALLOWED_PUSH_SETTING_KEYS = {
    "push_target_url",
    "push_localized_texts_base_url",
    "push_localized_texts_authorization",
    "push_localized_texts_cookie",
    "push_product_links_base_url",
    "push_product_links_username",
    "push_product_links_password",
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
    _audit_push_action(
        None,
        "push_credentials_updated",
        target_type="system_setting",
        detail={
            "updated_keys": updated,
            "cleared_keys": [key for key in updated if clear_flags.get(key)],
        },
    )
    return _json_response({"ok": True, "updated": updated})


# ================================================================
# 推送历史记录 & 素材广告详情（独立路由）
# ================================================================

import json
from datetime import date, datetime

_LANG_TO_COUNTRY = {
    "de": "DE",
    "fr": "FR",
    "ja": "JP",
    "es": "ES",
    "it": "IT",
    "pt": "PT",
    "nl": "NL",
    "sv": "SE",
    "fi": "FI",
    "en": "US",
}

def _normalize_push_media_url(url: str | None) -> str:
    if not url:
        return ""
    if url.startswith(("http://", "https://")) and "/medias/obj/" in url:
        idx = url.find("/medias/obj/")
        return url[idx:]
    return url


def _country_for_lang(lang: str | None) -> str:
    if not lang:
        return "US"
    return _LANG_TO_COUNTRY.get(str(lang).strip().lower(), "US")


def _filter_links_by_lang(links: list[str], lang: str) -> list[str]:
    """根据素材的语种过滤投放推广链接，只保留契合该语种的链接。"""
    if not links:
        return []
    lang_str = str(lang or "").strip().lower()
    if not lang_str:
        return links

    small_langs = ["de", "fr", "ja", "es", "it", "pt", "nl", "sv", "fi"]
    filtered = []
    
    for link in links:
        link_lower = link.lower()
        if lang_str == "en":
            # 英语链接：不包含任何其他小语种的路径前缀
            has_other_lang = False
            for sl in small_langs:
                if f"/{sl}/" in link_lower or f"/{sl}/products/" in link_lower:
                    has_other_lang = True
                    break
            if not has_other_lang:
                filtered.append(link)
        else:
            # 小语种链接：链接路径中必须包含对应语种前缀
            if f"/{lang_str}/" in link_lower or f"/{lang_str}/products/" in link_lower:
                filtered.append(link)

    # 兜底返回，如果完全没有匹配的，则返回原列表防呆
    return filtered if filtered else links


@bp.route("/history")
@login_required
@permission_required("pushes")
def history():
    return render_template(
        "pushes_history.html",
        is_admin=_is_admin(),
        active="history",
    )


@bp.route("/api/history", methods=["GET"])
@login_required
@permission_required("pushes")
def api_history():
    langs = [l for l in request.args.getlist("lang") if l]
    keyword = (request.args.get("keyword") or "").strip()
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None
    ad_plan = (request.args.get("ad_plan") or "all").strip().lower()  # all / has / none
    page = max(1, int(request.args.get("page") or 1))
    limit = _PAGE_SIZE_DEFAULT

    where = ["l.status = 'success'", "i.deleted_at IS NULL", "p.deleted_at IS NULL"]
    args = []

    if langs:
        placeholders = ",".join(["%s"] * len(langs))
        where.append(f"i.lang IN ({placeholders})")
        args.extend(langs)
    if keyword:
        where.append("(i.display_name LIKE %s OR i.filename LIKE %s OR p.name LIKE %s OR p.product_code LIKE %s)")
        like = f"%{keyword}%"
        args.extend([like, like, like, like])
    if date_from:
        if len(date_from) == 10:
            date_from_dt = f"{date_from} 00:00:00"
        else:
            date_from_dt = date_from
        where.append("l.created_at >= %s")
        args.append(date_from_dt)
    if date_to:
        if len(date_to) == 10:
            date_to_dt = f"{date_to} 23:59:59"
        else:
            date_to_dt = date_to
        where.append("l.created_at <= %s")
        args.append(date_to_dt)

    has_ad_plan_clause = (
        "EXISTS ("
        "SELECT 1 FROM meta_ad_daily_ad_metrics madm "
        "WHERE COALESCE(madm.spend_usd, 0) > 0 "
        "AND ("
        "madm.product_id = i.product_id "
        "OR (madm.product_code IS NOT NULL AND CHAR_LENGTH(madm.product_code) >= 8 "
        "AND LOWER(p.product_code) LIKE CONCAT(LOWER(madm.product_code), '%%'))"
        ") "
        "AND ("
        "madm.ad_name LIKE CONCAT('%%', i.filename, '%%') "
        "OR madm.ad_name LIKE CONCAT('%%', i.display_name, '%%')"
        ")"
        ")"
    )
    if ad_plan == "has":
        where.append(has_ad_plan_clause)
    elif ad_plan == "none":
        where.append(f"NOT {has_ad_plan_clause}")
    elif ad_plan == "none_3d":
        where.append(f"NOT {has_ad_plan_clause}")
        where.append("l.created_at <= DATE_SUB(NOW(), INTERVAL 3 DAY)")

    where_sql = " AND ".join(where)
    from appcore.db import query as db_query

    owner_name_expr = medias._media_product_owner_name_expr().replace("u.", "owner_u.")
    rows = db_query(
        "SELECT l.id AS log_id, l.item_id, l.operator_user_id, l.status, l.request_payload, l.response_body, l.created_at AS pushed_at, "
        "       i.lang, i.display_name, i.filename, i.duration_seconds, i.file_size, i.product_id, "
        f"       p.name AS product_name, p.product_code, u.username AS operator_username, {owner_name_expr} AS product_owner_name "
        "FROM media_push_logs l "
        "JOIN media_items i ON i.id = l.item_id "
        "JOIN media_products p ON p.id = i.product_id "
        "LEFT JOIN users u ON u.id = l.operator_user_id "
        "LEFT JOIN users owner_u ON owner_u.id = p.user_id "
        f"WHERE {where_sql} "
        "ORDER BY l.created_at DESC, l.id DESC",
        tuple(args)
    )

    total = len(rows)
    start = (page - 1) * limit
    page_rows = rows[start:start + limit]

    history_items = []
    for r in page_rows:
        payload = {}
        try:
            payload = json.loads(r["request_payload"]) if r.get("request_payload") else {}
        except Exception:
            pass

        video_snap = payload.get("videos", [{}])[0] if payload.get("videos") else {}
        texts_snap = payload.get("texts", [])
        links_snap = payload.get("product_links", [])

        p_id = int(r["product_id"])
        product_code_lower = (r.get("product_code") or "").strip().lower()

        snap_name = (video_snap.get("name") or "").strip()
        search_filename = snap_name or r["filename"]
        search_display_name = snap_name or r["display_name"] or r["filename"]

        ad_info = db_query(
            "SELECT COALESCE(SUM(spend_usd), 0) AS total_spend, "
            "       COALESCE(SUM(purchase_value_usd), 0) AS total_purchase_value, "
            "       COUNT(DISTINCT ad_name) AS campaign_count "
            "FROM meta_ad_daily_ad_metrics "
            "WHERE COALESCE(spend_usd, 0) > 0 "
            "AND ("
            "product_id = %s "
            "OR (product_code IS NOT NULL AND CHAR_LENGTH(product_code) >= 8 "
            "AND %s LIKE CONCAT(LOWER(product_code), '%%'))"
            ") "
            "AND ("
            "ad_name LIKE CONCAT('%%', %s, '%%') "
            "OR ad_name LIKE CONCAT('%%', %s, '%%')"
            ")",
            (p_id, product_code_lower, search_filename, search_display_name)
        )[0]

        campaign_count = int(ad_info["campaign_count"] or 0)
        spend_total = float(ad_info["total_spend"] or 0)
        purchase_value_total = float(ad_info.get("total_purchase_value") or 0)
        ad_roas = purchase_value_total / spend_total if spend_total > 0 else 0.0

        history_item = {
            "log_id": r["log_id"],
            "item_id": r["item_id"],
            "product_id": p_id,
            "product_name": r["product_name"],
            "product_code": r["product_code"],
            "product_owner_name": r["product_owner_name"] or "未指派",
            "lang": r["lang"],
            "display_name": search_display_name,
            "filename": search_filename,
            "file_size": r["file_size"] or 0,
            "pushed_at": r["pushed_at"].isoformat() if r.get("pushed_at") else None,
            "operator_username": r["operator_username"] or "System",
            "video_url": _normalize_push_media_url(video_snap.get("url")),
            "cover_url": _normalize_push_media_url(video_snap.get("image_url")),
            "texts": texts_snap,
            "product_links": _filter_links_by_lang(links_snap, r["lang"]),
            "has_ad_plan": campaign_count > 0,
            "ad_campaign_count": campaign_count,
            "ad_spend_total": spend_total,
            "ad_roas": ad_roas,
        }
        history_items.append(history_item)

    return _json_response({
        "items": history_items,
        "total": total,
        "page": page,
        "page_size": limit,
    })


@bp.route("/material-ads/<int:item_id>")
@login_required
@permission_required("pushes")
def material_ads_detail(item_id: int):
    item = medias.get_item(item_id)
    if not item:
        return render_template("errors/404.html", message="素材未找到"), 404

    product = medias.get_product(item["product_id"])
    if not product:
        return render_template("errors/404.html", message="商品未找到"), 404

    from appcore.db import query as db_query
    push_log = query_one(
        "SELECT * FROM media_push_logs "
        "WHERE item_id = %s AND status = 'success' "
        "ORDER BY created_at DESC LIMIT 1",
        (item_id,)
    )

    payload = {}
    if push_log and push_log.get("request_payload"):
        try:
            payload = json.loads(push_log["request_payload"])
        except Exception:
            pass

    if payload and "product_links" in payload:
        payload["product_links"] = _filter_links_by_lang(payload["product_links"], item["lang"])

    country = _country_for_lang(item["lang"])

    video_snap = payload.get("videos", [{}])[0] if payload.get("videos") else {}
    snap_name = (video_snap.get("name") or "").strip()
    search_filename = snap_name or item["filename"]
    search_display_name = snap_name or item["display_name"] or item["filename"]

    product_code_lower = (product.get("product_code") or "").strip().lower()
    campaign_daily_metrics = db_query(
        "SELECT ad_account_name, ad_name AS campaign_name, spend_usd, purchase_value_usd, result_count, report_date, market_country "
        "FROM meta_ad_daily_ad_metrics "
        "WHERE COALESCE(spend_usd, 0) > 0 "
        "AND ("
        "product_id = %s "
        "OR (product_code IS NOT NULL AND CHAR_LENGTH(product_code) >= 8 "
        "AND %s LIKE CONCAT(LOWER(product_code), '%%'))"
        ") "
        "AND ("
        "ad_name LIKE CONCAT('%%', %s, '%%') "
        "OR ad_name LIKE CONCAT('%%', %s, '%%')"
        ") "
        "ORDER BY report_date DESC, spend_usd DESC",
        (item["product_id"], product_code_lower, search_filename, search_display_name)
    )

    filtered_metrics = []
    for m in campaign_daily_metrics:
        m_country = str(m.get("market_country") or "").strip().upper()
        if m_country == country or m_country in ("", "MULTI"):
            filtered_metrics.append(m)

    if not filtered_metrics and campaign_daily_metrics:
        filtered_metrics = campaign_daily_metrics

    campaigns_summary = {}
    for m in filtered_metrics:
        c_name = m["campaign_name"]
        if c_name not in campaigns_summary:
            campaigns_summary[c_name] = {
                "campaign_name": c_name,
                "ad_account_name": m["ad_account_name"] or "Unknown",
                "spend_total": 0.0,
                "purchase_value_total": 0.0,
                "result_count_total": 0,
                "min_date": m["report_date"],
                "max_date": m["report_date"],
            }
        sum_row = campaigns_summary[c_name]
        sum_row["spend_total"] += float(m["spend_usd"] or 0)
        sum_row["purchase_value_total"] += float(m["purchase_value_usd"] or 0)
        sum_row["result_count_total"] += int(m["result_count"] or 0)
        if m["report_date"] < sum_row["min_date"]:
            sum_row["min_date"] = m["report_date"]
        if m["report_date"] > sum_row["max_date"]:
            sum_row["max_date"] = m["report_date"]

    for c_name, s in campaigns_summary.items():
        s["roas"] = s["purchase_value_total"] / s["spend_total"] if s["spend_total"] > 0 else 0.0
        s["min_date"] = s["min_date"].strftime("%Y-%m-%d") if isinstance(s["min_date"], (date, datetime)) else str(s["min_date"])
        s["max_date"] = s["max_date"].strftime("%Y-%m-%d") if isinstance(s["max_date"], (date, datetime)) else str(s["max_date"])

    daily_rows_formatted = []
    for m in filtered_metrics:
        spend = float(m["spend_usd"] or 0)
        p_val = float(m["purchase_value_usd"] or 0)
        daily_rows_formatted.append({
            "report_date": m["report_date"].strftime("%Y-%m-%d") if isinstance(m["report_date"], (date, datetime)) else str(m["report_date"]),
            "campaign_name": m["campaign_name"],
            "ad_account_name": m["ad_account_name"] or "Unknown",
            "spend_usd": spend,
            "result_count": int(m["result_count"] or 0),
            "roas": p_val / spend if spend > 0 else 0.0,
        })

    video_snap = payload.get("videos", [{}])[0] if payload.get("videos") else {}
    if "url" in video_snap:
        video_snap["url"] = _normalize_push_media_url(video_snap.get("url"))
    if "image_url" in video_snap:
        video_snap["image_url"] = _normalize_push_media_url(video_snap.get("image_url"))

    return render_template(
        "pushes_material_ads.html",
        is_admin=_is_admin(),
        item=item,
        product=product,
        push_log=push_log,
        payload=payload,
        video_snap=video_snap,
        campaigns_summary=list(campaigns_summary.values()),
        daily_rows=daily_rows_formatted,
        country=country,
    )

