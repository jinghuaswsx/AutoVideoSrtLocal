from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required
from web.auth import admin_required
from appcore import medias
from appcore.users import list_users, create_user, set_active, get_by_username
from appcore.settings import (
    PROJECT_TYPE_LABELS,
    get_all_retention_settings,
    get_retention_hours,
    set_setting,
    adjust_expires_for_type,
    adjust_expires_for_default,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/users", methods=["GET", "POST"])
@login_required
@admin_required
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
                create_user(username, password, role=role)
                flash(f"用户 '{username}' 创建成功")
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
    all_users = list_users()
    return render_template("admin_users.html", users=all_users, error=error)


@bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings():
    if request.method == "POST":
        from appcore.db import execute as db_execute

        # ── 记住旧值，用于计算 delta ──
        old_default = get_retention_hours("__nonexistent__")  # 纯全局默认
        old_per_type = {pt: get_retention_hours(pt) for pt in PROJECT_TYPE_LABELS}

        # 保存全局默认值
        default_days = request.form.get("retention_default_days", "").strip()
        if default_days:
            try:
                hours = int(float(default_days) * 24)
                if hours > 0:
                    set_setting("retention_default_hours", str(hours))
            except (ValueError, TypeError):
                flash("全局默认值必须是正数")
                return redirect(url_for("admin.settings"))

        # 保存各模块覆盖值
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
                # 留空 = 删除覆盖，回退到全局默认
                db_execute("DELETE FROM system_settings WHERE `key` = %s", (key,))

        # ── 同步调整已有项目的 expires_at ──
        adjusted = 0
        for ptype in PROJECT_TYPE_LABELS:
            new_hours = get_retention_hours(ptype)
            if new_hours != old_per_type[ptype]:
                adjusted += adjust_expires_for_type(ptype, old_per_type[ptype], new_hours)

        # 全局默认变更：调整没有模块覆盖的项目
        new_default = get_retention_hours("__nonexistent__")
        if new_default != old_default:
            adjusted += adjust_expires_for_default(old_default, new_default)

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
        SUPPORTED_LANGS,
        get_prompts_for_lang,
        list_all_prompts,
    )
    lang = (request.args.get("lang") or "").strip().lower()
    if lang:
        if lang not in SUPPORTED_LANGS:
            return jsonify({"error": f"unsupported lang: {lang}"}), 400
        return jsonify(get_prompts_for_lang(lang))
    return jsonify({
        "languages": list(SUPPORTED_LANGS),
        "presets": list(PRESETS),
        "prompts": list_all_prompts(),
    })


@bp.route("/api/image-translate/prompts", methods=["POST"])
@login_required
@admin_required
def set_image_translate_prompt():
    from appcore.image_translate_settings import PRESETS, SUPPORTED_LANGS, update_prompt
    body = request.get_json(silent=True) or {}
    preset = (body.get("preset") or "").strip().lower()
    lang = (body.get("lang") or "").strip().lower()
    value = (body.get("value") or "").strip()
    if preset not in PRESETS:
        return jsonify({"error": "preset must be cover or detail"}), 400
    if lang not in SUPPORTED_LANGS:
        return jsonify({"error": f"lang must be one of {SUPPORTED_LANGS}"}), 400
    if not value:
        return jsonify({"error": "value required"}), 400
    update_prompt(preset, lang, value)
    return jsonify({"ok": True})
