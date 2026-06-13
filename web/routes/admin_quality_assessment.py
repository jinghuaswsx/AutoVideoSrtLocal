from __future__ import annotations

from flask import Blueprint, render_template, request
from flask_login import login_required

from appcore import quality_assessment
from web.auth import admin_required


bp = Blueprint("admin_quality_assessment", __name__, url_prefix="/admin")


def _parse_days() -> int:
    try:
        value = int(request.args.get("days") or 30)
    except (TypeError, ValueError):
        value = 30
    return max(1, min(value, 365))


@bp.route("/translation-quality")
@login_required
@admin_required
def translation_quality_summary():
    days = _parse_days()
    return render_template(
        "admin_quality_assessment.html",
        days=days,
        rows=quality_assessment.summarize_recent(days=days),
    )
