from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from web.auth import admin_required
from appcore.db import query

bp = Blueprint("admin_usage", __name__, url_prefix="/admin")
# 普通用户用量页 (无 /admin 前缀)
user_usage_bp = Blueprint("user_usage", __name__)


@bp.route("/usage")
@login_required
@admin_required
def usage():
    return _render_usage(admin=True)


@user_usage_bp.route("/my-usage")
@login_required
def my_usage():
    """普通用户查看自己的用量。"""
    return _render_usage(admin=False)


def _render_usage(admin: bool):
    from datetime import date as _date
    today = _date.today().isoformat()
    service = request.args.get("service", "")
    date_from = request.args.get("from", "") or today
    date_to = request.args.get("to", "") or today

    where = "WHERE 1=1"
    args = []

    if not admin:
        where += " AND ul.user_id = %s"
        args.append(current_user.id)

    if service:
        where += " AND ul.service = %s"
        args.append(service)
    if date_from:
        where += " AND DATE(ul.called_at) >= %s"
        args.append(date_from)
    if date_to:
        where += " AND DATE(ul.called_at) <= %s"
        args.append(date_to)

    rows = query(f"""
        SELECT u.username, ul.service, ul.model_name,
               DATE(ul.called_at) AS day,
               COUNT(*) AS calls,
               SUM(ul.input_tokens) AS input_tokens,
               SUM(ul.output_tokens) AS output_tokens,
               SUM(ul.audio_duration_seconds) AS audio_seconds
        FROM usage_logs ul
        JOIN users u ON u.id = ul.user_id
        {where}
        GROUP BY u.username, ul.service, ul.model_name, day
        ORDER BY day DESC, u.username
    """, tuple(args))

    summary = query(f"""
        SELECT COUNT(*) AS total_calls,
               COALESCE(SUM(ul.input_tokens), 0) AS total_input_tokens,
               COALESCE(SUM(ul.output_tokens), 0) AS total_output_tokens,
               COALESCE(SUM(ul.audio_duration_seconds), 0) AS total_audio_seconds
        FROM usage_logs ul
        {where}
    """, tuple(args))
    summary = summary[0] if summary else {
        "total_calls": 0, "total_input_tokens": 0,
        "total_output_tokens": 0, "total_audio_seconds": 0,
    }

    services = query("SELECT DISTINCT service FROM usage_logs ORDER BY service")
    service_list = [r["service"] for r in services]

    return render_template("admin_usage.html",
                           rows=rows, service=service,
                           date_from=date_from, date_to=date_to,
                           summary=summary, service_list=service_list,
                           admin_mode=admin)
