from __future__ import annotations
import json
from flask import Blueprint, render_template, abort
from flask_login import login_required, current_user
from appcore.db import query, query_one

bp = Blueprint("projects", __name__)


@bp.route("/")
@login_required
def index():
    rows = query(
        """SELECT id, original_filename, thumbnail_path, status, created_at, expires_at, deleted_at
           FROM projects WHERE user_id = %s ORDER BY created_at DESC""",
        (current_user.id,),
    )
    return render_template("projects.html", projects=rows)


@bp.route("/projects/<task_id>")
@login_required
def detail(task_id: str):
    row = query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    if not row:
        abort(404)
    state = {}
    if row.get("state_json"):
        try:
            state = json.loads(row["state_json"])
        except Exception:
            pass
    return render_template(
        "project_detail.html",
        project=row,
        state=state,
        initial_task_json=json.dumps(state, ensure_ascii=False),
    )
