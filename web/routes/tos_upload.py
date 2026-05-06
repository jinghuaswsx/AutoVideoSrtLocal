from __future__ import annotations

from flask import Blueprint
from flask_login import login_required
from web.services.tos_upload import (
    build_tos_upload_bootstrap_disabled_response,
    build_tos_upload_complete_disabled_response,
    tos_upload_flask_response,
)

bp = Blueprint("tos_upload", __name__, url_prefix="/api/tos-upload")


@bp.route("/bootstrap", methods=["POST"])
@login_required
def bootstrap_upload():
    result = build_tos_upload_bootstrap_disabled_response()
    return tos_upload_flask_response(result)


@bp.route("/complete", methods=["POST"])
@login_required
def complete_upload():
    result = build_tos_upload_complete_disabled_response()
    return tos_upload_flask_response(result)
