from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required
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
            login_user(User(row), remember=True)
            return redirect(url_for("projects.index"))
        flash("用户名或密码错误")
    return render_template("login.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
