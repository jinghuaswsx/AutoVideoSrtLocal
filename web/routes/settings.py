from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from appcore.api_keys import DEFAULT_JIANYING_PROJECT_ROOT, get_all, set_key
from appcore.gemini import VIDEO_CAPABLE_MODELS

bp = Blueprint("settings", __name__)

SERVICES = [
    ("doubao_asr", "豆包 ASR", ["key_value", "app_id", "cluster"]),
    ("openrouter", "OpenRouter", ["key_value", "base_url", "model_id"]),
    ("gemini", "Google Gemini", ["key_value", "model_id"]),
    ("gemini_video_analysis", "Gemini 视频分析", ["key_value", "model_id"]),
    ("doubao_llm", "豆包翻译", ["key_value", "base_url", "model_id"]),
    ("elevenlabs", "ElevenLabs", ["key_value"]),
]

TRANSLATE_PROVIDERS = ["openrouter", "doubao"]


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        for service, _, fields in SERVICES:
            key_value = request.form.get(f"{service}_key", "").strip()
            extra = {}
            for field in fields[1:]:
                value = request.form.get(f"{service}_{field}", "").strip()
                if value:
                    extra[field] = value
            if key_value or extra:
                set_key(current_user.id, service, key_value, extra or None)

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
        keys=keys,
        services=SERVICES,
        jianying_project_root=jianying_project_root,
        default_jianying_project_root=DEFAULT_JIANYING_PROJECT_ROOT,
        translate_pref=translate_pref,
        video_analysis_models=VIDEO_CAPABLE_MODELS,
    )
