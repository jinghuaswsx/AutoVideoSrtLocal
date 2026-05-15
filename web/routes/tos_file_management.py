from __future__ import annotations

from functools import wraps

from flask import Blueprint, abort, render_template, request, redirect, url_for, jsonify
from flask_login import current_user, login_required

from appcore import tos_file_management
from appcore import infra_credentials


bp = Blueprint("tos_file_management", __name__, url_prefix="/admin/tos-files")


def superadmin_only(fn):
    @wraps(fn)
    def _wrap(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(current_user, "is_superadmin", False):
            abort(403)
        return fn(*args, **kwargs)
    return _wrap


@bp.route("", methods=["GET"])
@login_required
@superadmin_only
def page():
    channel = request.args.get("channel", "tos_wj")
    summary = tos_file_management.latest_scan_summary(channel)
    channel_options = infra_credentials.tos_channel_options()
    return render_template(
        "tos_file_management.html",
        channel=channel,
        summary=summary,
        channel_options=channel_options,
    )


@bp.route("/scan", methods=["POST"])
@login_required
@superadmin_only
def scan():
    channel = request.form.get("channel", "tos_wj")
    tos_file_management.run_inventory_scan(channel, triggered_by=getattr(current_user, "id", None))
    return redirect(url_for("tos_file_management.page", channel=channel))


@bp.route("/sync", methods=["POST"])
@login_required
@superadmin_only
def sync():
    channel = request.form.get("channel", "tos_wj")
    dry_run = request.form.get("dry_run", "1") == "1"
    tos_file_management.run_channel_sync(
        channel,
        dry_run=dry_run,
        triggered_by=getattr(current_user, "id", None),
    )
    return redirect(url_for("tos_file_management.page", channel=channel))


@bp.route("/api/files", methods=["GET"])
@login_required
@superadmin_only
def api_files():
    channel = request.args.get("channel", "tos_wj")
    module_code = request.args.get("module")
    sync_status = request.args.get("status")
    q = request.args.get("q")
    page = max(1, int(request.args.get("page", 1)))
    page_size = min(100, max(10, int(request.args.get("page_size", 50))))

    filters = tos_file_management.TosFileFilters(
        target_channel_code=channel,
        module_code=module_code,
        sync_status=sync_status,
        q=q,
        page=page,
        page_size=page_size,
    )
    result = tos_file_management.list_mappings(filters)
    return jsonify(result)
