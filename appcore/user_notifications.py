"""Per-user in-app notification helpers.

首期只接任务中心。设计见
docs/superpowers/specs/2026-05-20-task-message-center-design.md。
"""
from __future__ import annotations

import json
from typing import Iterable

from appcore.db import execute, query_all, query_one
from appcore.permissions import merge_with_defaults

SOURCE_TASK = "task"


def _coerce_permissions(raw) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _has_effective_permission(row: dict, code: str) -> bool:
    perms = merge_with_defaults(str(row.get("role") or "user"), _coerce_permissions(row.get("permissions")))
    return bool(perms.get(code))


def _task_url(task_id: int) -> str:
    return f"/tasks/?task_id={int(task_id)}"


def _insert_notification(
    cur,
    *,
    user_id: int,
    source_type: str,
    source_id: int,
    event_type: str,
    title: str,
    body: str,
    target_url: str,
) -> None:
    cur.execute(
        "INSERT INTO user_notifications "
        "(user_id, source_type, source_id, event_type, title, body, target_url) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            int(user_id),
            source_type,
            int(source_id),
            event_type,
            title,
            body,
            target_url,
        ),
    )


def _insert_for_users(
    cur,
    user_ids: Iterable[int],
    *,
    source_id: int,
    event_type: str,
    title: str,
    body: str,
) -> int:
    inserted = 0
    seen: set[int] = set()
    for raw_user_id in user_ids:
        if raw_user_id is None:
            continue
        user_id = int(raw_user_id)
        if user_id in seen:
            continue
        seen.add(user_id)
        _insert_notification(
            cur,
            user_id=user_id,
            source_type=SOURCE_TASK,
            source_id=int(source_id),
            event_type=event_type,
            title=title,
            body=body,
            target_url=_task_url(int(source_id)),
        )
        inserted += 1
    return inserted


def _active_raw_processor_user_ids(cur) -> list[int]:
    cur.execute(
        "SELECT id, username, role, permissions "
        "FROM users WHERE is_active=1 ORDER BY id ASC"
    )
    return [
        int(row["id"])
        for row in cur.fetchall()
        if _has_effective_permission(row, "can_process_raw_video")
    ]


def notify_pending_raw_task(cur, *, task_id: int, product_name: str) -> int:
    """Notify every active raw-video processor that a parent task is claimable."""
    return _insert_for_users(
        cur,
        _active_raw_processor_user_ids(cur),
        source_id=int(task_id),
        event_type="task_parent_pending",
        title="有新的原始素材待认领",
        body=f"{product_name} 已进入待认领队列",
    )


def notify_child_blocked(
    cur,
    *,
    task_id: int,
    assignee_id: int,
    product_name: str,
    country_code: str,
) -> int:
    return _insert_for_users(
        cur,
        [int(assignee_id)],
        source_id=int(task_id),
        event_type="task_child_blocked",
        title="有新的翻译任务待解锁",
        body=f"{product_name} · {country_code} 已分配给你，等待原始素材审核",
    )


def notify_child_assigned(cur, *, task_id: int, product_name: str) -> int:
    row = _task_assignee_and_country(cur, task_id)
    if not row or row.get("assignee_id") is None:
        return 0
    country = row.get("country_code") or ""
    return _insert_for_users(
        cur,
        [int(row["assignee_id"])],
        source_id=int(task_id),
        event_type="task_child_assigned",
        title="翻译任务已可处理",
        body=f"{product_name} · {country} 已解锁，请处理翻译产物",
    )


def notify_parent_rejected(cur, *, task_id: int, product_name: str) -> int:
    row = _task_assignee_and_country(cur, task_id)
    if not row or row.get("assignee_id") is None:
        return 0
    return _insert_for_users(
        cur,
        [int(row["assignee_id"])],
        source_id=int(task_id),
        event_type="task_parent_rejected",
        title="原始素材任务被打回",
        body=f"{product_name} 需要重新处理",
    )


def notify_child_rejected(cur, *, task_id: int, product_name: str) -> int:
    row = _task_assignee_and_country(cur, task_id)
    if not row or row.get("assignee_id") is None:
        return 0
    country = row.get("country_code") or ""
    return _insert_for_users(
        cur,
        [int(row["assignee_id"])],
        source_id=int(task_id),
        event_type="task_child_rejected",
        title="翻译任务被打回",
        body=f"{product_name} · {country} 需要重新处理",
    )


def _task_assignee_and_country(cur, task_id: int) -> dict | None:
    cur.execute(
        "SELECT assignee_id, country_code FROM tasks WHERE id=%s",
        (int(task_id),),
    )
    return cur.fetchone()


def count_unread(*, user_id: int) -> int:
    row = query_one(
        "SELECT COUNT(*) AS unread_count FROM user_notifications "
        "WHERE user_id=%s AND read_at IS NULL",
        (int(user_id),),
    )
    return int((row or {}).get("unread_count") or 0)


def list_user_notifications(*, user_id: int, limit: int = 20) -> list[dict]:
    rows = query_all(
        "SELECT id, source_type, source_id, event_type, title, body, target_url, "
        "       read_at, created_at "
        "FROM user_notifications "
        "WHERE user_id=%s "
        "ORDER BY read_at IS NULL DESC, created_at DESC, id DESC "
        "LIMIT %s",
        (int(user_id), int(limit)),
    )
    return [_serialize_notification(row) for row in rows]


def mark_read(*, notification_id: int, user_id: int) -> int:
    return execute(
        "UPDATE user_notifications SET read_at=COALESCE(read_at, NOW()) "
        "WHERE id=%s AND user_id=%s",
        (int(notification_id), int(user_id)),
    )


def _serialize_notification(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "event_type": row["event_type"],
        "title": row["title"],
        "body": row.get("body"),
        "target_url": row["target_url"],
        "read_at": row["read_at"].isoformat() if row.get("read_at") else None,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }
