"""视频翻译（测试）模块蓝图骨架。

仅实现列表/详情两个页面路由，为 Task 14 起的业务实现留出挂载点；
模块内部字段与流水线均遵循 ``appcore.task_state.create_translate_lab``
的 7 步骨架。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from flask import Blueprint, render_template, abort
from flask_login import login_required, current_user

from appcore.db import query as db_query, query_one as db_query_one
from appcore.settings import get_retention_hours

log = logging.getLogger(__name__)

bp = Blueprint("translate_lab", __name__)


@bp.route("/translate-lab")
@login_required
def index():
    rows = db_query(
        """SELECT id, original_filename, display_name, thumbnail_path, status,
                  created_at, expires_at, deleted_at
           FROM projects
           WHERE user_id = %s AND type = 'translate_lab' AND deleted_at IS NULL
           ORDER BY created_at DESC""",
        (current_user.id,),
    )
    try:
        retention_hours = get_retention_hours("translate_lab")
    except Exception:
        log.warning("get_retention_hours failed for translate_lab", exc_info=True)
        retention_hours = 168
    return render_template(
        "translate_lab_list.html",
        projects=rows or [],
        now=datetime.now(),
        retention_hours=retention_hours,
    )


@bp.route("/translate-lab/<task_id>")
@login_required
def detail(task_id: str):
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    if not row or row.get("type") != "translate_lab":
        abort(404)

    state = {}
    if row.get("state_json"):
        try:
            state = json.loads(row["state_json"])
        except Exception:
            state = {}

    return render_template(
        "translate_lab_detail.html",
        project=row,
        state=state,
    )
