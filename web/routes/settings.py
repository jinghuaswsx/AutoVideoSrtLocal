"""API 设置页。

4-tab 结构：
  - providers: 服务商 Key / Base URL / model_id / extra_config，admin only，明文
  - bindings:  模块模型分配（UseCase × Provider × Model）
  - pricing:   AI 定价
  - push:      推送配置

2026-04-25 变更：providers Tab 完全由 llm_provider_configs 驱动。每个业务功能
一条独立 provider_code，字段明文渲染 + 自动复制按钮。admin 保存后新请求立即
读取最新 DB 行。历史 "translate_pref" 选择器保留（走老 api_keys 表）。
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from appcore import llm_bindings, llm_provider_configs, pricing
from appcore.api_keys import can_manage_api_config_user, set_key
from appcore.db import execute, query
from appcore.gemini import VIDEO_CAPABLE_MODELS
from appcore.image_translate_settings import (
    CHANNEL_LABELS as IMAGE_TRANSLATE_CHANNEL_LABELS,
    CHANNELS as IMAGE_TRANSLATE_CHANNELS,
    get_channel as get_image_translate_channel,
    get_default_model as get_image_translate_default_model,
    get_openrouter_openai_image2_default_quality,
    is_openrouter_openai_image2_enabled,
    set_channel as set_image_translate_channel,
    set_default_model as set_image_translate_default_model,
    set_openrouter_openai_image2_default_quality,
    set_openrouter_openai_image2_enabled,
)
from appcore.gemini_image import coerce_image_model, list_image_models
from appcore.llm_use_cases import MODULE_LABELS, USE_CASES

bp = Blueprint("settings", __name__)


# ---------------------------------------------------------------------------
# Providers Tab：UI 分组显示顺序（group_code → 标题）
# ---------------------------------------------------------------------------

PROVIDER_GROUP_ORDER: list[tuple[str, str]] = [
    ("text_llm", "文本 / 本土化 LLM"),
    ("image",    "图片重绘 / 图片翻译"),
    ("asr",      "语音识别"),
    ("video",    "视频生成"),
    ("tts",      "配音"),
    ("aux",      "辅助 API"),
]


TRANSLATE_PROVIDERS = [
    "vertex_gemini_31_flash_lite", "vertex_gemini_3_flash", "vertex_gemini_31_pro",
    "gemini_31_flash", "gemini_31_pro", "gemini_3_flash", "gpt_5_mini",
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
HIDDEN_BINDING_CODES = {"image_translate.generate"}
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


def admin_config_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not can_manage_api_config_user(current_user):
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Providers Tab helpers
# ---------------------------------------------------------------------------

def _provider_rows_by_group() -> list[dict]:
    """返回 [{group_code, group_label, rows: [...]}]。
    group 顺序固定，rows 来自 llm_provider_configs 表。
    """
    rows = llm_provider_configs.list_provider_configs()
    by_group: dict[str, list[llm_provider_configs.LlmProviderConfig]] = {}
    for row in rows:
        by_group.setdefault(row.group_code, []).append(row)
    view: list[dict] = []
    for group_code, group_label in PROVIDER_GROUP_ORDER:
        group_rows = by_group.get(group_code, [])
        if not group_rows:
            continue
        view.append({
            "code": group_code,
            "label": group_label,
            "rows": [
                {
                    "provider_code": r.provider_code,
                    "display_name": r.display_name,
                    "api_key": r.api_key or "",
                    "base_url": r.base_url or "",
                    "model_id": r.model_id or "",
                    "extra_config_json": (
                        json.dumps(r.extra_config, ensure_ascii=False, indent=2)
                        if r.extra_config else ""
                    ),
                    "enabled": bool(r.enabled),
                }
                for r in group_rows
            ],
        })
    return view


@bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_config_required
def index():
    if request.method == "POST":
        tab = (request.form.get("tab") or "providers").strip()
        if tab == "bindings":
            _handle_bindings_post()
        elif tab == "push":
            _handle_push_post()
        else:
            _handle_providers_post()  # tab=providers 或兼容老表单
        flash("配置已保存")
        return redirect(url_for("settings.index", tab=tab))

    translate_pref_value = _load_translate_pref()
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
        if row["code"] in HIDDEN_BINDING_CODES:
            continue
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
        "push_localized_texts_authorization": _pushes_mod.get_localized_texts_authorization(),
        "push_localized_texts_cookie": _pushes_mod.get_localized_texts_cookie(),
        "push_localized_texts_authorization_present": bool(
            _pushes_mod.get_localized_texts_authorization()
        ),
        "push_localized_texts_cookie_present": bool(
            _pushes_mod.get_localized_texts_cookie()
        ),
    }

    try:
        openrouter_openai_image2_enabled = is_openrouter_openai_image2_enabled()
    except Exception:
        openrouter_openai_image2_enabled = False
    try:
        openrouter_openai_image2_default_quality = get_openrouter_openai_image2_default_quality()
    except Exception:
        openrouter_openai_image2_default_quality = "mid"

    return render_template(
        "settings.html",
        provider_groups=_provider_rows_by_group(),
        translate_pref=translate_pref_value,
        video_analysis_models=VIDEO_CAPABLE_MODELS,
        image_translate_channel=current_image_channel,
        image_translate_channels=[
            (code, IMAGE_TRANSLATE_CHANNEL_DISPLAY_LABELS.get(code, code))
            for code in IMAGE_TRANSLATE_CHANNELS
        ],
        image_translate_default_model=current_image_default_model,
        image_translate_current_models=image_translate_current_models,
        image_translate_models_by_channel=image_translate_models_by_channel,
        openrouter_openai_image2_enabled=openrouter_openai_image2_enabled,
        openrouter_openai_image2_default_quality=openrouter_openai_image2_default_quality,
        bindings_grouped=bindings_grouped,
        module_labels=MODULE_LABELS,
        binding_allowed_providers=BINDING_ALLOWED_PROVIDERS,
        active_tab=active_tab,
        can_manage_pricing=is_admin,
        pricing_units_types=PRICING_UNITS_TYPES,
        push_credentials_view=push_credentials_view,
    )


def _load_translate_pref() -> str:
    """translate_pref 存在 api_keys 表（admin user 的非供应商偏好行）。"""
    from appcore.api_keys import get_all

    stored = get_all(current_user.id).get("translate_pref", {}).get("key_value", "")
    return stored or DEFAULT_TRANSLATE_PROVIDER


def _handle_providers_post() -> None:
    """保存 providers Tab：每个 provider_code 独立保存，admin-only。"""
    user_id = current_user.id
    known_codes = set(llm_provider_configs.known_provider_codes())
    for provider_code in known_codes:
        prefix = f"provider_{provider_code}_"
        touched = any(field.startswith(prefix) for field in request.form.keys())
        if not touched:
            continue
        fields: dict[str, object] = {}
        raw_api_key = request.form.get(f"{prefix}api_key")
        if raw_api_key is not None:
            fields["api_key"] = raw_api_key
        raw_base_url = request.form.get(f"{prefix}base_url")
        if raw_base_url is not None:
            fields["base_url"] = raw_base_url
        raw_model_id = request.form.get(f"{prefix}model_id")
        if raw_model_id is not None:
            fields["model_id"] = raw_model_id
        raw_extra = request.form.get(f"{prefix}extra_config")
        if raw_extra is not None:
            text = (raw_extra or "").strip()
            if not text:
                fields["extra_config"] = {}
            else:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    flash(
                        f"{provider_code} 的 extra_config 不是合法 JSON，已跳过保存该字段",
                        "error",
                    )
                    continue
                if not isinstance(parsed, dict):
                    flash(
                        f"{provider_code} 的 extra_config 必须是 JSON 对象",
                        "error",
                    )
                    continue
                fields["extra_config"] = parsed
        if not fields:
            continue
        llm_provider_configs.save_provider_config(
            provider_code, fields, updated_by=user_id,
        )

    # 全局：图片翻译通道 + OpenAI Image 2 开关 / 默认质量
    image2_enabled_raw = (request.form.get("openrouter_openai_image2_enabled") or "").strip().lower()
    image2_enabled = image2_enabled_raw in {"1", "true", "on", "yes"}
    image2_quality = (request.form.get("openrouter_openai_image2_default_quality") or "mid").strip().lower()
    try:
        set_openrouter_openai_image2_enabled(image2_enabled)
    except Exception:
        pass
    try:
        set_openrouter_openai_image2_default_quality(image2_quality)
    except ValueError:
        pass

    image_translate_channel = request.form.get("image_translate_channel", "").strip().lower()
    if image_translate_channel in IMAGE_TRANSLATE_CHANNELS:
        set_image_translate_channel(image_translate_channel)
        image_translate_model = request.form.get("image_translate_default_model", "").strip()
        set_image_translate_default_model(
            image_translate_channel,
            coerce_image_model(image_translate_model, channel=image_translate_channel),
        )

    # Admin 个人偏好：翻译模型选择器（老路径，存 api_keys.translate_pref）
    translate_pref = request.form.get("translate_pref", DEFAULT_TRANSLATE_PROVIDER).strip()
    if translate_pref in TRANSLATE_PROVIDERS:
        set_key(user_id, "translate_pref", translate_pref)


def _handle_bindings_post() -> None:
    """Tab 2：模块模型分配。

    - restore_default=<code>：删除该 use_case 的 binding，下次 resolve 回到默认
    - binding_<code>_provider + binding_<code>_model：upsert 覆盖
    """
    restore = (request.form.get("restore_default") or "").strip()
    if restore and restore in USE_CASES and restore not in HIDDEN_BINDING_CODES:
        llm_bindings.delete(restore)
        return

    for code in USE_CASES:
        if code in HIDDEN_BINDING_CODES:
            continue
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
@admin_config_required
def ai_pricing_page():
    return redirect(url_for("settings.index", tab="pricing"))


@bp.route("/admin/settings/ai-pricing/list", methods=["GET"])
@login_required
@admin_config_required
def ai_pricing_list():
    return jsonify({"items": _list_ai_pricing_rows()})


@bp.route("/admin/settings/ai-pricing", methods=["POST"])
@login_required
@admin_config_required
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
@admin_config_required
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
@admin_config_required
def ai_pricing_delete(price_id: int):
    deleted = execute("DELETE FROM ai_model_prices WHERE id = %s", (price_id,))
    if not deleted:
        return jsonify({"error": "not found"}), 404
    pricing.invalidate_cache()
    return jsonify({"ok": True})
