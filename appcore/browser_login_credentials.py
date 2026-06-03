"""Plaintext browser login credential storage.

Docs-anchor: docs/superpowers/specs/2026-05-08-meta-login-plaintext-autofill-design.md
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from appcore.db import execute, query, query_one


DEFAULT_ENV_CODE = "DXM01-Meta"
DEFAULT_PROVIDER = "facebook"
TABCUT_ENV_CODE = "TABCUT"
TABCUT_PROVIDER = "tabcut"

SUPPORTED_CREDENTIAL_DEFAULTS = (
    (DEFAULT_ENV_CODE, DEFAULT_PROVIDER),
    (TABCUT_ENV_CODE, TABCUT_PROVIDER),
)
PROVIDER_LABELS = {
    DEFAULT_PROVIDER: "Facebook",
    TABCUT_PROVIDER: "TABCUT",
}
USERNAME_LABELS = {
    DEFAULT_PROVIDER: "Facebook account",
    TABCUT_PROVIDER: "TABCUT account",
}
PASSWORD_LABELS = {
    DEFAULT_PROVIDER: "Facebook password",
    TABCUT_PROVIDER: "TABCUT password",
}


@dataclass(frozen=True)
class BrowserLoginCredential:
    id: int | None
    env_code: str
    provider: str
    username: str
    password: str
    enabled: bool = True
    last_login_status: str | None = None
    last_error: str | None = None
    last_login_at: Any | None = None


def _coerce_bool(value: Any) -> bool:
    return bool(int(value or 0))


def _row_to_credential(row: dict[str, Any]) -> BrowserLoginCredential:
    return BrowserLoginCredential(
        id=int(row["id"]) if row.get("id") is not None else None,
        env_code=str(row.get("env_code") or ""),
        provider=str(row.get("provider") or ""),
        username=str(row.get("username") or ""),
        password=str(row.get("password") or ""),
        enabled=_coerce_bool(row.get("enabled")),
        last_login_status=str(row.get("last_login_status") or "") or None,
        last_error=str(row.get("last_error") or "") or None,
        last_login_at=row.get("last_login_at"),
    )


def mask_username(username: str | None) -> str:
    text = (username or "").strip()
    if not text:
        return ""
    if len(text) <= 4:
        return "***"
    return f"{text[:4]}{'*' * max(8, len(text) - 8)}{text[-4:]}"


def get_credential(
    env_code: str = DEFAULT_ENV_CODE,
    provider: str = DEFAULT_PROVIDER,
    *,
    enabled_only: bool = True,
) -> BrowserLoginCredential | None:
    sql = (
        "SELECT id, env_code, provider, username, password, enabled, "
        "last_login_at, last_login_status, last_error "
        "FROM browser_login_credentials WHERE env_code=%s AND provider=%s"
    )
    if enabled_only:
        sql += " AND enabled=1"
    row = query_one(sql, (env_code, provider))
    return _row_to_credential(row) if row else None


def list_credentials() -> list[BrowserLoginCredential]:
    rows = query(
        "SELECT id, env_code, provider, username, password, enabled, "
        "last_login_at, last_login_status, last_error "
        "FROM browser_login_credentials ORDER BY env_code, provider"
    )
    return [_row_to_credential(row) for row in rows]


def list_credentials_view() -> list[dict[str, Any]]:
    rows = list_credentials()
    by_key = {(row.env_code, row.provider): row for row in rows}
    ordered_rows: list[BrowserLoginCredential] = []
    seen: set[tuple[str, str]] = set()

    for env_code, provider in SUPPORTED_CREDENTIAL_DEFAULTS:
        key = (env_code, provider)
        ordered_rows.append(
            by_key.get(key)
            or BrowserLoginCredential(
                id=None,
                env_code=env_code,
                provider=provider,
                username="",
                password="",
                enabled=True,
            )
        )
        seen.add(key)

    for row in rows:
        key = (row.env_code, row.provider)
        if key not in seen:
            ordered_rows.append(row)
            seen.add(key)

    return [
        {
            "id": row.id,
            "env_code": row.env_code,
            "provider": row.provider,
            "provider_label": PROVIDER_LABELS.get(row.provider, row.provider or row.env_code),
            "username_label": USERNAME_LABELS.get(row.provider, "Login account"),
            "password_label": PASSWORD_LABELS.get(row.provider, "Login password"),
            "username_mask": mask_username(row.username),
            "username_value": row.username,
            "password_present": bool(row.password),
            "enabled": row.enabled,
            "last_login_at": row.last_login_at,
            "last_login_status": row.last_login_status,
            "last_error": row.last_error,
        }
        for row in ordered_rows
    ]


def get_tabcut_credential(*, enabled_only: bool = True) -> BrowserLoginCredential | None:
    return get_credential(TABCUT_ENV_CODE, TABCUT_PROVIDER, enabled_only=enabled_only)


def save_credential(
    env_code: str,
    provider: str,
    *,
    username: str,
    password: str | None,
    enabled: bool,
    updated_by: int | None,
) -> None:
    env_code = (env_code or DEFAULT_ENV_CODE).strip() or DEFAULT_ENV_CODE
    provider = (provider or DEFAULT_PROVIDER).strip() or DEFAULT_PROVIDER
    username = (username or "").strip()
    existing: BrowserLoginCredential | None = None
    if not username or password is None:
        existing = get_credential(env_code, provider, enabled_only=False)
    if not username and existing:
        username = existing.username
    if password is None:
        password_value = existing.password if existing else ""
    else:
        password_value = str(password)
    execute(
        "INSERT INTO browser_login_credentials "
        "(env_code, provider, username, password, enabled, updated_by) "
        "VALUES (%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE "
        "username=VALUES(username), password=VALUES(password), "
        "enabled=VALUES(enabled), updated_by=VALUES(updated_by)",
        (env_code, provider, username, password_value, int(enabled), updated_by),
    )


def mark_login_result(
    env_code: str = DEFAULT_ENV_CODE,
    provider: str = DEFAULT_PROVIDER,
    status: str = "",
    error: str | None = None,
) -> None:
    execute(
        "UPDATE browser_login_credentials SET last_login_at=NOW(), "
        "last_login_status=%s, last_error=%s "
        "WHERE env_code=%s AND provider=%s",
        (status, error, env_code, provider),
    )
