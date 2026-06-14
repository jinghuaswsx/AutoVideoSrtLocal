"""广告预警处理状态（已处理/忽略标记）。

一个预警对象（高亏损 AD 或商品语言）只保留一条最新状态，重复标记走 upsert。
Docs anchor: docs/superpowers/specs/2026-06-12-ad-alert-action-workflow-design.md
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from appcore.db import execute, query

SCOPE_HIGH_LOSS = "high_loss"
SCOPE_LANGUAGE = "language"
SCOPE_LONG_TERM_LOSS = "long_term_loss"
VALID_SCOPES = (SCOPE_HIGH_LOSS, SCOPE_LANGUAGE, SCOPE_LONG_TERM_LOSS)

ACTION_RESOLVED = "resolved"
ACTION_IGNORED = "ignored"
VALID_ACTIONS = (ACTION_RESOLVED, ACTION_IGNORED)

ACTION_LABELS = {
    ACTION_RESOLVED: "已处理",
    ACTION_IGNORED: "已忽略",
}

_NOTE_LIMIT = 500


def high_loss_target_key(ad_account_id: Any, code: Any) -> str:
    account = str(ad_account_id or "").strip().removeprefix("act_")
    return f"{account}:{str(code or '').strip()}"


def long_term_loss_target_key(product_id: Any) -> str:
    return f"product:{int(product_id)}"


def language_target_key(product_id: Any, lang: Any) -> str:
    return f"{int(product_id or 0)}:{str(lang or '').strip().lower()}"


def _validate(scope: str, target_key: str, action: str | None = None) -> None:
    if scope not in VALID_SCOPES:
        raise ValueError(f"scope must be one of {VALID_SCOPES}, got {scope!r}")
    if not str(target_key or "").strip():
        raise ValueError("target_key required")
    if action is not None and action not in VALID_ACTIONS:
        raise ValueError(f"action must be one of {VALID_ACTIONS}, got {action!r}")


def set_action(
    scope: str,
    target_key: str,
    action: str,
    *,
    note: str | None = None,
    operator_user_id: int | None = None,
) -> dict[str, Any]:
    """标记或更新一条预警的处理状态。"""
    _validate(scope, target_key, action)
    clean_note = (str(note or "").strip() or None)
    if clean_note:
        clean_note = clean_note[:_NOTE_LIMIT]
    execute(
        """
        INSERT INTO ad_alert_actions (scope, target_key, action, note, operator_user_id)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          action=VALUES(action),
          note=VALUES(note),
          operator_user_id=VALUES(operator_user_id)
        """,
        (scope, str(target_key).strip(), action, clean_note, operator_user_id),
    )
    return {
        "scope": scope,
        "target_key": str(target_key).strip(),
        "action": action,
        "note": clean_note,
        "operator_user_id": operator_user_id,
    }


def clear_action(scope: str, target_key: str) -> bool:
    """取消一条预警的处理状态标记。"""
    _validate(scope, target_key)
    affected = execute(
        "DELETE FROM ad_alert_actions WHERE scope=%s AND target_key=%s",
        (scope, str(target_key).strip()),
    )
    return bool(affected)


def get_actions(scope: str, target_keys: list[str]) -> dict[str, dict[str, Any]]:
    """批量查询一组预警对象的处理状态，返回 target_key → 状态信息。"""
    _validate(scope, "_batch_")
    keys = [str(key).strip() for key in target_keys if str(key or "").strip()]
    if not keys:
        return {}
    placeholders = ",".join(["%s"] * len(keys))
    rows = query(
        "SELECT target_key, action, note, operator_user_id, updated_at "
        f"FROM ad_alert_actions WHERE scope=%s AND target_key IN ({placeholders})",
        (scope, *keys),
    )
    result: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        key = str(row.get("target_key") or "")
        if not key:
            continue
        action = str(row.get("action") or "")
        result[key] = {
            "action": action,
            "action_label": ACTION_LABELS.get(action, action),
            "note": row.get("note"),
            "operator_user_id": row.get("operator_user_id"),
            "updated_at": _iso(row.get("updated_at")),
        }
    return result


def _iso(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value) if value else None
