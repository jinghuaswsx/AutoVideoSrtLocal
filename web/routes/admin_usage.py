from flask import Blueprint, render_template, request
from flask_login import login_required
from web.auth import admin_required
from appcore.db import query

bp = Blueprint("admin_usage", __name__, url_prefix="/admin")


@bp.route("/usage")
@login_required
@admin_required
def usage():
    service = request.args.get("service", "")
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")

    where = "WHERE 1=1"
    args = []
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
        SELECT u.username, ul.service, DATE(ul.called_at) AS day,
               COUNT(*) AS calls,
               SUM(ul.input_tokens) AS input_tokens,
               SUM(ul.output_tokens) AS output_tokens,
               SUM(ul.audio_duration_seconds) AS audio_seconds
        FROM usage_logs ul
        JOIN users u ON u.id = ul.user_id
        {where}
        GROUP BY u.username, ul.service, day
        ORDER BY day DESC, u.username
    """, tuple(args))
    return render_template("admin_usage.html", rows=rows, service=service,
                           date_from=date_from, date_to=date_to)
