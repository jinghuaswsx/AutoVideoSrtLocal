from __future__ import annotations
import json
from functools import wraps

from flask import abort
from flask_login import LoginManager, UserMixin, current_user

from appcore.users import get_by_id
from appcore.permissions import (
    PERMISSION_CODES,
    ROLE_ADMIN,
    ROLE_SUPERADMIN,
    merge_with_defaults,
)

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "请先登录"


def _coerce_permissions(raw) -> dict | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except Exception:
            return None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


class User(UserMixin):
    def __init__(self, row: dict):
        self.id = row["id"]
        self.username = row["username"]
        self.role = row["role"]
        self.is_active_flag = bool(row["is_active"])
        stored = _coerce_permissions(row.get("permissions"))
        self._permissions = merge_with_defaults(self.role, stored)

    @property
    def is_active(self):
        return self.is_active_flag

    @property
    def is_superadmin(self) -> bool:
        return self.role == ROLE_SUPERADMIN

    @property
    def is_admin(self) -> bool:
        """超管和管理员都视为 admin（向后兼容旧的 admin_required）。"""
        return self.role in (ROLE_SUPERADMIN, ROLE_ADMIN)

    def has_permission(self, code: str) -> bool:
        if self.role == ROLE_SUPERADMIN:
            return True
        return bool(self._permissions.get(code, False))

    @property
    def permissions(self) -> dict[str, bool]:
        return dict(self._permissions)


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    row = get_by_id(int(user_id))
    if row and row["is_active"]:
        return User(row)
    return None


def admin_required(f):
    """超管或管理员可访问。保留向后兼容旧路由。"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def superadmin_required(f):
    """仅超级管理员可访问。"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_superadmin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def permission_required(code: str):
    """要求当前用户拥有指定菜单/页面权限。超管自动通过。"""
    if code not in PERMISSION_CODES:
        raise ValueError(f"unknown permission code: {code}")

    def deco(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(403)
            if not current_user.has_permission(code):
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return deco
