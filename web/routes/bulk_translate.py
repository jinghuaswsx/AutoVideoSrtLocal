"""bulk_translate 父任务 + 视频翻译参数配置的 HTTP API 套件。

本期已实现的端点(由 Phase 3/5 逐步扩充):
  Phase 3:
    POST /api/bulk-translate/estimate     — 费用预估
    GET  /api/video-translate-profile     — 读取合并后的参数
    PUT  /api/video-translate-profile     — 保存参数(三种 scope)

Phase 5 会追加:
    POST /api/bulk-translate/create / start / pause / resume / cancel
    POST /api/bulk-translate/<id>/retry-item / retry-failed
    GET  /api/bulk-translate/<id> / list / audit

设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md 第 6 章
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from appcore.bulk_translate_estimator import estimate as do_estimate
from appcore.video_translate_defaults import (
    SYSTEM_DEFAULTS,
    load_effective_params,
    save_profile,
)

bp = Blueprint("bulk_translate", __name__, url_prefix="/api/bulk-translate")
profile_bp = Blueprint("video_translate_profile", __name__,
                        url_prefix="/api/video-translate-profile")


# ============================================================
# POST /api/bulk-translate/estimate
# ============================================================
@bp.post("/estimate")
@login_required
def estimate_endpoint():
    """费用/资源预估(弹窗打开 + 勾选变化时调用)。

    Body:
      {
        "product_id": int,              # 必填
        "target_langs": ["de", "fr"],   # 必填,非空
        "content_types": ["copy", ...], # 必填
        "force_retranslate": bool       # 默认 false
      }
    """
    payload = request.get_json(force=True, silent=True) or {}
    product_id = payload.get("product_id")
    target_langs = payload.get("target_langs") or []
    content_types = payload.get("content_types") or []
    force = bool(payload.get("force_retranslate", False))

    if not isinstance(product_id, int):
        return jsonify({"error": "product_id 必填且为 int"}), 400
    if not target_langs or not isinstance(target_langs, list):
        return jsonify({"error": "target_langs 必填且为非空数组"}), 400
    if not content_types or not isinstance(content_types, list):
        return jsonify({"error": "content_types 必填且为非空数组"}), 400

    result = do_estimate(
        user_id=current_user.id,
        product_id=product_id,
        target_langs=target_langs,
        content_types=content_types,
        force_retranslate=force,
    )
    return jsonify(result), 200


# ============================================================
# GET / PUT /api/video-translate-profile
# ============================================================
@profile_bp.get("")
@profile_bp.get("/")
@login_required
def get_profile():
    """读取合并后的 12 项参数值。三层回填逻辑内置。

    Query args:
      product_id: int|""  — 空字符串视为 None(用户级查询)
      lang:       str|""  — 空字符串视为 None
    """
    product_id_raw = request.args.get("product_id")
    lang_raw = request.args.get("lang")

    product_id = int(product_id_raw) if product_id_raw else None
    lang = lang_raw if lang_raw else None

    params = load_effective_params(current_user.id, product_id, lang)
    return jsonify(params), 200


@profile_bp.put("")
@profile_bp.put("/")
@login_required
def put_profile():
    """保存一条 profile。

    Body:
      {
        "product_id": int|null,  # null 表示用户级
        "lang": str|null,        # null 表示产品级(对所有语言生效)
        "params": { ... }        # 至少一个字段
      }

    scope 对应按钮:
      - 保存配置:            product_id=X, lang=Y
      - 保存为该产品默认:    product_id=X, lang=null
      - 保存为我的默认:      product_id=null, lang=null
    """
    payload = request.get_json(force=True, silent=True) or {}
    product_id = payload.get("product_id")
    lang = payload.get("lang")
    params = payload.get("params")

    if not isinstance(params, dict) or not params:
        return jsonify({"error": "params 必填且为非空 dict"}), 400

    # 白名单校验:只接受 SYSTEM_DEFAULTS 里的 key
    unknown = set(params.keys()) - set(SYSTEM_DEFAULTS.keys())
    if unknown:
        return jsonify({"error": f"未知参数: {sorted(unknown)}"}), 400

    if product_id is not None and not isinstance(product_id, int):
        return jsonify({"error": "product_id 必须是 int 或 null"}), 400

    save_profile(current_user.id, product_id, lang, params)
    return jsonify({"ok": True}), 200
