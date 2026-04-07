from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from appcore.api_keys import DEFAULT_JIANYING_PROJECT_ROOT, get_all, set_key

bp = Blueprint("settings", __name__)

SERVICES = [
    ("doubao_asr", "豆包 ASR", ["key_value", "app_id", "cluster"]),
    ("openrouter", "OpenRouter", ["key_value", "base_url", "model_id"]),
    ("doubao_llm", "豆包翻译", ["key_value", "base_url", "model_id"]),
    ("elevenlabs", "ElevenLabs", ["key_value"]),
]

TRANSLATE_PROVIDERS = ["openrouter", "doubao"]
SECRET_SERVICES = {service for service, _, _ in SERVICES}


def _sanitize_keys_for_display(keys: dict[str, dict]) -> dict[str, dict]:
    display_keys: dict[str, dict] = {}
    for service, payload in (keys or {}).items():
        entry = {"key_value": "", "extra": {}}
        if isinstance(payload, dict):
            entry["key_value"] = payload.get("key_value", "")
            entry["extra"] = dict(payload.get("extra") or {})
        if service in SECRET_SERVICES:
            entry["key_value"] = ""
        display_keys[service] = entry
    return display_keys


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        existing_keys = get_all(current_user.id)
        for service, _, fields in SERVICES:
            key_value = request.form.get(f"{service}_key", "").strip()
            extra = {}
            for field in fields[1:]:
                value = request.form.get(f"{service}_{field}", "").strip()
                if value:
                    extra[field] = value
            if key_value:
                set_key(current_user.id, service, key_value, extra or None)
            elif extra:
                preserved_key = existing_keys.get(service, {}).get("key_value", "")
                set_key(current_user.id, service, preserved_key, extra or None)

        # 保存默认翻译模型偏好
        translate_pref = request.form.get("translate_pref", "openrouter").strip()
        if translate_pref in TRANSLATE_PROVIDERS:
            set_key(current_user.id, "translate_pref", translate_pref)

        jianying_project_root = request.form.get("jianying_project_root", "").strip() or DEFAULT_JIANYING_PROJECT_ROOT
        set_key(current_user.id, "jianying", "", {"project_root": jianying_project_root})

        flash("配置已保存")
        return redirect(url_for("settings.index"))

    keys = get_all(current_user.id)
    jianying_project_root = keys.get("jianying", {}).get("extra", {}).get("project_root") or DEFAULT_JIANYING_PROJECT_ROOT
    translate_pref = keys.get("translate_pref", {}).get("key_value", "") or "openrouter"
    return render_template(
        "settings.html",
        keys=_sanitize_keys_for_display(keys),
        services=SERVICES,
        jianying_project_root=jianying_project_root,
        default_jianying_project_root=DEFAULT_JIANYING_PROJECT_ROOT,
        translate_pref=translate_pref,
    )
