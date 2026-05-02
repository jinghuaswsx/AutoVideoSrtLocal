from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import current_user, login_user, logout_user, login_required
from appcore import system_audit
from appcore.users import get_by_username, check_password
from web.auth import User

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        row = get_by_username(username)
        if row and row["is_active"] and check_password(password, row["password_hash"]):
            # 标记 session 为持久化，使其遵循 PERMANENT_SESSION_LIFETIME（1 个月）
            session.permanent = True
            user = User(row)
            login_user(user, remember=True)
            system_audit.record_from_request(
                user=user,
                request_obj=request,
                action="login_success",
                module="auth",
                target_type="user",
                target_id=row["id"],
                target_label=row["username"],
            )
            return redirect(url_for("medias.index"))
        system_audit.record_from_request(
            user=None,
            request_obj=request,
            action="login_failed",
            module="auth",
            target_type="user",
            target_label=username,
            status="failed",
            detail={"username": username},
        )
        flash("用户名或密码错误")
    return render_template("login.html")


@bp.route("/logout")
@login_required
def logout():
    system_audit.record_from_request(
        user=current_user,
        request_obj=request,
        action="logout",
        module="auth",
        target_type="user",
        target_id=current_user.id,
        target_label=current_user.username,
    )
    logout_user()
    return redirect(url_for("auth.login"))
