"""任务中心 Blueprint."""
from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, current_app, render_template, request
from flask_login import current_user, login_required

from web.auth import permission_required
from appcore import mk_import as mk_import_svc
from appcore import system_audit
from appcore import tasks as tasks_svc
from appcore.users import (
    ensure_raw_processor_user,
    ensure_translation_work_user,
    list_raw_processors,
    list_translation_work_users,
    list_translators,
)
from web.services.tasks_responses import (
    build_tasks_payload_response,
    tasks_flask_response,
)

log = logging.getLogger(__name__)
bp = Blueprint("tasks", __name__, url_prefix="/tasks")


def _json_response(payload, status_code: int = 200):
    return tasks_flask_response(
        build_tasks_payload_response(payload, status_code)
    )


def _is_admin() -> bool:
    return getattr(current_user, "is_admin", False) or \
        getattr(current_user, "role", "") in ("admin", "superadmin")


def _user_perms() -> dict:
    perms = getattr(current_user, "permissions", None) or {}
    if isinstance(perms, str):
        import json
        try:
            perms = json.loads(perms)
        except Exception:
            perms = {}
    return perms or {}


def _has_capability(code: str) -> bool:
    if _is_admin():
        return True
    return bool(_user_perms().get(code, False))


def admin_required(fn):
    @wraps(fn)
    def _wrap(*a, **kw):
        if not _is_admin():
            return _json_response({"error": "仅管理员可操作"}, 403)
        return fn(*a, **kw)
    return _wrap


def capability_required(code: str):
    def _dec(fn):
        @wraps(fn)
        def _wrap(*a, **kw):
            if not _has_capability(code):
                return _json_response({"error": f"缺少能力 {code}"}, 403)
            return fn(*a, **kw)
        return _wrap
    return _dec


def _audit_task_action(task_id: int, action: str, detail: dict | None = None) -> None:
    system_audit.record_from_request(
        user=current_user,
        request_obj=request,
        action=action,
        module="tasks",
        target_type="task",
        target_id=task_id,
        detail=detail,
    )


def _normalize_countries(countries: list[str]) -> list[str]:
    return [str(country or "").strip().upper() for country in (countries or []) if str(country or "").strip()]


def _parse_language_assignments(raw_value, countries: list[str]) -> dict[str, int] | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, dict):
        raise ValueError("language_assignments must be an object")
    normalized = {}
    for raw_country, raw_user_id in raw_value.items():
        country = str(raw_country or "").strip().upper()
        if not country:
            continue
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"language_assignments[{country}] must be an integer") from exc
        normalized[country] = user_id
    missing = [country for country in countries if country not in normalized]
    extras = [country for country in normalized if country not in countries]
    if missing or extras:
        raise ValueError("language_assignments must cover exactly the requested countries")
    return normalized


def _validate_translation_targets(
    *,
    translator_id: int | None,
    language_assignments: dict[str, int] | None,
) -> None:
    if language_assignments:
        seen = set()
        for user_id in language_assignments.values():
            if user_id in seen:
                continue
            ensure_translation_work_user(user_id)
            seen.add(user_id)
        return
    if translator_id is None:
        raise ValueError("translator_id or language_assignments required")
    ensure_translation_work_user(translator_id)


@bp.route("/")
@login_required
@permission_required("task_center")
def index():
    return render_template(
        "tasks_list.html",
        is_admin=_is_admin(),
        capabilities={
            "can_process_raw_video": _has_capability("can_process_raw_video"),
            "can_translate": _has_capability("can_translate"),
        },
    )


@bp.route("/api/list", methods=["GET"])
@login_required
def api_list():
    tab = (request.args.get("tab") or "mine").strip()
    keyword = (request.args.get("keyword") or "").strip()
    high_status = (request.args.get("status") or "").strip()
    bucket = (request.args.get("bucket") or "").strip()
    page = max(1, int(request.args.get("page") or 1))
    page_size = min(100, max(1, int(request.args.get("page_size") or 20)))
    raw_task_id = (request.args.get("task_id") or "").strip()
    task_id = None
    if raw_task_id:
        try:
            parsed_task_id = int(raw_task_id)
        except ValueError:
            return _json_response({"error": "invalid task_id"}, 400)
        if parsed_task_id > 0:
            task_id = parsed_task_id
    if tab == "all":
        if not _is_admin():
            return _json_response({"error": "需要管理员权限"}, 403)
    elif tab == "mine":
        pass
    else:
        return _json_response({"error": "invalid tab"}, 400)
    if bucket and bucket not in {"todo", "review", "done"}:
        return _json_response({"error": "invalid bucket"}, 400)

    return _json_response(
        tasks_svc.list_task_center_items(
            tab=tab,
            user_id=int(current_user.id),
            can_process_raw_video=_has_capability("can_process_raw_video"),
            keyword=keyword,
            high_status=high_status,
            bucket=bucket,
            page=page,
            page_size=page_size,
            task_id=task_id,
        )
    )


@bp.route("/api/dispatch_pool", methods=["GET"])
@login_required
@admin_required
def api_dispatch_pool():
    return _json_response({"items": tasks_svc.list_dispatch_pool_products()})


@bp.route("/api/parent", methods=["POST"])
@login_required
@admin_required
def api_create_parent():
    payload = request.get_json(silent=True) or {}
    try:
        product_id = int(payload["media_product_id"])
        item_id = payload.get("media_item_id")
        item_id = int(item_id) if item_id is not None else None
        countries = _normalize_countries(payload.get("countries") or [])
        translator_id_raw = payload.get("translator_id")
        translator_id = int(translator_id_raw) if translator_id_raw is not None else None
        language_assignments = _parse_language_assignments(
            payload.get("language_assignments"),
            countries,
        )
        raw_processor_id = int(payload["raw_processor_id"])
    except (KeyError, TypeError, ValueError) as e:
        return _json_response({"error": f"参数错误: {e}"}, 400)
    try:
        _validate_translation_targets(
            translator_id=translator_id,
            language_assignments=language_assignments,
        )
        ensure_raw_processor_user(raw_processor_id)
    except ValueError as e:
        return _json_response({"error": str(e)}, 400)
    try:
        parent_id = tasks_svc.create_parent_task(
            media_product_id=product_id,
            media_item_id=item_id,
            countries=countries,
            translator_id=translator_id,
            language_assignments=language_assignments,
            raw_processor_id=raw_processor_id,
            created_by=int(current_user.id),
        )
    except ValueError as e:
        return _json_response({"error": str(e)}, 400)
    from appcore import task_raw_video_processing

    try:
        raw_processing = task_raw_video_processing.start_niuma_processing_for_parent_task(
            task_id=parent_id,
            actor_user_id=raw_processor_id,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            task_raw_video_processing.record_niuma_start_failed(
                parent_task_id=parent_id,
                actor_user_id=raw_processor_id,
                error=str(exc),
            )
        except Exception:  # noqa: BLE001
            current_app.logger.exception("record niuma start failure failed task_id=%s", parent_id)
        raw_processing = {"status": "start_failed", "error": str(exc)}
    _audit_task_action(
        parent_id,
        "task_parent_created",
        {
            "media_product_id": product_id,
            "media_item_id": item_id,
            "countries": countries,
            "translator_id": translator_id,
            "raw_processor_id": raw_processor_id,
            **({"language_assignments": language_assignments} if language_assignments else {}),
        },
    )
    return _json_response({"parent_task_id": parent_id, "raw_processing": raw_processing})


@bp.route("/api/import-and-create", methods=["POST"])
@login_required
@admin_required
def api_import_and_create():
    payload = request.get_json(silent=True) or {}
    try:
        meta = payload["mk_video_metadata"]
        translator_id_raw = payload.get("translator_id")
        translator_id = int(translator_id_raw) if translator_id_raw is not None else None
        countries = _normalize_countries(payload.get("countries") or [])
        language_assignments = _parse_language_assignments(
            payload.get("language_assignments"),
            countries,
        )
    except (KeyError, TypeError, ValueError) as e:
        return _json_response({"error": f"参数错误: {e}"}, 400)
    try:
        _validate_translation_targets(
            translator_id=translator_id,
            language_assignments=language_assignments,
        )
    except ValueError as e:
        return _json_response({"error": str(e)}, 400)
    try:
        result = tasks_svc.import_and_create_task(
            mk_video_metadata=meta,
            translator_id=translator_id,
            countries=countries,
            language_assignments=language_assignments,
            actor_user_id=int(current_user.id),
        )
    except mk_import_svc.DuplicateError as e:
        return _json_response({"error": f"视频已入库: {e}"}, 409)
    except mk_import_svc.DownloadError as e:
        return _json_response({"error": f"下载失败: {e}"}, 502)
    except mk_import_svc.StorageError as e:
        return _json_response({"error": f"存储失败: {e}"}, 500)
    except mk_import_svc.DBError as e:
        return _json_response({"error": f"数据库错误: {e}"}, 500)
    except ValueError as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(
        result["parent_task_id"],
        "task_import_and_create",
        {
            "media_product_id": result["media_product_id"],
            "media_item_id": result["media_item_id"],
            "countries": countries,
            "translator_id": translator_id,
            **({"language_assignments": language_assignments} if language_assignments else {}),
            "is_new_product": result["is_new_product"],
        },
    )
    return _json_response(result)


@bp.route("/api/parent/<int:tid>/claim", methods=["POST"])
@login_required
@capability_required("can_process_raw_video")
def api_parent_claim(tid: int):
    try:
        tasks_svc.claim_parent(task_id=tid, actor_user_id=int(current_user.id))
    except tasks_svc.ConflictError as e:
        return _json_response({"error": str(e)}, 409)
    _audit_task_action(tid, "task_parent_claimed")
    raw_processing = None
    if not current_app.testing:
        from appcore import task_raw_video_processing

        try:
            raw_processing = task_raw_video_processing.start_niuma_processing_for_parent_task(
                task_id=tid,
                actor_user_id=int(current_user.id),
            )
        except Exception as exc:  # noqa: BLE001
            try:
                task_raw_video_processing.record_niuma_start_failed(
                    parent_task_id=tid,
                    actor_user_id=int(current_user.id),
                    error=str(exc),
                )
            except Exception:  # noqa: BLE001
                current_app.logger.exception("record niuma start failure failed task_id=%s", tid)
            raw_processing = {"status": "start_failed", "error": str(exc)}
    return _json_response({"ok": True, "raw_processing": raw_processing})


@bp.route("/api/parent/<int:tid>/upload_done", methods=["POST"])
@login_required
def api_parent_upload_done(tid: int):
    try:
        tasks_svc.mark_uploaded(task_id=tid, actor_user_id=int(current_user.id))
    except tasks_svc.StateError as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_parent_upload_done")
    return _json_response({"ok": True})


@bp.route("/api/parent/<int:tid>/approve", methods=["POST"])
@login_required
@admin_required
def api_parent_approve(tid: int):
    try:
        tasks_svc.approve_raw(task_id=tid, actor_user_id=int(current_user.id))
    except tasks_svc.StateError as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_parent_approved")
    return _json_response({"ok": True})


@bp.route("/api/parent/<int:tid>/reject", methods=["POST"])
@login_required
@admin_required
def api_parent_reject(tid: int):
    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "").strip()
    try:
        tasks_svc.reject_raw(task_id=tid, actor_user_id=int(current_user.id),
                             reason=reason)
    except (ValueError, tasks_svc.StateError) as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_parent_rejected", {"reason": reason})
    return _json_response({"ok": True})


@bp.route("/api/parent/<int:tid>/cancel", methods=["POST"])
@login_required
@admin_required
def api_parent_cancel(tid: int):
    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "").strip()
    try:
        tasks_svc.cancel_parent(task_id=tid, actor_user_id=int(current_user.id),
                                reason=reason)
    except (ValueError, tasks_svc.StateError) as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_parent_cancelled", {"reason": reason})
    return _json_response({"ok": True})


@bp.route("/api/parent/<int:tid>/bind_item", methods=["PATCH"])
@login_required
def api_parent_bind_item(tid: int):
    """父任务回填 media_item_id；上传后跳转回时调用。"""
    payload = request.get_json(silent=True) or {}
    item_id = payload.get("media_item_id")
    if item_id is None:
        return _json_response({"error": "media_item_id required"}, 400)
    try:
        tasks_svc.bind_parent_media_item(
            task_id=tid,
            media_item_id=int(item_id),
            actor_user_id=int(current_user.id),
            is_admin=_is_admin(),
        )
    except tasks_svc.StateError as exc:
        return _json_response({"error": str(exc)}, 404)
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, 403)
    except ValueError as exc:
        return _json_response({"error": str(exc)}, 400)
    _audit_task_action(tid, "task_parent_bound_item", {"media_item_id": int(item_id)})
    return _json_response({"ok": True})


@bp.route("/api/child/<int:tid>/submit", methods=["POST"])
@login_required
def api_child_submit(tid: int):
    try:
        tasks_svc.submit_child(task_id=tid, actor_user_id=int(current_user.id))
    except tasks_svc.NotReadyError as e:
        return _json_response({"error": "readiness_failed", "missing": e.missing}, 422)
    except tasks_svc.StateError as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_child_submitted")
    return _json_response({"ok": True})


@bp.route("/api/child/<int:tid>/approve", methods=["POST"])
@login_required
@admin_required
def api_child_approve(tid: int):
    try:
        tasks_svc.approve_child(task_id=tid, actor_user_id=int(current_user.id))
    except tasks_svc.StateError as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_child_approved")
    return _json_response({"ok": True})


@bp.route("/api/child/<int:tid>/reject", methods=["POST"])
@login_required
@admin_required
def api_child_reject(tid: int):
    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "").strip()
    try:
        tasks_svc.reject_child(task_id=tid, actor_user_id=int(current_user.id),
                               reason=reason)
    except (ValueError, tasks_svc.StateError) as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_child_rejected", {"reason": reason})
    return _json_response({"ok": True})


@bp.route("/api/child/<int:tid>/cancel", methods=["POST"])
@login_required
@admin_required
def api_child_cancel(tid: int):
    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "").strip()
    try:
        tasks_svc.cancel_child(task_id=tid, actor_user_id=int(current_user.id),
                               reason=reason)
    except (ValueError, tasks_svc.StateError) as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_child_cancelled", {"reason": reason})
    return _json_response({"ok": True})


@bp.route("/api/<int:tid>/events", methods=["GET"])
@login_required
def api_events(tid: int):
    return _json_response({"events": tasks_svc.list_task_events(tid)})


@bp.route("/api/<int:tid>/review-assets", methods=["GET"])
@login_required
def api_review_assets(tid: int):
    try:
        return _json_response(tasks_svc.get_task_review_assets(tid))
    except tasks_svc.StateError as exc:
        return _json_response({"error": str(exc)}, 404)


@bp.route("/api/translators", methods=["GET"])
@login_required
def api_translators():
    return _json_response({"translators": list_translators()})


@bp.route("/api/raw-processors", methods=["GET"])
@login_required
@admin_required
def api_raw_processors():
    return _json_response({"users": list_raw_processors()})


@bp.route("/api/translation-work-users", methods=["GET"])
@login_required
def api_translation_work_users():
    return _json_response({"users": list_translation_work_users()})


@bp.route("/api/languages", methods=["GET"])
@login_required
def api_languages():
    return _json_response({"languages": tasks_svc.list_enabled_target_languages()})


@bp.route("/api/product/<int:pid>/en_items", methods=["GET"])
@login_required
def api_product_en_items(pid: int):
    return _json_response({"items": tasks_svc.list_product_english_items(pid)})


@bp.route("/api/child/<int:tid>/readiness", methods=["GET"])
@login_required
def api_child_readiness(tid: int):
    """E 子系统：返回子任务对应语种 media_item 的 readiness 状态。"""
    try:
        payload = tasks_svc.get_child_readiness(tid)
    except tasks_svc.StateError as exc:
        return _json_response({"error": str(exc)}, 404)
    return _json_response(payload)


@bp.route("/api/<int:tid>/artifacts", methods=["GET"])
@login_required
def api_task_artifacts(tid: int):
    """返回任务产生的 media_items 列表。"""
    task = tasks_svc._row(tid)
    if not task:
        return _json_response({"error": "task not found"}, 404)
    is_parent = task["parent_task_id"] is None
    items = tasks_svc.list_task_artifacts(task_id=tid, is_parent=is_parent)
    return _json_response({"items": items})


@bp.route("/api/<int:tid>/unbound-items", methods=["GET"])
@login_required
def api_task_unbound_items(tid: int):
    """返回可手动绑定到该任务的未关联 media_items。"""
    try:
        items = tasks_svc.list_unbound_items_for_task(tid)
    except tasks_svc.StateError as e:
        return _json_response({"error": str(e)}, 404)
    return _json_response({"items": items})


@bp.route("/api/<int:tid>/bind-items", methods=["POST"])
@login_required
@admin_required
def api_task_bind_items(tid: int):
    """手动绑定 media_items 到任务。"""
    payload = request.get_json(silent=True) or {}
    item_ids = payload.get("item_ids") or []
    if not item_ids:
        return _json_response({"error": "item_ids required"}, 400)
    try:
        item_ids = [int(i) for i in item_ids]
    except (TypeError, ValueError):
        return _json_response({"error": "item_ids must be integers"}, 400)
    from appcore import medias as medias_svc
    task = tasks_svc._row(tid)
    if not task:
        return _json_response({"error": "task not found"}, 404)
    child_id = tid if task["parent_task_id"] is not None else None
    bound = 0
    for iid in item_ids:
        affected = medias_svc.update_item_task_id(iid, child_id)
        bound += affected
    return _json_response({"bound": bound})
