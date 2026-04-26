"""F 子系统：员工产能报表 Blueprint."""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore import productivity_stats as ps_svc

bp = Blueprint("productivity_stats", __name__, url_prefix="/productivity-stats")


def _is_admin() -> bool:
    return getattr(current_user, "role", "") in ("admin", "superadmin") or \
        getattr(current_user, "is_admin", False)


def _admin_required():
    if not _is_admin():
        return jsonify({"error": "admin_required"}), 403
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
        return jsonify({
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "daily_throughput": [_serialize_row(r) for r in ps_svc.get_daily_throughput(from_dt=from_dt, to_dt=to_dt)],
            "pass_rate": [_serialize_row(r) for r in ps_svc.get_pass_rate(from_dt=from_dt, to_dt=to_dt)],
            "rework_rate": [_serialize_row(r) for r in ps_svc.get_rework_rate(from_dt=from_dt, to_dt=to_dt)],
        })
    except ValueError as e:
        return jsonify({"error": "bad_param", "detail": str(e)}), 400
    except Exception as e:
        return jsonify({"error": "internal", "detail": str(e)}), 500


def _serialize_row(r: dict) -> dict:
    """Convert datetime / Decimal to JSON-friendly types."""
    out = {}
    for k, v in r.items():
        if hasattr(v, 'isoformat'):
            out[k] = v.isoformat()
        elif hasattr(v, '__float__') and not isinstance(v, (int, float, bool)):
            out[k] = float(v)
        else:
            out[k] = v
    return out
