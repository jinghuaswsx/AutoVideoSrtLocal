"""菜单/页面级权限注册表 + 角色默认模板。

权限模型设计（见 docs/superpowers/specs/2026-04-25-permission-system-design.md）：

- 三级角色：superadmin / admin / user
- 权限项粒度：菜单/页面级 + 任务能力位，共 22 项，分 4 组（业务 / 管理 / 任务能力 / 系统）
- 角色决定「页面里能做什么」（看自己 vs 看全局 vs 改别人）
- permissions（菜单级）决定「能否进入某个菜单/页面」
- superadmin 唯一（绑定 username='admin'），永远视为全部权限开启
- 改用户角色时同步重置权限到新角色默认模板（appcore.users.update_role）
"""
from __future__ import annotations

ROLE_SUPERADMIN = "superadmin"
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_SUPERADMIN, ROLE_ADMIN, ROLE_USER)

ROLE_LABELS = {
    ROLE_SUPERADMIN: "超级管理员",
    ROLE_ADMIN: "管理员",
    ROLE_USER: "普通用户",
}

GROUP_BUSINESS = "business"
GROUP_MANAGEMENT = "management"
GROUP_CAPABILITY = "capability"
GROUP_SYSTEM = "system"

GROUPS = (
    (GROUP_BUSINESS, "业务功能"),
    (GROUP_MANAGEMENT, "管理功能"),
    (GROUP_CAPABILITY, "任务能力"),
    (GROUP_SYSTEM, "系统 / 超管"),
)

# 字段顺序：(code, group, label, default_for_admin, default_for_user)
# superadmin 永远全开，无须在表里配置
PERMISSIONS: tuple[tuple[str, str, str, bool, bool], ...] = (
    # A. 业务功能（普通用户也用）
    ("medias",                GROUP_BUSINESS,   "素材管理",         True,  True),
    ("multi_translate",       GROUP_BUSINESS,   "多语种视频翻译",   True,  True),
    ("title_translate",       GROUP_BUSINESS,   "多语言标题翻译",   True,  True),
    ("image_translate",       GROUP_BUSINESS,   "图片翻译",         True,  True),
    ("subtitle_removal",      GROUP_BUSINESS,   "字幕移除",         True,  True),
    ("pushes",                GROUP_BUSINESS,   "推送管理",         True,  True),
    ("task_center",           GROUP_BUSINESS,   "任务中心",         True,  True),
    ("raw_video_pool",        GROUP_BUSINESS,   "原始素材任务库",   True,  True),
    ("projects",              GROUP_BUSINESS,   "视频翻译",         True,  True),
    ("user_settings",         GROUP_BUSINESS,   "用户设置",         True,  True),
    # B. 管理类
    ("mk_selection",          GROUP_MANAGEMENT, "选品中心",         True,  False),
    ("bulk_translate_admin",  GROUP_MANAGEMENT, "批量翻译任务管理", True,  False),
    ("data_analytics",        GROUP_MANAGEMENT, "数据分析",         True,  False),
    ("lab",                   GROUP_MANAGEMENT, "实验室",           True,  False),
    ("ai_billing",            GROUP_MANAGEMENT, "API 账单",         True,  False),
    ("productivity_stats",    GROUP_MANAGEMENT, "员工产能报表",     True,  False),
    # C. 任务能力
    ("can_process_raw_video", GROUP_CAPABILITY, "原始视频处理人",   True,  False),
    ("can_translate",         GROUP_CAPABILITY, "翻译员",           True,  False),
    # D. 超管 / 系统类
    ("user_management",       GROUP_SYSTEM,     "用户管理",         False, False),
    ("system_settings",       GROUP_SYSTEM,     "系统设置",         False, False),
    ("api_config",            GROUP_SYSTEM,     "API 配置",         False, False),
    ("scheduled_tasks",       GROUP_SYSTEM,     "定时任务",         False, False),
)

PERMISSION_CODES: tuple[str, ...] = tuple(code for code, *_ in PERMISSIONS)
PERMISSION_META: dict[str, dict] = {
    code: {"group": group, "label": label, "admin": adm, "user": usr}
    for code, group, label, adm, usr in PERMISSIONS
}


def is_valid_role(role: str) -> bool:
    return role in ROLES


def default_permissions_for_role(role: str) -> dict[str, bool]:
    """根据角色返回默认权限模板。superadmin 永远全开。"""
    if role == ROLE_SUPERADMIN:
        return {code: True for code in PERMISSION_CODES}
    if role == ROLE_ADMIN:
        return {code: meta["admin"] for code, meta in PERMISSION_META.items()}
    return {code: meta["user"] for code, meta in PERMISSION_META.items()}


def merge_with_defaults(role: str, stored: dict | None) -> dict[str, bool]:
    """把存储的 permissions 与角色默认模板合并。
    超管忽略 stored，永远全开。其他角色：缺失的 key 用默认值补齐。
    """
    if role == ROLE_SUPERADMIN:
        return {code: True for code in PERMISSION_CODES}
    base = default_permissions_for_role(role)
    if not stored:
        return base
    for code in PERMISSION_CODES:
        if code in stored:
            base[code] = bool(stored[code])
    return base


def normalize_permissions(role: str, payload: dict | None) -> dict[str, bool]:
    """把 UI 提交的权限 dict 净化为合法的 17 项布尔。
    超管永远全开（payload 被忽略）。其他角色：未提交的 key 用角色默认值补齐。
    """
    if role == ROLE_SUPERADMIN:
        return {code: True for code in PERMISSION_CODES}
    defaults = default_permissions_for_role(role)
    payload = payload or {}
    return {code: bool(payload.get(code, defaults[code])) for code in PERMISSION_CODES}


def grouped_permissions() -> list[tuple[str, str, list[dict]]]:
    """返回 [(group_code, group_label, [item, ...]), ...] 供 UI 渲染。"""
    by_group: dict[str, list[dict]] = {g: [] for g, _ in GROUPS}
    for code, group, label, adm, usr in PERMISSIONS:
        by_group[group].append({
            "code": code,
            "label": label,
            "default_admin": adm,
            "default_user": usr,
        })
    return [(g, label, by_group[g]) for g, label in GROUPS]
