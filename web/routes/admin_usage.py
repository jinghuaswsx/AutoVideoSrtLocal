from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from web.auth import admin_required
from appcore import usage_log

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

    report = usage_log.get_usage_report(
        admin=admin,
        user_id=current_user.id,
        service=service,
        date_from=date_from,
        date_to=date_to,
    )

    return render_template("admin_usage.html",
                           rows=report["rows"], service=service,
                           date_from=date_from, date_to=date_to,
                           summary=report["summary"], service_list=report["service_list"],
                           admin_mode=admin)
