"""Omni-translate experimental preset CRUD API blueprint (Phase 1).

路由清单（spec §4.5 + plan §1）：

    GET    /api/omni-presets                        — 当前用户可见的 preset 列表
    POST   /api/omni-presets                        — 创建用户级 preset
    GET    /api/omni-presets/default                — 当前全站默认 preset 完整数据
    POST   /api/omni-presets/<id>/set-as-default    — admin 设全站默认
    PUT    /api/omni-presets/<id>                   — admin 改系统级 / user 改自己的
    DELETE /api/omni-presets/<id>                   — 同上

权限矩阵：

| 操作               | 系统级 preset                | 用户级 preset (自己的) | 用户级 preset (别人的) |
| 看                | 全员                          | 自己                    | 拒绝                    |
| 用                | 全员                          | 自己                    | 拒绝                    |
| CRUD              | admin only                    | 自己                    | 拒绝                    |
| set-as-default    | admin only + 必须是系统级 ✓    | 拒绝                    | 拒绝                    |
"""
from __future__ import annotations

import json
import logging

from flask import Blueprint, Response, request
from flask_login import current_user, login_required

from appcore import omni_preset_dao
from appcore.omni_plugin_config import (
    CAPABILITY_GROUPS,
    DEFAULT_PLUGIN_CONFIG,
    validate_plugin_config,
)
from web.auth import admin_required

log = logging.getLogger(__name__)

bp = Blueprint("omni_preset_api", __name__, url_prefix="/api/omni-presets")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_admin() -> bool:
    return bool(getattr(current_user, "is_admin", False))


def _serialize(preset: dict) -> dict:
    return {
        "id": preset["id"],
        "scope": preset["scope"],
        "user_id": preset.get("user_id"),
        "name": preset["name"],
        "description": preset.get("description"),
        "plugin_config": preset["plugin_config"],
        "created_at": preset["created_at"].isoformat() if preset.get("created_at") else None,
        "updated_at": preset["updated_at"].isoformat() if preset.get("updated_at") else None,
    }


def _json_response(payload: dict, status: int = 200):
    return Response(
        json.dumps(payload, ensure_ascii=False),
        status=status,
        mimetype="application/json",
    )


def _bad(message: str, status: int = 400):
    return _json_response({"error": message}, status)


def _can_modify(preset: dict) -> tuple[bool, str | None]:
    """返回 (allowed, reason_when_denied)。"""
    scope = preset.get("scope")
    if scope == "system":
        if not _is_admin():
            return False, "仅管理员可修改系统级 preset"
        return True, None
    # user-level
    if preset.get("user_id") != getattr(current_user, "id", None):
        return False, "无权操作他人的用户级 preset"
    return True, None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@bp.route("", methods=["GET"])
@login_required
def list_presets():
    user_id = current_user.id
    presets = omni_preset_dao.list_for_user(user_id)
    default_id = omni_preset_dao.get_default_id()
    return _json_response(
        {
            "presets": [_serialize(p) for p in presets],
            "default_preset_id": default_id,
            "capability_groups": CAPABILITY_GROUPS,
        }
    )


@bp.route("/default", methods=["GET"])
@login_required
def get_default():
    preset = omni_preset_dao.get_default()
    if not preset:
        return _json_response(
            {"preset": None, "fallback_plugin_config": DEFAULT_PLUGIN_CONFIG}
        )
    return _json_response({"preset": _serialize(preset)})


@bp.route("", methods=["POST"])
@login_required
def create_preset():
    """普通 user → 用户级；admin 也能这么建用户级（如要建系统级走 ?scope=system）。"""
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    description = payload.get("description")
    cfg_raw = payload.get("plugin_config")
    scope = (payload.get("scope") or "user").strip().lower()

    if not name:
        return _bad("preset 名字不能为空")
    if len(name) > 64:
        return _bad("preset 名字最长 64 字符")
    if description and len(description) > 255:
        return _bad("preset 说明最长 255 字符")
    try:
        cfg = validate_plugin_config(cfg_raw)
    except ValueError as exc:
        return _bad(str(exc))

    if scope == "system":
        if not _is_admin():
            return _bad("仅管理员可创建系统级 preset", 403)
        new_id = omni_preset_dao.create_system_preset(name, description, cfg)
    elif scope == "user":
        new_id = omni_preset_dao.create_user_preset(
            current_user.id, name, description, cfg
        )
    else:
        return _bad(f"scope 取值不合法：{scope!r}（仅支持 system/user）")

    created = omni_preset_dao.get(new_id)
    return _json_response({"preset": _serialize(created)}, 201)


@bp.route("/<int:preset_id>", methods=["PUT"])
@login_required
def update_preset(preset_id: int):
    preset = omni_preset_dao.get(preset_id)
    if not preset:
        return _bad("preset 不存在", 404)
    allowed, reason = _can_modify(preset)
    if not allowed:
        return _bad(reason or "无权修改", 403)

    payload = request.get_json(silent=True) or {}
    name = payload.get("name")
    description = payload.get("description")
    cfg_raw = payload.get("plugin_config")

    if name is not None:
        name = name.strip()
        if not name:
            return _bad("preset 名字不能为空")
        if len(name) > 64:
            return _bad("preset 名字最长 64 字符")
    if description is not None and len(description) > 255:
        return _bad("preset 说明最长 255 字符")
    fixed_cfg = None
    if cfg_raw is not None:
        try:
            fixed_cfg = validate_plugin_config(cfg_raw)
        except ValueError as exc:
            return _bad(str(exc))

    omni_preset_dao.update(
        preset_id, name=name, description=description, plugin_config=fixed_cfg
    )
    updated = omni_preset_dao.get(preset_id)
    return _json_response({"preset": _serialize(updated)})


@bp.route("/<int:preset_id>", methods=["DELETE"])
@login_required
def delete_preset(preset_id: int):
    preset = omni_preset_dao.get(preset_id)
    if not preset:
        return _bad("preset 不存在", 404)
    allowed, reason = _can_modify(preset)
    if not allowed:
        return _bad(reason or "无权删除", 403)
    ok = omni_preset_dao.delete(preset_id)
    if not ok:
        return _bad("该 preset 当前是全站默认，删除前请先把默认切到其他 preset", 409)
    return _json_response({"ok": True})


@bp.route("/<int:preset_id>/set-as-default", methods=["POST"])
@login_required
@admin_required
def set_as_default(preset_id: int):
    ok = omni_preset_dao.set_default(preset_id)
    if not ok:
        return _bad("preset 不存在或不是系统级（用户级 preset 不能作为全站默认）", 400)
    return _json_response({"ok": True, "default_preset_id": preset_id})
