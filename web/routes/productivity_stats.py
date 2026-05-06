"""F 子系统：员工产能报表 Blueprint."""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from appcore import productivity_stats as ps_svc
from web.services.productivity_stats import (
    build_productivity_stats_admin_required_response,
    build_productivity_stats_bad_param_response,
    build_productivity_stats_internal_error_response,
    build_productivity_stats_summary_response,
    productivity_stats_flask_response,
)

bp = Blueprint("productivity_stats", __name__, url_prefix="/productivity-stats")


def _is_admin() -> bool:
    return getattr(current_user, "role", "") in ("admin", "superadmin") or \
        getattr(current_user, "is_admin", False)


def _admin_required():
    if not _is_admin():
        return productivity_stats_flask_response(
            build_productivity_stats_admin_required_response()
        )
    return None


def _parse_window():
    days = request.args.get("days")
    from_str = request.args.get("from")
    to_str = request.args.get("to")
    now = datetime.now()
    if from_str and to_str:
        from_dt = datetime.strptime(from_str, "%Y-%m-%d")
        to_dt = datetime.strptime(to_str, "%Y-%m-%d") + timedelta(days=1)
    else:
        d = int(days) if days else 30
        if d not in (7, 30, 60, 90):
            d = 30
        from_dt = (now - timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
        to_dt = now + timedelta(seconds=1)
    return from_dt, to_dt


@bp.route("/", methods=["GET"])
@login_required
def index():
    if not _is_admin():
        return "<h1>403</h1><p>仅管理员可访问</p>", 403
    return render_template("productivity_stats.html")


@bp.route("/api/summary", methods=["GET"])
@login_required
def api_summary():
    deny = _admin_required()
    if deny: return deny
    try:
        from_dt, to_dt = _parse_window()
        return productivity_stats_flask_response(
            build_productivity_stats_summary_response(
                from_dt=from_dt,
                to_dt=to_dt,
                daily_throughput=ps_svc.get_daily_throughput(from_dt=from_dt, to_dt=to_dt),
                pass_rate=ps_svc.get_pass_rate(from_dt=from_dt, to_dt=to_dt),
                rework_rate=ps_svc.get_rework_rate(from_dt=from_dt, to_dt=to_dt),
            )
        )
    except ValueError as e:
        return productivity_stats_flask_response(
            build_productivity_stats_bad_param_response(e)
        )
    except Exception as e:
        return productivity_stats_flask_response(
            build_productivity_stats_internal_error_response(e)
        )
