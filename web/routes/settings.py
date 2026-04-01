from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from appcore.api_keys import DEFAULT_JIANYING_PROJECT_ROOT, get_all, set_key

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
                for field in fields[1:]:
                    value = request.form.get(f"{service}_{field}", "").strip()
                    if value:
                        extra[field] = value
                set_key(current_user.id, service, key_value, extra or None)

        jianying_project_root = request.form.get("jianying_project_root", "").strip() or DEFAULT_JIANYING_PROJECT_ROOT
        set_key(current_user.id, "jianying", "", {"project_root": jianying_project_root})

        flash("配置已保存")
        return redirect(url_for("settings.index"))

    keys = get_all(current_user.id)
    jianying_project_root = keys.get("jianying", {}).get("extra", {}).get("project_root") or DEFAULT_JIANYING_PROJECT_ROOT
    return render_template(
        "settings.html",
        keys=keys,
        services=SERVICES,
        jianying_project_root=jianying_project_root,
        default_jianying_project_root=DEFAULT_JIANYING_PROJECT_ROOT,
    )
