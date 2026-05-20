from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from web.auth import admin_required, superadmin_required
from appcore import medias, product_link_domains, product_roas, shopifyid_sync_trigger, system_audit
from appcore import voice_library_sync_task as vlst
from web.services.admin import (
    admin_flask_response,
    build_admin_error_response,
    build_admin_ok_response,
    build_admin_payload_response,
)
from appcore.users import (
    list_users, create_user, set_active, get_by_username,
    update_role, update_password, update_user_profile,
    update_permissions, reset_permissions_to_role_default,
    editable_user_profile_fields, OPTIONAL_USER_PROFILE_COLUMNS,
)
from appcore.permissions import (
    ROLE_ADMIN, ROLE_USER, ROLE_SUPERADMIN, ROLE_TRANSLATOR, ROLE_ANALYST,
    ROLE_LABELS, ROLES, grouped_permissions, PERMISSION_META,
    default_permissions_for_role, merge_with_defaults,
)
from appcore.settings import (
    PROJECT_TYPE_LABELS,
    get_all_retention_settings,
    get_retention_hours,
    get_setting,
    has_retention_override,
    set_setting,
    delete_setting,
    adjust_expires_for_type,
    adjust_expires_for_default,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")

WORK_SCOPE_OPTIONS = (
    {
        "code": "translation",
        "label": PERMISSION_META["work_scope_translation"]["label"],
        "permission": "work_scope_translation",
    },
)


def _render_users_page(error=None, status: int = 200):
    all_users = list_users()
    optional_profile_fields = [
        field for field in OPTIONAL_USER_PROFILE_COLUMNS
        if any(field in u for u in all_users)
    ]
    # 为模板准备权限对象；模板侧用 tojson 注入 JS 参数，避免 inline handler 注入。
    import json as _json
    for u in all_users:
        raw = u.get("permissions")
        if isinstance(raw, dict):
            u["permissions_payload"] = raw
        elif isinstance(raw, str):
            try:
                parsed = _json.loads(raw)
            except Exception:
                parsed = {}
            u["permissions_payload"] = parsed if isinstance(parsed, dict) else {}
        else:
            u["permissions_payload"] = {}
        effective_permissions = merge_with_defaults(u.get("role"), u["permissions_payload"])
        enabled_codes = [code for code in PERMISSION_META if effective_permissions.get(code)]
        enabled_labels = [PERMISSION_META[code]["label"] for code in enabled_codes[:3]]
        if u.get("role") == ROLE_SUPERADMIN:
            u["permissions_summary"] = "全部权限"
        elif enabled_labels:
            suffix = " 等" if len(enabled_codes) > len(enabled_labels) else ""
            u["permissions_summary"] = "、".join(enabled_labels) + suffix
        else:
            u["permissions_summary"] = "无已启用权限"
        work_scope_labels = [
            scope["label"]
            for scope in WORK_SCOPE_OPTIONS
            if effective_permissions.get(scope["permission"])
        ]
        work_scope_codes = [
            scope["code"]
            for scope in WORK_SCOPE_OPTIONS
            if effective_permissions.get(scope["permission"])
        ]
        u["work_scope_summary"] = "、".join(work_scope_labels) if work_scope_labels else "—"
        u["permissions_enabled_count"] = len(enabled_codes)
        u["permissions_total_count"] = len(PERMISSION_META)
        u["profile_payload"] = {
            "id": u.get("id"),
            "username": u.get("username") or "",
            "role": u.get("role") or ROLE_USER,
            "role_label": ROLE_LABELS.get(u.get("role"), u.get("role")),
            "is_active": bool(u.get("is_active")),
            "is_superadmin": u.get("role") == ROLE_SUPERADMIN,
            "work_scopes": work_scope_codes,
        }
        for field in optional_profile_fields:
            u["profile_payload"][field] = u.get(field) or ""
    return render_template("admin_users.html", users=all_users, error=error,
                           role_labels=ROLE_LABELS, current_user_id=current_user.id,
                           editable_profile_fields=optional_profile_fields,
                           work_scope_options=WORK_SCOPE_OPTIONS,
                           perm_groups=grouped_permissions(),
                           role_defaults={r: default_permissions_for_role(r) for r in ROLES}), status


def _audit_admin_action(
    action: str,
    *,
    target_type: str | None = None,
    target_id: int | str | None = None,
    target_label: str | None = None,
    detail: dict | None = None,
) -> None:
    system_audit.record_from_request(
        user=current_user,
        request_obj=request,
        action=action,
        module="admin",
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        detail=detail,
    )


@bp.route("/users", methods=["GET", "POST"])
@login_required
@superadmin_required
def users():
    error = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            role = request.form.get("role", "user")
            if not username or not password:
                error = "用户名和密码不能为空"
            elif get_by_username(username):
                error = f"用户名 '{username}' 已存在"
            else:
                try:
                    created_user_id = create_user(username, password, role=role)
                    _audit_admin_action(
                        "admin_user_created",
                        target_type="user",
                        target_id=created_user_id,
                        target_label=username,
                        detail={"role": role},
                    )
                    flash(f"用户 '{username}' 创建成功")
                except ValueError as exc:
                    error = str(exc)
                return redirect(url_for("admin.users"))
        elif action == "toggle_active":
            try:
                user_id = int(request.form.get("user_id"))
            except (TypeError, ValueError):
                error = "无效的用户 ID"
                return _render_users_page(error=error, status=400)
            active = request.form.get("active") == "1"
            set_active(user_id, active)
            _audit_admin_action(
                "admin_user_active_changed",
                target_type="user",
                target_id=user_id,
                detail={"active": active},
            )
            return redirect(url_for("admin.users"))
        elif action == "update_role":
            try:
                user_id = int(request.form.get("user_id"))
            except (TypeError, ValueError):
                error = "无效的用户 ID"
                return _render_users_page(error=error, status=400)
            new_role = request.form.get("new_role", "").strip()
            if new_role not in (ROLE_ADMIN, ROLE_USER, ROLE_TRANSLATOR, ROLE_ANALYST):
                error = f"无效的角色: {new_role}"
            else:
                try:
                    update_role(user_id, new_role)
                    _audit_admin_action(
                        "admin_user_role_updated",
                        target_type="user",
                        target_id=user_id,
                        detail={"new_role": new_role},
                    )
                    flash("角色已更新，权限已同步重置为新角色默认值")
                except ValueError as exc:
                    error = str(exc)
            return redirect(url_for("admin.users"))
    return _render_users_page(error=error)


@bp.route("/api/users/<int:user_id>/permissions", methods=["GET"])
@login_required
@superadmin_required
def get_user_permissions(user_id: int):
    from appcore.users import get_by_id
    from appcore.permissions import merge_with_defaults
    user = get_by_id(user_id)
    if not user:
        return admin_flask_response(build_admin_error_response("用户不存在", 404))
    effective = merge_with_defaults(user["role"], _coerce_json(user.get("permissions")))
    is_superadmin = user["role"] == ROLE_SUPERADMIN
    return admin_flask_response(
        build_admin_payload_response(
            {
                "user_id": user["id"],
                "username": user["username"],
                "role": user["role"],
                "role_label": ROLE_LABELS.get(user["role"], user["role"]),
                "is_superadmin": is_superadmin,
                "permissions": effective,
                "groups": [
                    {"code": g, "label": l, "items": items}
                    for g, l, items in grouped_permissions()
                ],
            }
        )
    )


@bp.route("/api/users/<int:user_id>/role", methods=["PUT"])
@login_required
@superadmin_required
def api_update_user_role(user_id: int):
    from appcore.users import get_by_id
    user = get_by_id(user_id)
    if not user:
        return admin_flask_response(build_admin_error_response("用户不存在", 404))
    if user["role"] == ROLE_SUPERADMIN:
        return admin_flask_response(build_admin_error_response("不能修改超级管理员角色", 403))
    body = request.get_json(silent=True) or {}
    new_role = (body.get("role") or "").strip()
    if new_role not in (ROLE_ADMIN, ROLE_USER, ROLE_TRANSLATOR, ROLE_ANALYST):
        return admin_flask_response(build_admin_error_response(f"无效的角色: {new_role}", 400))
    try:
        update_role(user_id, new_role)
    except ValueError as exc:
        return admin_flask_response(build_admin_error_response(str(exc), 400))
    _audit_admin_action(
        "admin_user_role_updated",
        target_type="user",
        target_id=user_id,
        target_label=user.get("username"),
        detail={"old_role": user.get("role"), "new_role": new_role},
    )
    return admin_flask_response(
        build_admin_ok_response(
            role=new_role,
            role_label=ROLE_LABELS.get(new_role, new_role),
        )
    )


@bp.route("/api/users/<int:user_id>/password", methods=["PUT"])
@login_required
@superadmin_required
def api_update_user_password(user_id: int):
    from appcore.users import get_by_id
    user = get_by_id(user_id)
    if not user:
        return admin_flask_response(build_admin_error_response("用户不存在", 404))
    body = request.get_json(silent=True) or {}
    password = (body.get("password") or "").strip()
    if not password:
        return admin_flask_response(build_admin_error_response("密码不能为空", 400))
    update_password(user_id, password)
    _audit_admin_action(
        "admin_user_password_updated",
        target_type="user",
        target_id=user_id,
        target_label=user.get("username"),
    )
    return admin_flask_response(build_admin_ok_response())


@bp.route("/api/users/<int:user_id>", methods=["PUT"])
@login_required
@superadmin_required
def api_update_user_profile(user_id: int):
    from appcore.users import get_by_id
    user = get_by_id(user_id)
    if not user:
        return admin_flask_response(build_admin_error_response("用户不存在", 404))
    body = request.get_json(silent=True) or {}
    update_kwargs = {
        "username": body.get("username") or "",
        "role": body.get("role") or user.get("role") or ROLE_USER,
        "is_active": bool(body.get("is_active")),
    }
    if "is_active" not in body:
        update_kwargs["is_active"] = bool(user.get("is_active"))
    if "xingming" in editable_user_profile_fields():
        update_kwargs["xingming"] = body.get("xingming") or ""
    if "work_scopes" in body:
        raw_scopes = body.get("work_scopes")
        allowed_scopes = {scope["code"] for scope in WORK_SCOPE_OPTIONS}
        if isinstance(raw_scopes, list):
            update_kwargs["work_scopes"] = [
                scope for scope in raw_scopes
                if isinstance(scope, str) and scope in allowed_scopes
            ]
        else:
            update_kwargs["work_scopes"] = []
    try:
        update_user_profile(user_id, **update_kwargs)
    except ValueError as exc:
        return admin_flask_response(build_admin_error_response(str(exc), 400))
    _audit_admin_action(
        "admin_user_profile_updated",
        target_type="user",
        target_id=user_id,
        target_label=user.get("username"),
        detail={
            "old_role": user.get("role"),
            "new_role": update_kwargs["role"],
            "is_active": update_kwargs["is_active"],
        },
    )
    return admin_flask_response(build_admin_ok_response())


@bp.route("/api/users/<int:user_id>/permissions", methods=["PUT"])
@login_required
@superadmin_required
def set_user_permissions(user_id: int):
    from appcore.users import get_by_id
    user = get_by_id(user_id)
    if not user:
        return admin_flask_response(build_admin_error_response("用户不存在", 404))
    if user["role"] == ROLE_SUPERADMIN:
        return admin_flask_response(build_admin_error_response("超级管理员权限不可修改", 403))
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    if action == "reset":
        cleaned = reset_permissions_to_role_default(user_id)
    else:
        cleaned = update_permissions(user_id, body.get("permissions"))
    _audit_admin_action(
        "admin_user_permissions_reset" if action == "reset" else "admin_user_permissions_updated",
        target_type="user",
        target_id=user_id,
        target_label=user.get("username"),
        detail={"permission_keys": sorted(cleaned.keys())},
    )
    return admin_flask_response(build_admin_ok_response(permissions=cleaned))


def _coerce_json(raw):
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    import json
    try:
        parsed = json.loads(raw) if isinstance(raw, (str, bytes)) else None
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _parse_int_list(values) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item <= 0 or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _handle_product_link_domains_post() -> None:
    action = (request.form.get("domain_action") or "save").strip().lower()
    if action == "add":
        raw_domain = (request.form.get("new_domain") or "").strip()
        if not raw_domain:
            flash("域名不能为空", "error")
            return
        try:
            product_link_domains.upsert_domain(raw_domain, enabled=True)
            flash("域名已新增")
        except ValueError as exc:
            flash(f"域名格式不正确：{exc}", "error")
        return
    if action == "delete":
        try:
            domain_id = int((request.form.get("delete_domain_id") or 0) or 0)
        except (TypeError, ValueError):
            domain_id = 0
        product_link_domains.delete_domain(domain_id)
        flash("域名已删除")
        return
    if action == "set_default":
        try:
            domain_id = int((request.form.get("default_domain_id") or 0) or 0)
        except (TypeError, ValueError):
            domain_id = 0
        if domain_id <= 0:
            flash("默认域名 id 不正确", "error")
            return
        product_link_domains.set_default_domain(domain_id)
        flash("默认域名已切换")
        return

    enabled_ids = _parse_int_list(request.form.getlist("enabled_domain_ids"))
    product_link_domains.set_global_enabled_domain_ids(enabled_ids)
    flash("域名启用状态已保存")


@bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings():
    active_tab = (request.values.get("tab") or "general").strip().lower()
    if active_tab not in {"general", "domains"}:
        active_tab = "general"

    if request.method == "POST":
        if active_tab == "domains":
            _handle_product_link_domains_post()
            return redirect(url_for("admin.settings", tab="domains"))

        raw_roas_rate = request.form.get("material_roas_rmb_per_usd", "").strip()
        if not raw_roas_rate:
            raw_roas_rate = product_roas.format_decimal(product_roas.DEFAULT_RMB_PER_USD)
        try:
            roas_rate = product_roas.validate_rmb_per_usd(raw_roas_rate)
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("admin.settings"))
        set_setting(product_roas.RMB_PER_USD_SETTING_KEY, product_roas.format_decimal(roas_rate))

        # TTS 全局并发上限：1 ≤ n ≤ 15（ElevenLabs Business 套餐硬上限），默认 12
        raw_tts_concurrency = request.form.get("tts_max_concurrency", "").strip()
        if raw_tts_concurrency:
            try:
                n = int(raw_tts_concurrency)
            except (ValueError, TypeError):
                n = 12
            n = max(1, min(n, 15))
            set_setting("tts_max_concurrency", str(n))

        old_default = get_retention_hours("__nonexistent__")
        old_per_type = {pt: get_retention_hours(pt) for pt in PROJECT_TYPE_LABELS}
        old_override_types = {pt for pt in PROJECT_TYPE_LABELS if has_retention_override(pt)}

        default_days = request.form.get("retention_default_days", "").strip()
        if default_days:
            try:
                hours = int(float(default_days) * 24)
                if hours > 0:
                    set_setting("retention_default_hours", str(hours))
            except (ValueError, TypeError):
                flash("全局默认值必须是正数")
                return redirect(url_for("admin.settings"))

        for ptype in PROJECT_TYPE_LABELS:
            field = f"retention_{ptype}_days"
            val = request.form.get(field, "").strip()
            key = f"retention_{ptype}_hours"
            if val:
                try:
                    hours = int(float(val) * 24)
                    if hours > 0:
                        set_setting(key, str(hours))
                    else:
                        delete_setting(key)
                except (ValueError, TypeError):
                    pass
            else:
                delete_setting(key)

        adjusted = 0
        new_override_types = {pt for pt in PROJECT_TYPE_LABELS if has_retention_override(pt)}
        for ptype in PROJECT_TYPE_LABELS:
            if ptype not in old_override_types and ptype not in new_override_types:
                continue
            new_hours = get_retention_hours(ptype)
            if new_hours != old_per_type[ptype]:
                adjusted += adjust_expires_for_type(ptype, old_per_type[ptype], new_hours)

        new_default = get_retention_hours("__nonexistent__")
        if new_default != old_default:
            adjusted += adjust_expires_for_default(
                old_default,
                new_default,
                excluded_project_types=old_override_types | new_override_types,
            )

        if adjusted:
            flash(f"保留周期设置已保存，已同步调整 {adjusted} 个项目的过期时间")
        else:
            flash("保留周期设置已保存")
        return redirect(url_for("admin.settings", tab="general"))

    current = get_all_retention_settings()
    tts_concurrency_raw = get_setting("tts_max_concurrency")
    try:
        tts_concurrency = int(tts_concurrency_raw) if tts_concurrency_raw else 12
    except (ValueError, TypeError):
        tts_concurrency = 12
    return render_template(
        "admin_settings.html",
        project_types=PROJECT_TYPE_LABELS,
        current=current,
        roas_rmb_per_usd=product_roas.format_decimal(product_roas.get_configured_rmb_per_usd()),
        media_languages=medias.list_languages_for_admin(),
        tts_max_concurrency=tts_concurrency,
        product_link_domains=product_link_domains.list_domains(include_disabled=True),
        active_tab=active_tab,
    )


@bp.route("/api/media-languages", methods=["GET"])
@login_required
@admin_required
def api_media_languages():
    return admin_flask_response(
        build_admin_payload_response({"items": medias.list_languages_for_admin()})
    )


@bp.route("/api/media-languages", methods=["POST"])
@login_required
@admin_required
def api_create_media_language():
    body = request.get_json(silent=True) or {}
    try:
        medias.create_language(
            body.get("code", ""),
            body.get("name_zh", ""),
            body.get("sort_order", 0),
            bool(body.get("enabled", True)),
            body.get("shopify_language_name", ""),
        )
    except ValueError as exc:
        return admin_flask_response(build_admin_error_response(str(exc), 400))
    _audit_admin_action(
        "admin_media_language_created",
        target_type="media_language",
        target_id=body.get("code", ""),
        target_label=body.get("name_zh", ""),
        detail={
            "code": body.get("code", ""),
            "enabled": bool(body.get("enabled", True)),
            "sort_order": body.get("sort_order", 0),
        },
    )
    return admin_flask_response(build_admin_ok_response(status_code=201))


@bp.route("/api/media-languages/<code>", methods=["PUT"])
@login_required
@admin_required
def api_update_media_language(code: str):
    body = request.get_json(silent=True) or {}
    try:
        medias.update_language(
            code,
            body.get("name_zh", ""),
            body.get("sort_order", 0),
            bool(body.get("enabled", True)),
            body.get("shopify_language_name", ""),
        )
    except ValueError as exc:
        return admin_flask_response(build_admin_error_response(str(exc), 400))
    _audit_admin_action(
        "admin_media_language_updated",
        target_type="media_language",
        target_id=code,
        target_label=body.get("name_zh", ""),
        detail={
            "enabled": bool(body.get("enabled", True)),
            "sort_order": body.get("sort_order", 0),
        },
    )
    return admin_flask_response(build_admin_ok_response())


@bp.route("/api/media-languages/<code>", methods=["DELETE"])
@login_required
@admin_required
def api_delete_media_language(code: str):
    try:
        medias.delete_language(code)
    except ValueError as exc:
        return admin_flask_response(build_admin_error_response(str(exc), 400))
    _audit_admin_action(
        "admin_media_language_deleted",
        target_type="media_language",
        target_id=code,
    )
    return admin_flask_response(build_admin_ok_response())


@bp.route("/api/image-translate/prompts", methods=["GET"])
@login_required
@admin_required
def get_image_translate_prompts():
    from appcore.image_translate_settings import (
        PRESETS,
        get_prompts_for_lang,
        list_all_prompts,
        list_image_translate_languages,
        is_image_translate_language_supported,
    )
    lang = (request.args.get("lang") or "").strip().lower()
    if lang:
        if not is_image_translate_language_supported(lang):
            return admin_flask_response(
                build_admin_error_response(f"unsupported lang: {lang}", 400)
            )
        return admin_flask_response(build_admin_payload_response(get_prompts_for_lang(lang)))
    return admin_flask_response(
        build_admin_payload_response(
            {
                "languages": list_image_translate_languages(),
                "presets": list(PRESETS),
                "prompts": list_all_prompts(),
            }
        )
    )


@bp.route("/api/image-translate/prompts", methods=["POST"])
@login_required
@admin_required
def set_image_translate_prompt():
    from appcore.image_translate_settings import (
        PRESETS,
        is_image_translate_language_supported,
        update_prompt,
    )
    body = request.get_json(silent=True) or {}
    preset = (body.get("preset") or "").strip().lower()
    lang = (body.get("lang") or "").strip().lower()
    value = (body.get("value") or "").strip()
    if preset not in PRESETS:
        return admin_flask_response(
            build_admin_error_response("preset must be cover or detail", 400)
        )
    if not is_image_translate_language_supported(lang):
        return admin_flask_response(
            build_admin_error_response(f"unsupported lang: {lang}", 400)
        )
    if not value:
        return admin_flask_response(build_admin_error_response("value required", 400))
    update_prompt(preset, lang, value)
    _audit_admin_action(
        "admin_image_translate_prompt_updated",
        target_type="image_translate_prompt",
        target_id=f"{preset}:{lang}",
        detail={"preset": preset, "lang": lang},
    )
    return admin_flask_response(build_admin_ok_response())


@bp.route("/voice-library/sync/<language>", methods=["POST"])
@login_required
@admin_required
def voice_library_sync(language: str):
    if language not in medias.list_enabled_language_codes():
        return admin_flask_response(build_admin_error_response("language not enabled", 400))
    try:
        sync_id = vlst.start_sync(language=language)
    except RuntimeError as exc:
        msg = str(exc)
        if "another sync" in msg:
            return admin_flask_response(build_admin_error_response(msg, 409))
        return admin_flask_response(build_admin_error_response(msg, 500))
    _audit_admin_action(
        "admin_voice_library_sync_started",
        target_type="voice_library",
        target_id=language,
        detail={"sync_id": sync_id},
    )
    return admin_flask_response(
        build_admin_payload_response({"sync_id": sync_id}, status_code=202)
    )


@bp.route("/voice-library/sync-status", methods=["GET"])
@login_required
@admin_required
def voice_library_sync_status():
    return admin_flask_response(
        build_admin_payload_response(
            {
                "current": vlst.get_current(),
                "summary": vlst.summarize(),
            }
        )
    )


@bp.route("/shopifyid-sync/trigger", methods=["POST"])
@login_required
@admin_required
def api_shopifyid_sync_trigger():
    try:
        result = shopifyid_sync_trigger.trigger()
    except RuntimeError as exc:
        return admin_flask_response(
            build_admin_error_response(str(exc), 500, latest=shopifyid_sync_trigger.latest_run())
        )
    status_code = 409 if result.get("already_running") else 202
    return admin_flask_response(build_admin_payload_response(result, status_code=status_code))


@bp.route("/shopifyid-sync/status", methods=["GET"])
@login_required
@admin_required
def api_shopifyid_sync_status():
    return admin_flask_response(
        build_admin_payload_response({"latest": shopifyid_sync_trigger.latest_run()})
    )
