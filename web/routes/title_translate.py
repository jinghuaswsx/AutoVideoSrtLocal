from __future__ import annotations

from flask import Blueprint, jsonify, render_template
from flask_login import login_required

from appcore import title_translate_settings

bp = Blueprint("title_translate", __name__)


@bp.route("/title-translate", methods=["GET"])
@login_required
def page():
    return render_template("title_translate.html")


@bp.route("/api/title-translate/languages", methods=["GET"])
@login_required
def api_languages():
    languages = [
        {
            "code": (row.get("code") or "").strip(),
            "name_zh": (row.get("name_zh") or "").strip(),
            "sort_order": int(row.get("sort_order") or 0),
        }
        for row in title_translate_settings.list_title_translate_languages()
    ]
    return jsonify({"languages": languages})
