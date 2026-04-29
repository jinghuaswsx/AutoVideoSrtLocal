from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from web.auth import admin_required, superadmin_required
from appcore import medias, product_roas
from appcore import voice_library_sync_task as vlst
from appcore.users import (
    list_users, create_user, set_active, get_by_username,
    update_role, update_permissions, reset_permissions_to_role_default,
)
from appcore.permissions import (
    ROLE_ADMIN, ROLE_USER, ROLE_SUPERADMIN,
    ROLE_LABELS, ROLES, grouped_permissions, PERMISSION_META,
    default_permissions_for_role, normalize_permissions,
)
from appcore.settings import (
    PROJECT_TYPE_LABELS,
    get_all_retention_settings,
    get_retention_hours,
    has_retention_override,
    set_setting,
    adjust_expires_for_type,
    adjust_expires_for_default,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")


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
                    create_user(username, password, role=role)
                    flash(f"用户 '{username}' 创建成功")
                except ValueError as exc:
                    error = str(exc)
                return redirect(url_for("admin.users"))
        elif action == "toggle_active":
            try:
                user_id = int(request.form.get("user_id"))
            except (TypeError, ValueError):
                error = "无效的用户 ID"
                all_users = list_users()
                return render_template("admin_users.html", users=all_users, error=error), 400
            active = request.form.get("active") == "1"
            set_active(user_id, active)
            return redirect(url_for("admin.users"))
        elif action == "update_role":
            try:
                user_id = int(request.form.get("user_id"))
            except (TypeError, ValueError):
                error = "无效的用户 ID"
                all_users = list_users()
                return render_template("admin_users.html", users=all_users, error=error), 400
            new_role = request.form.get("new_role", "").strip()
            if new_role not in (ROLE_ADMIN, ROLE_USER):
                error = f"无效的角色: {new_role}"
            else:
                try:
                    update_role(user_id, new_role)
                    flash("角色已更新，权限已同步重置为新角色默认值")
                except ValueError as exc:
                    error = str(exc)
            return redirect(url_for("admin.users"))
    all_users = list_users()
    # 为模板序列化 permissions JSON
    import json as _json
    for u in all_users:
        raw = u.get("permissions")
        if raw is None:
            u["permissions_json"] = "{}"
        elif isinstance(raw, dict):
            u["permissions_json"] = _json.dumps(raw, ensure_ascii=False)
        elif isinstance(raw, str):
            u["permissions_json"] = raw
        else:
            u["permissions_json"] = _json.dumps(raw) if raw else "{}"
    return render_template("admin_users.html", users=all_users, error=error,
                           role_labels=ROLE_LABELS, current_user_id=current_user.id,
                           perm_groups=grouped_permissions(),
                           role_defaults={r: default_permissions_for_role(r) for r in ROLES})


@bp.route("/api/users/<int:user_id>/permissions", methods=["GET"])
@login_required
@superadmin_required
def get_user_permissions(user_id: int):
    from appcore.users import get_by_id
    from appcore.permissions import merge_with_defaults
    user = get_by_id(user_id)
    if not user:
        return jsonify({"error": "用户不存在"}), 404
    effective = merge_with_defaults(user["role"], _coerce_json(user.get("permissions")))
    is_superadmin = user["role"] == ROLE_SUPERADMIN
    return jsonify({
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
    })


@bp.route("/api/users/<int:user_id>/role", methods=["PUT"])
@login_required
@superadmin_required
def api_update_user_role(user_id: int):
    from appcore.users import get_by_id
    user = get_by_id(user_id)
    if not user:
        return jsonify({"error": "用户不存在"}), 404
    if user["role"] == ROLE_SUPERADMIN:
        return jsonify({"error": "不能修改超级管理员角色"}), 403
    body = request.get_json(silent=True) or {}
    new_role = (body.get("role") or "").strip()
    if new_role not in (ROLE_ADMIN, ROLE_USER):
        return jsonify({"error": f"无效的角色: {new_role}"}), 400
    try:
        update_role(user_id, new_role)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "role": new_role,
                     "role_label": ROLE_LABELS.get(new_role, new_role)})


@bp.route("/api/users/<int:user_id>/permissions", methods=["PUT"])
@login_required
@superadmin_required
def set_user_permissions(user_id: int):
    from appcore.users import get_by_id
    user = get_by_id(user_id)
    if not user:
        return jsonify({"error": "用户不存在"}), 404
    if user["role"] == ROLE_SUPERADMIN:
        return jsonify({"error": "超级管理员权限不可修改"}), 403
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    if action == "reset":
        cleaned = reset_permissions_to_role_default(user_id)
    else:
        cleaned = update_permissions(user_id, body.get("permissions"))
    return jsonify({"ok": True, "permissions": cleaned})


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


@bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings():
    if request.method == "POST":
        from appcore.db import execute as db_execute

        raw_roas_rate = request.form.get("material_roas_rmb_per_usd", "").strip()
        if not raw_roas_rate:
            raw_roas_rate = product_roas.format_decimal(product_roas.DEFAULT_RMB_PER_USD)
        try:
            roas_rate = product_roas.validate_rmb_per_usd(raw_roas_rate)
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("admin.settings"))
        set_setting(product_roas.RMB_PER_USD_SETTING_KEY, product_roas.format_decimal(roas_rate))

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
                        db_execute("DELETE FROM system_settings WHERE `key` = %s", (key,))
                except (ValueError, TypeError):
                    pass
            else:
                db_execute("DELETE FROM system_settings WHERE `key` = %s", (key,))

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
        return redirect(url_for("admin.settings"))

    current = get_all_retention_settings()
    return render_template(
        "admin_settings.html",
        project_types=PROJECT_TYPE_LABELS,
        current=current,
        roas_rmb_per_usd=product_roas.format_decimal(product_roas.get_configured_rmb_per_usd()),
        media_languages=medias.list_languages_for_admin(),
    )


@bp.route("/api/media-languages", methods=["GET"])
@login_required
@admin_required
def api_media_languages():
    return jsonify({"items": medias.list_languages_for_admin()})


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
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True}), 201


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
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@bp.route("/api/media-languages/<code>", methods=["DELETE"])
@login_required
@admin_required
def api_delete_media_language(code: str):
    try:
        medias.delete_language(code)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


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
            return jsonify({"error": f"unsupported lang: {lang}"}), 400
        return jsonify(get_prompts_for_lang(lang))
    return jsonify({
        "languages": list_image_translate_languages(),
        "presets": list(PRESETS),
        "prompts": list_all_prompts(),
    })


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
        return jsonify({"error": "preset must be cover or detail"}), 400
    if not is_image_translate_language_supported(lang):
        return jsonify({"error": f"unsupported lang: {lang}"}), 400
    if not value:
        return jsonify({"error": "value required"}), 400
    update_prompt(preset, lang, value)
    return jsonify({"ok": True})


@bp.route("/voice-library/sync/<language>", methods=["POST"])
@login_required
@admin_required
def voice_library_sync(language: str):
    if language not in medias.list_enabled_language_codes():
        return jsonify({"error": "language not enabled"}), 400
    try:
        sync_id = vlst.start_sync(language=language)
    except RuntimeError as exc:
        msg = str(exc)
        if "another sync" in msg:
            return jsonify({"error": msg}), 409
        return jsonify({"error": msg}), 500
    return jsonify({"sync_id": sync_id}), 202


@bp.route("/voice-library/sync-status", methods=["GET"])
@login_required
@admin_required
def voice_library_sync_status():
    return jsonify({
        "current": vlst.get_current(),
        "summary": vlst.summarize(),
    })
