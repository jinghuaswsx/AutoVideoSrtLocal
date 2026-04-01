from __future__ import annotations
import bcrypt
from appcore.db import query, query_one, execute


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_user(username: str, password: str, role: str = "user") -> int:
    pw_hash = hash_password(password)
    return execute(
        "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
        (username, pw_hash, role),
    )


def get_by_username(username: str) -> dict | None:
    return query_one("SELECT * FROM users WHERE username = %s", (username,))


def get_by_id(user_id: int) -> dict | None:
    return query_one("SELECT * FROM users WHERE id = %s", (user_id,))


def list_users() -> list[dict]:
    return query("SELECT id, username, role, is_active, created_at FROM users ORDER BY id")


def set_active(user_id: int, active: bool) -> None:
    execute("UPDATE users SET is_active = %s WHERE id = %s", (int(active), user_id))
