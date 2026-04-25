from __future__ import annotations
import json
import bcrypt

from appcore.db import query, query_one, execute
from appcore.permissions import (
    ROLE_SUPERADMIN,
    default_permissions_for_role,
    is_valid_role,
    normalize_permissions,
)


SUPERADMIN_USERNAME = "admin"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_user(username: str, password: str, role: str = "user") -> int:
    if not is_valid_role(role):
        raise ValueError(f"invalid role: {role}")
    if role == ROLE_SUPERADMIN and username != SUPERADMIN_USERNAME:
        raise ValueError("only the reserved username can hold superadmin role")
    pw_hash = hash_password(password)
    permissions_json = json.dumps(default_permissions_for_role(role))
    return execute(
        "INSERT INTO users (username, password_hash, role, permissions) VALUES (%s, %s, %s, %s)",
        (username, pw_hash, role, permissions_json),
    )


def get_by_username(username: str) -> dict | None:
    return query_one("SELECT * FROM users WHERE username = %s", (username,))


def get_by_id(user_id: int) -> dict | None:
    return query_one("SELECT * FROM users WHERE id = %s", (user_id,))


def list_users() -> list[dict]:
    return query(
        "SELECT id, username, role, permissions, is_active, created_at "
        "FROM users ORDER BY id"
    )


def set_active(user_id: int, active: bool) -> None:
    execute("UPDATE users SET is_active = %s WHERE id = %s", (int(active), user_id))


def update_role(user_id: int, role: str, *, reset_permissions: bool = True) -> None:
    """修改用户角色。reset_permissions=True 时同步把权限重置为新角色默认模板。

    超管约束：
      - 超管角色仅能由保留用户名 ``admin`` 持有
      - 不能把现存的超管降级（避免锁死系统）
    """
    if not is_valid_role(role):
        raise ValueError(f"invalid role: {role}")
    user = get_by_id(user_id)
    if user is None:
        raise ValueError(f"user not found: {user_id}")
    if role == ROLE_SUPERADMIN and user["username"] != SUPERADMIN_USERNAME:
        raise ValueError("only the reserved username can hold superadmin role")
    if user["role"] == ROLE_SUPERADMIN and role != ROLE_SUPERADMIN:
        raise ValueError("cannot demote the superadmin")
    if reset_permissions:
        permissions_json = json.dumps(default_permissions_for_role(role))
        execute(
            "UPDATE users SET role = %s, permissions = %s WHERE id = %s",
            (role, permissions_json, user_id),
        )
    else:
        execute("UPDATE users SET role = %s WHERE id = %s", (role, user_id))


def update_permissions(user_id: int, payload: dict | None) -> dict[str, bool]:
    """按用户 role 净化权限 payload 后写入。返回最终生效的 17 项布尔。"""
    user = get_by_id(user_id)
    if user is None:
        raise ValueError(f"user not found: {user_id}")
    cleaned = normalize_permissions(user["role"], payload)
    execute(
        "UPDATE users SET permissions = %s WHERE id = %s",
        (json.dumps(cleaned), user_id),
    )
    return cleaned


def reset_permissions_to_role_default(user_id: int) -> dict[str, bool]:
    user = get_by_id(user_id)
    if user is None:
        raise ValueError(f"user not found: {user_id}")
    cleaned = default_permissions_for_role(user["role"])
    execute(
        "UPDATE users SET permissions = %s WHERE id = %s",
        (json.dumps(cleaned), user_id),
    )
    return cleaned
