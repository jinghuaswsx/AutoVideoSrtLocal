"""API 设置页。

3-tab 结构：
  - providers: 服务商接入（沿用原 SERVICES 字段 + 主线 translate_pref）
  - bindings:  模块模型分配（UseCase × Provider × Model，新增）
  - general:   通用设置（剪映目录、图片翻译通道）

演进式重写：所有主线已有字段（TRANSLATE_PROVIDERS / SERVICES / DEFAULT_TRANSLATE_PROVIDER）
保持不变，以免破坏已有用户数据。只新增 Tab 2 Bindings 部分。
"""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from appcore import llm_bindings
from appcore.api_keys import DEFAULT_JIANYING_PROJECT_ROOT, get_all, set_key
from appcore.gemini import VIDEO_CAPABLE_MODELS
from appcore.image_translate_settings import (
    CHANNEL_LABELS as IMAGE_TRANSLATE_CHANNEL_LABELS,
    CHANNELS as IMAGE_TRANSLATE_CHANNELS,
    get_channel as get_image_translate_channel,
    set_channel as set_image_translate_channel,
)
from appcore.llm_use_cases import MODULE_LABELS, USE_CASES

bp = Blueprint("settings", __name__)

SERVICES = [
    ("doubao_asr", "豆包 ASR", ["key_value", "app_id", "cluster"]),
    ("openrouter", "OpenRouter", ["key_value", "base_url", "model_id"]),
    ("gemini", "Google Gemini", ["key_value", "model_id"]),
    ("gemini_video_analysis", "Gemini 视频分析", ["key_value", "model_id"]),
    ("doubao_llm", "豆包翻译", ["key_value", "base_url", "model_id"]),
    ("elevenlabs", "ElevenLabs", ["key_value"]),
]

TRANSLATE_PROVIDERS = [
    "vertex_gemini_31_flash_lite", "vertex_gemini_3_flash", "vertex_gemini_31_pro",
    "gemini_31_flash", "gemini_31_pro", "gemini_3_flash",
    "claude_sonnet",
    "openrouter",
    "doubao",
]

DEFAULT_TRANSLATE_PROVIDER = "vertex_gemini_31_flash_lite"

BINDING_ALLOWED_PROVIDERS = (
    "openrouter", "doubao", "gemini_aistudio", "gemini_vertex",
)


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        tab = (request.form.get("tab") or "providers").strip()
        if tab == "bindings":
            _handle_bindings_post()
        else:
            _handle_providers_post()  # 兼容老表单（无 tab）和 tab=providers/general
        flash("配置已保存")
        return redirect(url_for("settings.index", tab=tab))

    keys = get_all(current_user.id)
    jianying_project_root = keys.get("jianying", {}).get("extra", {}).get("project_root") or DEFAULT_JIANYING_PROJECT_ROOT
    translate_pref = keys.get("translate_pref", {}).get("key_value", "") or DEFAULT_TRANSLATE_PROVIDER
    try:
        current_image_channel = get_image_translate_channel()
    except Exception:
        current_image_channel = "aistudio"

    bindings_rows = llm_bindings.list_all()
    bindings_grouped: dict[str, list] = {}
    for row in bindings_rows:
        bindings_grouped.setdefault(row["module"], []).append(row)

    return render_template(
        "settings.html",
        keys=keys,
        services=SERVICES,
        jianying_project_root=jianying_project_root,
        default_jianying_project_root=DEFAULT_JIANYING_PROJECT_ROOT,
        translate_pref=translate_pref,
        video_analysis_models=VIDEO_CAPABLE_MODELS,
        image_translate_channel=current_image_channel,
        image_translate_channels=[
            (code, IMAGE_TRANSLATE_CHANNEL_LABELS.get(code, code))
            for code in IMAGE_TRANSLATE_CHANNELS
        ],
        bindings_grouped=bindings_grouped,
        module_labels=MODULE_LABELS,
        binding_allowed_providers=BINDING_ALLOWED_PROVIDERS,
        active_tab=request.args.get("tab") or "providers",
    )


def _handle_providers_post() -> None:
    """兼容主线的"服务商 + translate_pref + jianying + image_channel"一页表单。"""
    for service, _, fields in SERVICES:
        key_value = request.form.get(f"{service}_key", "").strip()
        extra = {}
        for field in fields[1:]:
            value = request.form.get(f"{service}_{field}", "").strip()
            if value:
                extra[field] = value
        if key_value or extra:
            set_key(current_user.id, service, key_value, extra or None)

    translate_pref = request.form.get("translate_pref", DEFAULT_TRANSLATE_PROVIDER).strip()
    if translate_pref in TRANSLATE_PROVIDERS:
        set_key(current_user.id, "translate_pref", translate_pref)

    jianying_project_root = (request.form.get("jianying_project_root", "").strip()
                              or DEFAULT_JIANYING_PROJECT_ROOT)
    set_key(current_user.id, "jianying", "", {"project_root": jianying_project_root})

    image_translate_channel = request.form.get("image_translate_channel", "").strip().lower()
    if image_translate_channel in IMAGE_TRANSLATE_CHANNELS:
        set_image_translate_channel(image_translate_channel)


def _handle_bindings_post() -> None:
    """Tab 2：模块模型分配。

    - restore_default=<code>：删除该 use_case 的 binding，下次 resolve 回到默认
    - binding_<code>_provider + binding_<code>_model：upsert 覆盖
    """
    restore = (request.form.get("restore_default") or "").strip()
    if restore and restore in USE_CASES:
        llm_bindings.delete(restore)
        return

    for code in USE_CASES:
        provider = (request.form.get(f"binding_{code}_provider") or "").strip()
        model = (request.form.get(f"binding_{code}_model") or "").strip()
        if not provider or not model:
            continue
        if provider not in BINDING_ALLOWED_PROVIDERS:
            continue
        llm_bindings.upsert(
            code, provider=provider, model=model, updated_by=current_user.id,
        )
