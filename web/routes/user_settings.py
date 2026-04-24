from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from appcore.api_keys import DEFAULT_JIANYING_PROJECT_ROOT, resolve_extra, set_key

bp = Blueprint("user_settings", __name__, url_prefix="/user-settings")


@bp.route("", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        project_root = (
            request.form.get("jianying_project_root", "").strip()
            or DEFAULT_JIANYING_PROJECT_ROOT
        )
        set_key(current_user.id, "jianying", "", {"project_root": project_root})
        flash("用户设置已保存")
        return redirect(url_for("user_settings.index"))

    extra = resolve_extra(current_user.id, "jianying")
    project_root = (extra.get("project_root") or "").strip() or DEFAULT_JIANYING_PROJECT_ROOT
    return render_template(
        "user_settings.html",
        jianying_project_root=project_root,
        default_jianying_project_root=DEFAULT_JIANYING_PROJECT_ROOT,
    )
