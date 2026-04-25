from __future__ import annotations
import json
from flask import Blueprint, render_template, abort, redirect, url_for
from flask_login import login_required, current_user
from appcore.av_translate_inputs import (
    AV_TARGET_LANGUAGE_OPTIONS,
    AV_TARGET_MARKET_OPTIONS,
    build_default_av_translate_inputs,
)
from appcore.db import query, query_one
from appcore.task_recovery import recover_all_interrupted_tasks, recover_project_if_needed
from appcore.settings import get_retention_hours

bp = Blueprint("projects", __name__)


@bp.route("/")
@login_required
def root():
    return redirect(url_for("medias.index"))


@bp.route("/projects")
@login_required
def index():
    recover_all_interrupted_tasks()
    rows = query(
        """SELECT id, original_filename, display_name, thumbnail_path, status, created_at, expires_at, deleted_at
           FROM projects WHERE user_id = %s AND type = 'translation' AND deleted_at IS NULL ORDER BY created_at DESC""",
        (current_user.id,),
    )
    from datetime import datetime
    return render_template("projects.html", projects=rows, now=datetime.now(),
                           retention_hours=get_retention_hours("translation"))


@bp.route("/video-translate-av-sync")
@login_required
def av_sync_page():
    from appcore.api_keys import get_key

    try:
        translate_pref = get_key(current_user.id, "translate_pref") or "openrouter"
    except Exception:
        translate_pref = "openrouter"
    return render_template(
        "video_translate_av_sync.html",
        translate_pref=translate_pref,
        av_target_languages=AV_TARGET_LANGUAGE_OPTIONS,
        av_target_markets=AV_TARGET_MARKET_OPTIONS,
        av_translate_defaults=build_default_av_translate_inputs(),
    )


@bp.route("/projects/<task_id>")
@login_required
def detail(task_id: str):
    recover_project_if_needed(task_id, "translation")
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
    from appcore.api_keys import get_key
    try:
        translate_pref = get_key(current_user.id, "translate_pref") or "openrouter"
    except Exception:
        translate_pref = "openrouter"
    return render_template(
        "project_detail.html",
        project=row,
        state=state,
        initial_task_json=json.dumps(state, ensure_ascii=False),
        translate_pref=translate_pref,
        av_target_languages=AV_TARGET_LANGUAGE_OPTIONS,
        av_target_markets=AV_TARGET_MARKET_OPTIONS,
        av_translate_defaults=build_default_av_translate_inputs(),
    )


@bp.route("/projects/<task_id>/download/tos/<path:tos_key>")
@login_required
def download_tos(task_id: str, tos_key: str):
    del tos_key
    row = query_one(
        "SELECT id, deleted_at FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    if not row:
        abort(404)
    if row.get("deleted_at"):
        abort(410)
    abort(410)
