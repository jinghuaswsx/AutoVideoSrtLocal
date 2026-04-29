"""小工具页面 Blueprint."""
from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint("tools", __name__, url_prefix="/tools")


@bp.route("/", methods=["GET"])
@login_required
def index():
    return render_template("tools.html")
