from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from web.auth import admin_required
from appcore.users import list_users, create_user, set_active, get_by_username

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
