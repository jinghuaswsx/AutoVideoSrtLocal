from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from web.auth import admin_required
from appcore.users import list_users, create_user, set_active, get_by_username
from appcore.settings import (
    PROJECT_TYPE_LABELS,
    get_all_retention_settings,
    set_setting,
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
                        from appcore.db import execute as db_execute
                        db_execute("DELETE FROM system_settings WHERE `key` = %s", (key,))
                except (ValueError, TypeError):
                    pass
            else:
                # 留空 = 删除覆盖，回退到全局默认
                from appcore.db import execute as db_execute
                db_execute("DELETE FROM system_settings WHERE `key` = %s", (key,))

        flash("保留周期设置已保存")
        return redirect(url_for("admin.settings"))

    current = get_all_retention_settings()
    return render_template(
        "admin_settings.html",
        project_types=PROJECT_TYPE_LABELS,
        current=current,
    )
