from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from appcore.api_keys import set_key, get_all

bp = Blueprint("settings", __name__)

SERVICES = [
    ("doubao_asr", "豆包 ASR", ["key_value", "app_id", "cluster"]),
    ("elevenlabs", "ElevenLabs", ["key_value"]),
    ("openrouter", "OpenRouter", ["key_value"]),
]


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        for service, _, fields in SERVICES:
            key_value = request.form.get(f"{service}_key", "").strip()
            if key_value:
                extra = {}
                for f in fields[1:]:  # extra fields beyond key_value
                    val = request.form.get(f"{service}_{f}", "").strip()
                    if val:
                        extra[f] = val
                set_key(current_user.id, service, key_value, extra or None)
        flash("API Key 已保存")
        return redirect(url_for("settings.index"))
    keys = get_all(current_user.id)
    return render_template("settings.html", keys=keys, services=SERVICES)
