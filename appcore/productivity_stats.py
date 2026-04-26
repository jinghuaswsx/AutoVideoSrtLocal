"""F 子系统：员工产能报表 — 基于 task_events 的聚合。

详见 docs/superpowers/specs/2026-04-26-productivity-stats-design.md
"""
from __future__ import annotations

from datetime import datetime
from appcore.db import query_all


def get_daily_throughput(*, from_dt: datetime, to_dt: datetime) -> list[dict]:
    """日产汇总：每位员工每天的 approved + completed 数。"""
    return query_all(
        """
        SELECT te.actor_user_id AS user_id,
               u.username,
               DATE(te.created_at) AS day,
               COUNT(*) AS count
        FROM task_events te
        JOIN users u ON u.id = te.actor_user_id
        WHERE te.event_type IN ('approved', 'completed')
          AND te.created_at >= %s AND te.created_at < %s
          AND te.actor_user_id IS NOT NULL
        GROUP BY te.actor_user_id, DATE(te.created_at)
        ORDER BY u.username, day
        """,
        (from_dt, to_dt),
    )


def get_pass_rate(*, from_dt: datetime, to_dt: datetime) -> list[dict]:
    """通过率：approved / (approved + rejected) per user (按审核员 = actor)。"""
    rows = query_all(
        """
        SELECT te.actor_user_id AS user_id,
               u.username,
               SUM(CASE WHEN te.event_type='approved' THEN 1 ELSE 0 END) AS approved,
               SUM(CASE WHEN te.event_type='rejected' THEN 1 ELSE 0 END) AS rejected
        FROM task_events te
        JOIN users u ON u.id = te.actor_user_id
        WHERE te.event_type IN ('approved', 'rejected')
          AND te.created_at >= %s AND te.created_at < %s
          AND te.actor_user_id IS NOT NULL
        GROUP BY te.actor_user_id
        HAVING approved + rejected > 0
        """,
        (from_dt, to_dt),
    )
    for r in rows:
        approved = r.get("approved") or 0
        rejected = r.get("rejected") or 0
        total = approved + rejected
        r["approved"] = int(approved)
        r["rejected"] = int(rejected)
        r["pass_rate"] = round(float(approved) / float(total), 3) if total else 0
    rows.sort(key=lambda r: -r["pass_rate"])
    return rows


def get_rework_rate(*, from_dt: datetime, to_dt: datetime) -> list[dict]:
    """返工率：被打回数 / 提交总数 per submitter."""
    submits = {r["user_id"]: int(r["cnt"]) for r in query_all(
        """
        SELECT te.actor_user_id AS user_id, COUNT(*) AS cnt
        FROM task_events te
        WHERE te.event_type = 'submitted'
          AND te.created_at >= %s AND te.created_at < %s
          AND te.actor_user_id IS NOT NULL
        GROUP BY te.actor_user_id
        """,
        (from_dt, to_dt),
    )}
    rejects = {r["user_id"]: int(r["cnt"]) for r in query_all(
        """
        SELECT t.assignee_id AS user_id, COUNT(*) AS cnt
        FROM task_events te
        JOIN tasks t ON t.id = te.task_id
        WHERE te.event_type = 'rejected'
          AND te.created_at >= %s AND te.created_at < %s
          AND t.assignee_id IS NOT NULL
        GROUP BY t.assignee_id
        """,
        (from_dt, to_dt),
    )}
    user_ids = list(set(submits) | set(rejects))
    if not user_ids:
        return []
    fmt = ",".join(["%s"] * len(user_ids))
    name_rows = query_all(
        f"SELECT id, username FROM users WHERE id IN ({fmt})",
        tuple(user_ids),
    )
    names = {r["id"]: r["username"] for r in name_rows}

    out = []
    for uid in user_ids:
        s = submits.get(uid, 0)
        if s == 0:
            continue
        r = rejects.get(uid, 0)
        out.append({
            "user_id": uid,
            "username": names.get(uid, "?"),
            "submitted": s,
            "rejected": r,
            "rework_rate": round(r / s, 3),
        })
    out.sort(key=lambda x: -x["rework_rate"])
    return out
