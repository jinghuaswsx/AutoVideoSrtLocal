from __future__ import annotations
import json
from flask import Blueprint, render_template, abort, redirect
from flask_login import login_required, current_user
from appcore.db import query, query_one

bp = Blueprint("projects", __name__)


@bp.route("/")
@login_required
def index():
    rows = query(
        """SELECT id, original_filename, display_name, thumbnail_path, status, created_at, expires_at, deleted_at
           FROM projects WHERE user_id = %s AND deleted_at IS NULL ORDER BY created_at DESC""",
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


@bp.route("/projects/<task_id>/download/tos/<path:tos_key>")
@login_required
def download_tos(task_id: str, tos_key: str):
    row = query_one(
        "SELECT id, deleted_at FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    if not row:
        abort(404)
    if row.get("deleted_at"):
        abort(410)
    try:
        import tos as tos_sdk
        import config
        client = tos_sdk.TosClientV2(
            ak=config.TOS_ACCESS_KEY, sk=config.TOS_SECRET_KEY,
            endpoint=config.TOS_ENDPOINT, region=config.TOS_REGION,
        )
        signed_url = client.pre_signed_url("GET", config.TOS_BUCKET, tos_key, expires=3600).signed_url
        return redirect(signed_url)
    except Exception:
        abort(404)
