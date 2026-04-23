"""API 设置页。

3-tab 结构：
  - providers: 服务商接入（沿用原 SERVICES 字段 + 主线 translate_pref）
  - bindings:  模块模型分配（UseCase × Provider × Model，新增）
  - general:   通用设置（剪映目录、图片翻译通道）

演进式重写：所有主线已有字段（TRANSLATE_PROVIDERS / SERVICES / DEFAULT_TRANSLATE_PROVIDER）
保持不变，以免破坏已有用户数据。只新增 Tab 2 Bindings 部分。
"""
from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from appcore import llm_bindings, pricing
from appcore.api_keys import DEFAULT_JIANYING_PROJECT_ROOT, get_all, set_key
from appcore.db import execute, query
from appcore.gemini import VIDEO_CAPABLE_MODELS
from appcore.image_translate_settings import (
    CHANNEL_LABELS as IMAGE_TRANSLATE_CHANNEL_LABELS,
    CHANNELS as IMAGE_TRANSLATE_CHANNELS,
    get_channel as get_image_translate_channel,
    get_default_model as get_image_translate_default_model,
    set_channel as set_image_translate_channel,
    set_default_model as set_image_translate_default_model,
)
from appcore.gemini_image import coerce_image_model, list_image_models
from appcore.llm_use_cases import MODULE_LABELS, USE_CASES
from web.auth import admin_required

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
BINDING_PROVIDER_LABELS = {
    "openrouter": "OpenRouter",
    "doubao": "Doubao ARK",
    "gemini_aistudio": "Google AI Studio",
    "gemini_vertex": "Google Cloud (Vertex AI)",
}
IMAGE_TEXT_DETECT_PROVIDERS = (
    "gemini_aistudio", "gemini_vertex", "openrouter",
)
IMAGE_TEXT_DETECT_MODEL = "gemini-3.1-flash-lite-preview"
PRICING_UNITS_TYPES = ("tokens", "chars", "seconds", "images")
IMAGE_TRANSLATE_CHANNEL_DISPLAY_LABELS = {
    **IMAGE_TRANSLATE_CHANNEL_LABELS,
    "doubao": "豆包 ARK（Seedream）",
}


def _image_translate_models_by_channel() -> dict[str, list[dict]]:
    return {
        code: [{"id": mid, "label": label} for mid, label in list_image_models(code)]
        for code in IMAGE_TRANSLATE_CHANNELS
    }


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        tab = (request.form.get("tab") or "providers").strip()
        if tab == "bindings":
            _handle_bindings_post()
        elif tab == "push":
            _handle_push_post()
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
    try:
        current_image_default_model = get_image_translate_default_model(current_image_channel)
    except Exception:
        current_image_default_model = coerce_image_model("", channel=current_image_channel)
    image_translate_models_by_channel = _image_translate_models_by_channel()
    image_translate_current_models = image_translate_models_by_channel.get(
        current_image_channel, image_translate_models_by_channel.get("aistudio", []),
    )

    bindings_rows = llm_bindings.list_all()
    bindings_grouped: dict[str, list] = {}
    for row in bindings_rows:
        if row["code"] == "image_translate.detect":
            row["provider_options"] = [
                (p, BINDING_PROVIDER_LABELS.get(p, p))
                for p in IMAGE_TEXT_DETECT_PROVIDERS
            ]
            row["model_suggestions"] = [IMAGE_TEXT_DETECT_MODEL]
        else:
            row["provider_options"] = [
                (p, BINDING_PROVIDER_LABELS.get(p, p))
                for p in BINDING_ALLOWED_PROVIDERS
            ]
            row["model_suggestions"] = []
        bindings_grouped.setdefault(row["module"], []).append(row)

    is_admin = getattr(current_user, "role", None) == "admin"
    active_tab = (request.args.get("tab") or "providers").strip().lower()
    allowed_tabs = {"providers", "bindings"}
    if is_admin:
        allowed_tabs.add("pricing")
        allowed_tabs.add("push")
    if active_tab not in allowed_tabs:
        active_tab = "providers"

    from appcore import pushes as _pushes_mod
    push_credentials_view = {
        "push_target_url": _pushes_mod.get_push_target_url(),
        "push_localized_texts_base_url": _pushes_mod.get_localized_texts_base_url(),
        "push_localized_texts_authorization_present": bool(
            _pushes_mod.get_localized_texts_authorization()
        ),
        "push_localized_texts_cookie_present": bool(
            _pushes_mod.get_localized_texts_cookie()
        ),
    }

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
            (code, IMAGE_TRANSLATE_CHANNEL_DISPLAY_LABELS.get(code, code))
            for code in IMAGE_TRANSLATE_CHANNELS
        ],
        image_translate_default_model=current_image_default_model,
        image_translate_current_models=image_translate_current_models,
        image_translate_models_by_channel=image_translate_models_by_channel,
        bindings_grouped=bindings_grouped,
        module_labels=MODULE_LABELS,
        binding_allowed_providers=BINDING_ALLOWED_PROVIDERS,
        active_tab=active_tab,
        can_manage_pricing=is_admin,
        pricing_units_types=PRICING_UNITS_TYPES,
        push_credentials_view=push_credentials_view,
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
        image_translate_model = request.form.get("image_translate_default_model", "").strip()
        set_image_translate_default_model(
            image_translate_channel,
            coerce_image_model(image_translate_model, channel=image_translate_channel),
        )


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
        allowed_providers = (
            IMAGE_TEXT_DETECT_PROVIDERS
            if code == "image_translate.detect" else BINDING_ALLOWED_PROVIDERS
        )
        if code == "image_translate.detect" and not model:
            model = IMAGE_TEXT_DETECT_MODEL
        if not provider or not model:
            continue
        if provider not in allowed_providers:
            continue
        llm_bindings.upsert(
            code, provider=provider, model=model, updated_by=current_user.id,
        )


def _handle_push_post() -> None:
    """推送 tab：保存推送目标 + 小语种文案推送凭据到 system_settings。"""
    if getattr(current_user, "role", None) != "admin":
        return

    from appcore.settings import set_setting

    field_keys = (
        "push_target_url",
        "push_localized_texts_base_url",
        "push_localized_texts_authorization",
        "push_localized_texts_cookie",
    )
    clear_keys = set((request.form.getlist("clear") or []))
    for key in field_keys:
        raw = (request.form.get(key) or "").strip()
        if raw:
            set_setting(key, raw)
        elif key in clear_keys:
            set_setting(key, "")


def _parse_price_decimal(raw_value, field_label: str) -> float | None:
    if raw_value in (None, ""):
        return None
    try:
        value = Decimal(str(raw_value))
    except (ArithmeticError, InvalidOperation, ValueError):
        raise ValueError(f"{field_label}必须是非负数字")
    if value < 0:
        raise ValueError(f"{field_label}不能为负数")
    return float(value)


def _serialize_price_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "provider": row["provider"],
        "model": row["model"],
        "units_type": row["units_type"],
        "unit_input_cny": None if row.get("unit_input_cny") is None else float(row["unit_input_cny"]),
        "unit_output_cny": None if row.get("unit_output_cny") is None else float(row["unit_output_cny"]),
        "unit_flat_cny": None if row.get("unit_flat_cny") is None else float(row["unit_flat_cny"]),
        "note": row.get("note"),
        "updated_at": str(row.get("updated_at") or ""),
    }


def _list_ai_pricing_rows() -> list[dict]:
    rows = query(
        """
        SELECT id, provider, model, units_type,
               unit_input_cny, unit_output_cny, unit_flat_cny,
               note, updated_at
        FROM ai_model_prices
        ORDER BY provider ASC, model ASC, id ASC
        """
    )
    return [_serialize_price_row(row) for row in rows]


def _get_ai_pricing_row(price_id: int) -> dict | None:
    rows = query(
        """
        SELECT id, provider, model, units_type,
               unit_input_cny, unit_output_cny, unit_flat_cny,
               note, updated_at
        FROM ai_model_prices
        WHERE id = %s
        """,
        (price_id,),
    )
    return _serialize_price_row(rows[0]) if rows else None


def _parse_ai_pricing_payload() -> dict:
    body = request.get_json(silent=True) or {}
    provider = (body.get("provider") or "").strip()
    model = (body.get("model") or "").strip()
    units_type = (body.get("units_type") or "").strip().lower()
    note = (body.get("note") or "").strip() or None
    unit_input_cny = _parse_price_decimal(body.get("unit_input_cny"), "输入单价")
    unit_output_cny = _parse_price_decimal(body.get("unit_output_cny"), "输出单价")
    unit_flat_cny = _parse_price_decimal(body.get("unit_flat_cny"), "统一单价")

    if not provider:
        raise ValueError("provider不能为空")
    if not model:
        raise ValueError("model不能为空")
    if units_type not in PRICING_UNITS_TYPES:
        raise ValueError(f"units_type必须是 {', '.join(PRICING_UNITS_TYPES)}")
    if unit_input_cny is None and unit_output_cny is None and unit_flat_cny is None:
        raise ValueError("至少填写一个单价字段")

    return {
        "provider": provider,
        "model": model,
        "units_type": units_type,
        "unit_input_cny": unit_input_cny,
        "unit_output_cny": unit_output_cny,
        "unit_flat_cny": unit_flat_cny,
        "note": note,
    }


@bp.route("/admin/settings/ai-pricing", methods=["GET"])
@login_required
@admin_required
def ai_pricing_page():
    return redirect(url_for("settings.index", tab="pricing"))


@bp.route("/admin/settings/ai-pricing/list", methods=["GET"])
@login_required
@admin_required
def ai_pricing_list():
    return jsonify({"items": _list_ai_pricing_rows()})


@bp.route("/admin/settings/ai-pricing", methods=["POST"])
@login_required
@admin_required
def ai_pricing_create():
    try:
        payload = _parse_ai_pricing_payload()
        price_id = execute(
            """
            INSERT INTO ai_model_prices (
              provider, model, units_type,
              unit_input_cny, unit_output_cny, unit_flat_cny, note
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                payload["provider"],
                payload["model"],
                payload["units_type"],
                payload["unit_input_cny"],
                payload["unit_output_cny"],
                payload["unit_flat_cny"],
                payload["note"],
            ),
        )
        pricing.invalidate_cache()
        return jsonify({"ok": True, "item": _get_ai_pricing_row(int(price_id))}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@bp.route("/admin/settings/ai-pricing/<int:price_id>", methods=["PUT"])
@login_required
@admin_required
def ai_pricing_update(price_id: int):
    if _get_ai_pricing_row(price_id) is None:
        return jsonify({"error": "not found"}), 404

    try:
        payload = _parse_ai_pricing_payload()
        updated = execute(
            """
            UPDATE ai_model_prices
            SET units_type = %s,
                unit_input_cny = %s,
                unit_output_cny = %s,
                unit_flat_cny = %s,
                note = %s
            WHERE id = %s
            """,
            (
                payload["units_type"],
                payload["unit_input_cny"],
                payload["unit_output_cny"],
                payload["unit_flat_cny"],
                payload["note"],
                price_id,
            ),
        )
        if not updated:
            return jsonify({"error": "not found"}), 404
        pricing.invalidate_cache()
        return jsonify({"ok": True, "item": _get_ai_pricing_row(price_id)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@bp.route("/admin/settings/ai-pricing/<int:price_id>", methods=["DELETE"])
@login_required
@admin_required
def ai_pricing_delete(price_id: int):
    deleted = execute("DELETE FROM ai_model_prices WHERE id = %s", (price_id,))
    if not deleted:
        return jsonify({"error": "not found"}), 404
    pricing.invalidate_cache()
    return jsonify({"ok": True})
