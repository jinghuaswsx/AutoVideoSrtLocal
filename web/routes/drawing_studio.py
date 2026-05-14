from __future__ import annotations

from flask import Blueprint, redirect
from flask_login import current_user, login_required

from appcore.drawing_studio_sso import (
    DrawingStudioSsoConfigError,
    build_drawing_studio_sso_url,
)


bp = Blueprint("drawing_studio", __name__, url_prefix="/drawing-studio")


@bp.route("/sso")
@login_required
def sso():
    try:
        target = build_drawing_studio_sso_url(
            user_id=current_user.id,
            username=current_user.username,
            role=getattr(current_user, "role", "user"),
        )
    except DrawingStudioSsoConfigError as exc:
        return str(exc), 503
    return redirect(target)
