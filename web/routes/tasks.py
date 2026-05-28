"""任务中心 Blueprint."""
from __future__ import annotations

import logging
import os
import re
from functools import wraps

from flask import Blueprint, current_app, render_template, request
from flask_login import current_user, login_required

from web.auth import permission_required
from appcore import local_media_storage, object_keys
from appcore import raw_video_pool as rvp_svc
from appcore import system_audit
from appcore import tasks as tasks_svc
from appcore.users import (
    ensure_translation_work_user,
    list_translation_work_users,
    list_translators,
)
from web.services.tasks_responses import (
    build_tasks_payload_response,
    tasks_flask_response,
)
from web.services.material_evaluation_trigger import (
    trigger_material_evaluation,
)
from web.upload_util import client_filename_basename

log = logging.getLogger(__name__)
bp = Blueprint("tasks", __name__, url_prefix="/tasks")
_MANUAL_FILENAME_SAFE_RE = re.compile(r"[^\w\u4e00-\u9fff.\-()（）]+")

TASK_CENTER_DEFAULT_PAGE_SIZE = 50
MANUAL_RESULT_MAX_UPLOAD_BYTES = 500 * 1024 * 1024
MANUAL_RESULT_ALLOWED_EXT = (".mp4", ".mov", ".webm", ".mkv")


def _json_response(payload, status_code: int = 200):
    return tasks_flask_response(
        build_tasks_payload_response(payload, status_code)
    )


def _is_admin() -> bool:
    return getattr(current_user, "is_admin", False) or \
        getattr(current_user, "role", "") in ("admin", "superadmin")


def _manual_upload_filename(filename: str) -> str:
    raw = os.path.basename(str(filename or "manual.bin").replace("\\", "/")).strip()
    safe = _MANUAL_FILENAME_SAFE_RE.sub("_", raw).strip("._")
    return safe or "manual.bin"


def _manual_output_text_payload() -> dict:
    if request.is_json:
        body = request.get_json(silent=True) or {}
    else:
        body = request.form
    return {
        "title": str(body.get("title") or "").strip(),
        "message": str(body.get("message") or body.get("body") or "").strip(),
        "description": str(body.get("description") or "").strip(),
    }


def _manual_output_uploaded_files(tid: int, step_key: str) -> list[dict]:
    incoming = []
    incoming.extend(request.files.getlist("file"))
    incoming.extend(request.files.getlist("files"))
    files = []
    seen = set()
    for storage in incoming:
        if not storage or not storage.filename:
            continue
        if id(storage) in seen:
            continue
        seen.add(id(storage))
        filename = _manual_upload_filename(storage.filename)
        object_key = object_keys.build_media_object_key(
            int(current_user.id),
            f"task-{int(tid)}",
            f"manual_{step_key}_{filename}",
        )
        path = local_media_storage.write_stream(object_key, storage.stream)
        file_size = path.stat().st_size if path and path.exists() else None
        files.append({
            "filename": filename,
            "object_key": object_key,
            "content_type": storage.mimetype or storage.content_type or "",
            "file_size": file_size,
        })
    return files


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


def _trigger_material_evaluation(
    *,
    product_id: int,
    media_item_id: int | None,
    force: bool,
    manual: bool,
    product_url_override: str | None = None,
) -> bool:
    return trigger_material_evaluation(
        product_id=product_id,
        media_item_id=media_item_id,
        force=force,
        manual=manual,
        product_url_override=product_url_override,
        user_id=int(getattr(current_user, "id", 0) or 0) or None,
        entrypoint="tasks.product_evaluation",
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


def _render_task_center(initial_task_id: int | None = None):
    return render_template(
        "tasks_list.html",
        is_admin=_is_admin(),
        initial_task_id=int(initial_task_id or 0),
        capabilities={
            "can_process_raw_video": _has_capability("can_process_raw_video"),
            "can_translate": _has_capability("can_translate"),
        },
    )


@bp.route("/")
@login_required
@permission_required("task_center")
def index():
    return _render_task_center()


@bp.route("/detail/<int:task_id>")
@login_required
@permission_required("task_center")
def detail(task_id: int):
    return _render_task_center(task_id)


@bp.route("/api/list", methods=["GET"])
@login_required
def api_list():
    tab = (request.args.get("tab") or "mine").strip()
    keyword = (request.args.get("keyword") or "").strip()
    high_status = (request.args.get("status") or "").strip()
    bucket = (request.args.get("bucket") or "").strip()
    task_type = (request.args.get("task_type") or "").strip()
    urgency = (request.args.get("urgency") or "").strip()
    raw_assignee_id = (request.args.get("assignee_id") or "").strip()
    page = max(1, int(request.args.get("page") or 1))
    page_size = min(100, max(1, int(request.args.get("page_size") or TASK_CENTER_DEFAULT_PAGE_SIZE)))
    raw_task_id = (request.args.get("task_id") or "").strip()
    assignee_id = None
    if raw_assignee_id and raw_assignee_id != "all":
        try:
            parsed_assignee_id = int(raw_assignee_id)
        except ValueError:
            return _json_response({"error": "invalid assignee_id"}, 400)
        if parsed_assignee_id <= 0:
            return _json_response({"error": "invalid assignee_id"}, 400)
        assignee_id = parsed_assignee_id
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
    if bucket == "all":
        bucket = ""
    if bucket and bucket not in {"todo", "review", "done"}:
        return _json_response({"error": "invalid bucket"}, 400)
    if task_type == "all":
        task_type = ""
    if task_type and task_type not in {"raw", "translate"}:
        return _json_response({"error": "invalid task_type"}, 400)
    if urgency == "all":
        urgency = ""
    if urgency and urgency not in {"urgent", "normal"}:
        return _json_response({"error": "invalid urgency"}, 400)

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
            task_type=task_type,
            assignee_id=assignee_id,
            urgency=urgency,
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
        product_url_override = str(payload.get("product_link") or "").strip() or None
    except (KeyError, TypeError, ValueError) as e:
        return _json_response({"error": f"参数错误: {e}"}, 400)
    try:
        _validate_translation_targets(
            translator_id=translator_id,
            language_assignments=language_assignments,
        )
        ensure_translation_work_user(raw_processor_id)
    except ValueError as e:
        return _json_response({"error": str(e)}, 400)
    raw_source_reuse = None
    if item_id is not None:
        from appcore import task_raw_source_bridge

        raw_source_reuse = task_raw_source_bridge.find_ready_raw_source_for_media_item(item_id)
    try:
        create_kwargs = {
            "media_product_id": product_id,
            "media_item_id": item_id,
            "countries": countries,
            "translator_id": translator_id,
            "language_assignments": language_assignments,
            "raw_processor_id": raw_processor_id,
            "created_by": int(current_user.id),
        }
        if raw_source_reuse:
            create_kwargs["reused_raw_source_id"] = int(raw_source_reuse["id"])
        parent_id = tasks_svc.create_parent_task(**create_kwargs)
    except ValueError as e:
        return _json_response({"error": str(e)}, 400)
    if raw_source_reuse:
        raw_processing = {
            "status": "skipped",
            "reason": "raw_source_ready",
            "raw_source_id": int(raw_source_reuse["id"]),
        }
    else:
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
            **({"product_link": product_url_override} if product_url_override else {}),
            **(
                {"reused_raw_source_id": int(raw_source_reuse["id"])}
                if raw_source_reuse else {}
            ),
            **({"language_assignments": language_assignments} if language_assignments else {}),
        },
    )
    try:
        _trigger_material_evaluation(
            product_id=product_id,
            media_item_id=item_id,
            force=False,
            manual=False,
            product_url_override=product_url_override,
        )
    except Exception:
        current_app.logger.exception(
            "trigger material evaluation after parent task create failed product_id=%s item_id=%s",
            product_id,
            item_id,
        )
    return _json_response({"parent_task_id": parent_id, "raw_processing": raw_processing})


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


@bp.route("/api/parent/<int:tid>/force_niuma_rerun", methods=["POST"])
@login_required
def api_parent_force_niuma_rerun(tid: int):
    from appcore import task_raw_video_processing

    try:
        raw_processing = task_raw_video_processing.force_rerun_niuma_processing_for_parent_task(
            task_id=tid,
            actor_user_id=int(current_user.id),
            is_admin=_is_admin(),
        )
    except PermissionError as e:
        return _json_response({"error": str(e)}, 403)
    except task_raw_video_processing.RawVideoProcessingError as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(
        tid,
        "task_parent_force_niuma_rerun",
        {
            "subtitle_task_id": raw_processing.get("subtitle_task_id"),
            "status": raw_processing.get("status"),
        },
    )
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
def api_parent_approve(tid: int):
    try:
        tasks_svc.approve_raw(
            task_id=tid,
            actor_user_id=int(current_user.id),
            is_admin=_is_admin(),
        )
    except PermissionError as e:
        return _json_response({"error": str(e)}, 403)
    except tasks_svc.StateError as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_parent_approved")
    return _json_response({"ok": True})


@bp.route("/api/parent/<int:tid>/manual_result", methods=["POST"])
@login_required
def api_parent_manual_result(tid: int):
    uploaded = request.files.get("file")
    if not uploaded:
        return _json_response({"error": "no_file"}, 400)
    uploaded.seek(0, 2)
    size = uploaded.tell()
    uploaded.seek(0)
    if size > MANUAL_RESULT_MAX_UPLOAD_BYTES:
        return _json_response({"error": "file_too_large", "max_mb": 500}, 413)
    filename = client_filename_basename(uploaded.filename)
    if not filename.lower().endswith(MANUAL_RESULT_ALLOWED_EXT):
        return _json_response({"error": "unsupported_type"}, 415)
    uploaded.filename = filename
    try:
        new_size = rvp_svc.replace_processed_video(
            task_id=tid,
            actor_user_id=int(current_user.id),
            uploaded_file=uploaded,
            allowed_statuses=(tasks_svc.PARENT_RAW_IN_PROGRESS, tasks_svc.PARENT_RAW_REVIEW),
            mark_uploaded_after=False,
        )
        _audit_task_action(
            tid,
            "task_parent_manual_result_uploaded",
            {"new_size": new_size},
        )
        # Check current task status; if it is raw_in_progress, transition to raw_review first!
        task_row = tasks_svc._row(tid) or {}
        if task_row.get("status") == tasks_svc.PARENT_RAW_IN_PROGRESS:
            tasks_svc.mark_uploaded(task_id=tid, actor_user_id=int(current_user.id))

        # 直接执行审核通过，使其自动入库并结束审核流程
        tasks_svc.approve_raw(
            task_id=tid,
            actor_user_id=int(current_user.id),
            is_admin=_is_admin(),
        )

    except rvp_svc.PermissionDenied as e:
        return _json_response({"error": str(e)}, 403)
    except PermissionError as e:
        return _json_response({"error": str(e)}, 403)
    except (rvp_svc.StateError, tasks_svc.StateError) as e:
        return _json_response({"error": str(e)}, 400)
    return _json_response({"ok": True, "new_size": new_size})


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


@bp.route("/api/<int:tid>/urgency", methods=["POST"])
@login_required
@admin_required
def api_task_urgency(tid: int):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get("is_urgent"), bool):
        return _json_response({"error": "is_urgent must be boolean"}, 400)
    try:
        result = tasks_svc.set_task_urgency(
            task_id=tid,
            actor_user_id=int(current_user.id),
            is_urgent=payload["is_urgent"],
        )
    except tasks_svc.StateError as e:
        return _json_response({"error": str(e)}, 404)
    _audit_task_action(tid, "task_urgency_changed", result)
    return _json_response({"ok": True, **result})


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
    return _json_response({"users": list_translation_work_users()})


@bp.route("/api/translation-work-users", methods=["GET"])
@login_required
def api_translation_work_users():
    return _json_response({"users": list_translation_work_users()})


@bp.route("/api/languages", methods=["GET"])
@login_required
def api_languages():
    media_item_id = request.args.get("media_item_id")
    existing_langs = []
    if media_item_id:
        try:
            item_id_int = int(media_item_id)
            existing_langs = tasks_svc.get_existing_task_languages_for_item(item_id_int)
        except (ValueError, TypeError):
            pass
    langs = tasks_svc.list_enabled_target_languages()
    for l in langs:
        l["existing"] = l["code"] in existing_langs
    return _json_response({"languages": langs})


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


@bp.route("/api/child/<int:tid>/steps/<step_key>/confirm", methods=["POST"])
@login_required
def api_child_step_confirm(tid: int, step_key: str):
    """人工兜底确认某个子任务验收步骤已完成。"""
    try:
        result = tasks_svc.confirm_child_step(
            task_id=tid,
            step_key=step_key,
            actor_user_id=int(current_user.id),
            is_admin=_is_admin(),
        )
    except ValueError as exc:
        return _json_response({"error": str(exc)}, 400)
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, 403)
    except tasks_svc.StateError as exc:
        return _json_response({"error": str(exc)}, 404)
    _audit_task_action(
        tid,
        "task_child_step_confirmed",
        {"step_key": result["step_key"]},
    )
    return _json_response({"ok": True, "step_key": result["step_key"]})


@bp.route("/api/child/<int:tid>/steps/<step_key>/manual-output", methods=["POST"])
@login_required
def api_child_step_manual_output(tid: int, step_key: str):
    """人工提交某个子任务验收步骤所需的真实结果。"""
    if str(step_key or "").strip().lower() not in tasks_svc.CHILD_MANUAL_OUTPUT_STEP_KINDS:
        return _json_response({"error": "step does not accept manual output"}, 400)
    try:
        result = tasks_svc.submit_child_step_manual_output(
            task_id=tid,
            step_key=step_key,
            actor_user_id=int(current_user.id),
            is_admin=_is_admin(),
            text=_manual_output_text_payload(),
            files=_manual_output_uploaded_files(tid, step_key),
        )
    except ValueError as exc:
        return _json_response({"error": str(exc)}, 400)
    except PermissionError as exc:
        return _json_response({"error": str(exc)}, 403)
    except tasks_svc.StateError as exc:
        return _json_response({"error": str(exc)}, 404)
    _audit_task_action(
        tid,
        "task_child_step_manual_output_submitted",
        {"step_key": result["step_key"], "kind": result["kind"]},
    )
    return _json_response({"ok": True, **result})


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
