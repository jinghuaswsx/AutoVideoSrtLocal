from __future__ import annotations

import json

from flask import Blueprint, render_template, abort
from flask_login import login_required, current_user

from appcore.db import query_one as db_query_one

bp = Blueprint("subtitle_removal", __name__)


@bp.route("/subtitle-removal")
@login_required
def upload_page():
    return render_template("subtitle_removal_upload.html")


@bp.route("/subtitle-removal/<task_id>")
@login_required
def detail_page(task_id: str):
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s AND type = 'subtitle_removal' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        abort(404)
    state = {}
    if row.get("state_json"):
        try:
            state = json.loads(row["state_json"])
        except Exception:
            state = {}
    return render_template("subtitle_removal_detail.html", project=row, state=state, task_id=task_id)
