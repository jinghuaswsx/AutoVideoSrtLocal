"""阶段 1 核心地基测试：appcore.permissions + web.auth.User."""
from __future__ import annotations

import json

import pytest

from appcore.permissions import (
    PERMISSION_CODES,
    PERMISSION_META,
    ROLE_ADMIN,
    ROLE_SUPERADMIN,
    ROLE_USER,
    default_permissions_for_role,
    grouped_permissions,
    is_valid_role,
    merge_with_defaults,
    normalize_permissions,
)
from web.auth import User


# ---------- permissions.py ----------

def test_permission_codes_no_duplicates_and_no_unknown_groups():
    codes = list(PERMISSION_CODES)
    assert len(codes) == len(set(codes)), "permission codes must be unique"
    for code in codes:
        meta = PERMISSION_META[code]
        assert meta["group"] in {"business", "management", "capability", "system"}


def test_default_permissions_for_superadmin_is_all_true():
    perms = default_permissions_for_role(ROLE_SUPERADMIN)
    assert set(perms.keys()) == set(PERMISSION_CODES)
    assert all(perms.values())


def test_default_permissions_for_admin_includes_management_excludes_system():
    perms = default_permissions_for_role(ROLE_ADMIN)
    assert perms["medias"] is True
    assert perms["lab"] is True
    assert perms["ai_billing"] is True
    assert perms["bulk_translate_admin"] is True
    assert perms["user_management"] is False
    assert perms["system_settings"] is False
    assert perms["api_config"] is False
    assert perms["scheduled_tasks"] is False


def test_default_permissions_for_user_only_includes_business():
    perms = default_permissions_for_role(ROLE_USER)
    assert perms["medias"] is True
    assert perms["multi_translate"] is True
    assert perms["pushes"] is True
    assert perms["user_settings"] is True
    # 管理类对普通用户默认关闭
    assert perms["lab"] is False
    assert perms["ai_billing"] is False
    assert perms["mk_selection"] is False
    assert perms["data_analytics"] is False
    # 系统类对普通用户永远关闭
    assert perms["user_management"] is False
    assert perms["system_settings"] is False


def test_merge_with_defaults_superadmin_ignores_stored():
    stored = {code: False for code in PERMISSION_CODES}
    merged = merge_with_defaults(ROLE_SUPERADMIN, stored)
    assert all(merged.values())


def test_merge_with_defaults_admin_overrides_with_stored():
    stored = {"lab": False, "ai_billing": False}
    merged = merge_with_defaults(ROLE_ADMIN, stored)
    assert merged["lab"] is False
    assert merged["ai_billing"] is False
    # 未提交的项保持角色默认
    assert merged["medias"] is True
    assert merged["bulk_translate_admin"] is True


def test_merge_with_defaults_user_grants_extra_via_stored():
    stored = {"lab": True}
    merged = merge_with_defaults(ROLE_USER, stored)
    assert merged["lab"] is True
    assert merged["medias"] is True


def test_merge_with_defaults_with_none_returns_role_defaults():
    merged_admin = merge_with_defaults(ROLE_ADMIN, None)
    assert merged_admin == default_permissions_for_role(ROLE_ADMIN)
    merged_user = merge_with_defaults(ROLE_USER, None)
    assert merged_user == default_permissions_for_role(ROLE_USER)


def test_normalize_permissions_drops_unknown_keys():
    payload = {"medias": True, "lab": False, "unknown_code": True}
    cleaned = normalize_permissions(ROLE_ADMIN, payload)
    assert set(cleaned.keys()) == set(PERMISSION_CODES)
    assert "unknown_code" not in cleaned
    assert cleaned["medias"] is True
    assert cleaned["lab"] is False


def test_normalize_permissions_superadmin_locks_all_true():
    payload = {"medias": False, "user_management": False}
    cleaned = normalize_permissions(ROLE_SUPERADMIN, payload)
    assert all(cleaned.values())


def test_is_valid_role():
    assert is_valid_role("superadmin")
    assert is_valid_role("admin")
    assert is_valid_role("user")
    assert not is_valid_role("guest")
    assert not is_valid_role("")


def test_grouped_permissions_groups_in_order():
    groups = grouped_permissions()
    group_codes = [g[0] for g in groups]
    assert group_codes == ["business", "management", "capability", "system"]
    # 每组都有 item
    for _, _, items in groups:
        assert len(items) > 0
    # 系统组只有 4 项（用户管理 / 系统设置 / API 配置 / 定时任务）
    system_items = [g for g in groups if g[0] == "system"][0][2]
    assert len(system_items) == 4


# ---------- web.auth.User ----------

def _make_row(role: str, *, username: str = "alice", permissions=None) -> dict:
    return {
        "id": 1,
        "username": username,
        "role": role,
        "is_active": 1,
        "permissions": permissions,
    }


def test_user_superadmin_full_access():
    u = User(_make_row(ROLE_SUPERADMIN, username="admin"))
    assert u.is_superadmin is True
    assert u.is_admin is True
    for code in PERMISSION_CODES:
        assert u.has_permission(code)


def test_user_superadmin_role_is_locked_to_admin_username():
    u = User(_make_row(ROLE_SUPERADMIN, username="manager"))
    assert u.is_superadmin is False
    assert u.is_admin is False
    assert u.has_permission("api_config") is False


def test_user_admin_default_permissions():
    u = User(_make_row(ROLE_ADMIN))
    assert u.is_superadmin is False
    assert u.is_admin is True
    assert u.has_permission("lab") is True
    assert u.has_permission("ai_billing") is True
    assert u.has_permission("user_management") is False
    assert u.has_permission("scheduled_tasks") is False


def test_user_normal_default_permissions():
    u = User(_make_row(ROLE_USER))
    assert u.is_superadmin is False
    assert u.is_admin is False
    assert u.has_permission("medias") is True
    assert u.has_permission("user_settings") is True
    assert u.has_permission("lab") is False
    assert u.has_permission("ai_billing") is False
    assert u.has_permission("user_management") is False


def test_user_permissions_overlay_admin_revoke_lab():
    u = User(_make_row(ROLE_ADMIN, permissions=json.dumps({"lab": False})))
    assert u.has_permission("lab") is False
    # 其他默认管理员权限保留
    assert u.has_permission("medias") is True
    assert u.has_permission("bulk_translate_admin") is True


def test_user_permissions_overlay_user_grant_lab():
    u = User(_make_row(ROLE_USER, permissions=json.dumps({"lab": True})))
    assert u.has_permission("lab") is True
    # 系统级权限不能通过这种方式获得（默认就是 False，stored 没给就维持 False）
    assert u.has_permission("user_management") is False


def test_user_permissions_overlay_user_grant_user_management_works_via_stored():
    """用户级权限可以单独勾出系统类权限（如果超管真的勾给了某用户）。"""
    u = User(_make_row(ROLE_USER, permissions=json.dumps({"user_management": True})))
    assert u.has_permission("user_management") is True


def test_user_handles_dict_permissions_directly():
    u = User(_make_row(ROLE_ADMIN, permissions={"lab": False}))
    assert u.has_permission("lab") is False


def test_user_handles_none_permissions_falls_back_to_role_default():
    u = User(_make_row(ROLE_ADMIN, permissions=None))
    assert u.has_permission("lab") is True
    assert u.has_permission("user_management") is False


def test_user_handles_invalid_permissions_blob_silently():
    u = User(_make_row(ROLE_USER, permissions="not json"))
    # 解析失败回落到角色默认
    assert u.has_permission("medias") is True
    assert u.has_permission("lab") is False
