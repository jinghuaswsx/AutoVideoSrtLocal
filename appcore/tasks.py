# appcore/tasks.py
"""任务中心 service 层 — 双层任务模型 + 状态机。

- 父任务（parent_task_id IS NULL）: 素材级，原始视频段
- 子任务（parent_task_id IS NOT NULL）: 国家级，翻译段

完整设计见 docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md。
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from appcore import mk_import as mk_import_svc
from appcore.db import execute, get_conn, query_one, query_all

# ---- 状态常量 ----
PARENT_PENDING = "pending"
PARENT_RAW_IN_PROGRESS = "raw_in_progress"
PARENT_RAW_REVIEW = "raw_review"
PARENT_RAW_DONE = "raw_done"
PARENT_ALL_DONE = "all_done"
PARENT_CANCELLED = "cancelled"

CHILD_BLOCKED = "blocked"
CHILD_ASSIGNED = "assigned"
CHILD_REVIEW = "review"
CHILD_DONE = "done"
CHILD_CANCELLED = "cancelled"

PARENT_NON_TERMINAL = (
    PARENT_PENDING, PARENT_RAW_IN_PROGRESS,
    PARENT_RAW_REVIEW, PARENT_RAW_DONE,
)
PARENT_TERMINAL = (PARENT_ALL_DONE, PARENT_CANCELLED)
CHILD_NON_TERMINAL = (CHILD_BLOCKED, CHILD_ASSIGNED, CHILD_REVIEW)
CHILD_TERMINAL = (CHILD_DONE, CHILD_CANCELLED)

# ---- 高层状态 rollup ----
def high_level_status(status: str) -> str:
    if status in (PARENT_ALL_DONE, CHILD_DONE):
        return "completed"
    if status in (PARENT_CANCELLED, CHILD_CANCELLED):
        return "terminated"
    return "in_progress"


def list_enabled_target_languages() -> list[dict]:
    rows = query_all(
        "SELECT code FROM media_languages "
        "WHERE enabled=1 AND code <> 'en' ORDER BY code"
    )
    return [{"code": str(row["code"]).upper()} for row in rows]


def list_product_english_items(product_id: int) -> list[dict]:
    rows = query_all(
        "SELECT id, filename, object_key FROM media_items "
        "WHERE product_id=%s AND lang='en' AND deleted_at IS NULL ORDER BY id DESC",
        (int(product_id),),
    )
    return [{"id": row["id"], "filename": row["filename"]} for row in rows]


# ---- 共用 helpers (后续 task 用) ----
def list_task_events(task_id: int) -> list[dict]:
    rows = query_all(
        "SELECT te.*, u.username AS actor_username "
        "FROM task_events te LEFT JOIN users u ON u.id=te.actor_user_id "
        "WHERE te.task_id=%s ORDER BY te.id ASC",
        (int(task_id),),
    )
    return [
        {
            "id": row["id"],
            "task_id": row["task_id"],
            "event_type": row["event_type"],
            "actor_user_id": row["actor_user_id"],
            "actor_username": row["actor_username"],
            "payload_json": row["payload_json"],
            "created_at": (
                row["created_at"].isoformat() if row.get("created_at") else None
            ),
        }
        for row in rows
    ]


def list_dispatch_pool_products() -> list[dict]:
    rows = query_all(
        "SELECT p.id AS product_id, p.name AS product_name, p.user_id AS owner_id, "
        "       (SELECT COUNT(*) FROM media_items mi WHERE mi.product_id=p.id "
        "        AND mi.lang='en' AND mi.deleted_at IS NULL) AS en_item_count "
        "FROM media_products p "
        "WHERE p.deleted_at IS NULL AND p.archived=0 "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM tasks t WHERE t.media_product_id=p.id "
        "  AND t.parent_task_id IS NULL "
        "  AND t.status NOT IN (%s, %s)"
        ") "
        "ORDER BY p.id DESC LIMIT 100",
        (PARENT_ALL_DONE, PARENT_CANCELLED),
    )
    return [dict(row) for row in rows]


def list_task_center_items(
    *,
    tab: str,
    user_id: int,
    can_process_raw_video: bool,
    keyword: str,
    high_status: str,
    page: int,
    page_size: int,
) -> dict:
    offset = (int(page) - 1) * int(page_size)
    where = ["1=1"]
    args: list = []

    if tab == "mine":
        where.append(
            "(t.assignee_id=%s OR "
            "(t.parent_task_id IS NULL AND t.status=%s AND %s))"
        )
        args.extend(
            [
                int(user_id),
                PARENT_PENDING,
                1 if can_process_raw_video else 0,
            ]
        )
    elif tab != "all":
        raise ValueError("invalid tab")

    if keyword:
        where.append("p.name LIKE %s")
        args.append(f"%{keyword}%")
    if high_status == "in_progress":
        where.append("t.status NOT IN (%s, %s, %s)")
        args.extend([PARENT_ALL_DONE, CHILD_DONE, PARENT_CANCELLED])
    elif high_status == "completed":
        where.append("t.status IN (%s, %s)")
        args.extend([PARENT_ALL_DONE, CHILD_DONE])
    elif high_status == "terminated":
        where.append("t.status=%s")
        args.append(PARENT_CANCELLED)

    sql = (
        "SELECT t.*, p.name AS product_name, "
        "       u.username AS assignee_username "
        "FROM tasks t "
        "JOIN media_products p ON p.id=t.media_product_id "
        "LEFT JOIN users u ON u.id=t.assignee_id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY t.id DESC "
        "LIMIT %s OFFSET %s"
    )
    rows = query_all(sql, (*args, int(page_size), offset))
    return {
        "items": [
            {
                "id": row["id"],
                "parent_task_id": row["parent_task_id"],
                "media_product_id": row["media_product_id"],
                "product_name": row["product_name"],
                "country_code": row["country_code"],
                "assignee_id": row["assignee_id"],
                "assignee_username": row["assignee_username"],
                "status": row["status"],
                "high_level": high_level_status(row["status"]),
                "created_at": (
                    row["created_at"].isoformat() if row.get("created_at") else None
                ),
                "updated_at": (
                    row["updated_at"].isoformat() if row.get("updated_at") else None
                ),
                "claimed_at": (
                    row["claimed_at"].isoformat() if row.get("claimed_at") else None
                ),
                "completed_at": (
                    row["completed_at"].isoformat()
                    if row.get("completed_at")
                    else None
                ),
                "cancelled_at": (
                    row["cancelled_at"].isoformat()
                    if row.get("cancelled_at")
                    else None
                ),
                "last_reason": row["last_reason"],
            }
            for row in rows
        ],
        "page": int(page),
        "page_size": int(page_size),
    }


def bind_parent_media_item(
    *,
    task_id: int,
    media_item_id: int,
    actor_user_id: int,
    is_admin: bool,
) -> None:
    item_id = int(media_item_id)
    row = query_one(
        "SELECT assignee_id, media_product_id FROM tasks "
        "WHERE id=%s AND parent_task_id IS NULL",
        (int(task_id),),
    )
    if not row:
        raise StateError("task not found")
    if row["assignee_id"] != int(actor_user_id) and not is_admin:
        raise PermissionError("forbidden")

    item = query_one(
        "SELECT id FROM media_items WHERE id=%s AND product_id=%s",
        (item_id, row["media_product_id"]),
    )
    if not item:
        raise ValueError("media_item not found or not under this product")

    execute(
        "UPDATE tasks SET media_item_id=%s, updated_at=NOW() WHERE id=%s",
        (item_id, int(task_id)),
    )


def _row(task_id: int) -> dict | None:
    return query_one("SELECT * FROM tasks WHERE id=%s", (int(task_id),))


def _write_event(
    cur, task_id: int, event_type: str,
    actor_user_id: int | None, payload: dict | None = None,
) -> None:
    cur.execute(
        "INSERT INTO task_events (task_id, event_type, actor_user_id, payload_json) "
        "VALUES (%s, %s, %s, %s)",
        (
            int(task_id), event_type,
            int(actor_user_id) if actor_user_id is not None else None,
            json.dumps(payload, ensure_ascii=False) if payload else None,
        ),
    )


def create_parent_task(
    *,
    media_product_id: int,
    media_item_id: int | None,
    countries: list[str],
    translator_id: int,
    created_by: int,
) -> int:
    """创建父任务 + 一并物化子任务 (status=blocked)。返回父任务 id。"""
    if not countries:
        raise ValueError("countries must be non-empty")
    norm_countries = [c.strip().upper() for c in countries if c and c.strip()]
    if not norm_countries:
        raise ValueError("countries must be non-empty after normalization")

    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO tasks "
                    "(parent_task_id, media_product_id, media_item_id, status, created_by) "
                    "VALUES (NULL, %s, %s, %s, %s)",
                    (int(media_product_id),
                     int(media_item_id) if media_item_id is not None else None,
                     PARENT_PENDING, int(created_by)),
                )
                parent_id = cur.lastrowid
                _write_event(cur, parent_id, "created", created_by,
                             {"countries": norm_countries,
                              "translator_id": int(translator_id)})
                for country in norm_countries:
                    cur.execute(
                        "INSERT INTO tasks "
                        "(parent_task_id, media_product_id, media_item_id, "
                        " country_code, assignee_id, status, created_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (parent_id, int(media_product_id),
                         int(media_item_id) if media_item_id is not None else None,
                         country, int(translator_id), CHILD_BLOCKED, int(created_by)),
                    )
                    child_id = cur.lastrowid
                    _write_event(cur, child_id, "created", created_by,
                                 {"country": country})
            conn.commit()
            return int(parent_id)
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def import_and_create_task(
    *,
    mk_video_metadata: dict,
    translator_id: int,
    countries: list[str],
    actor_user_id: int,
) -> dict:
    """Import a mk video + create parent task + N child tasks in one call.

    If the video is already imported, look up the existing product and create
    the task from it (skipping the import step).

    Returns:
        {"parent_task_id": int, "media_product_id": int, "media_item_id": int,
         "is_new_product": bool}
    """
    try:
        import_result = mk_import_svc.import_mk_video(
            mk_video_metadata=mk_video_metadata,
            translator_id=int(translator_id),
            actor_user_id=int(actor_user_id),
        )
        product_id = import_result["media_product_id"]
        item_id = import_result["media_item_id"]
        is_new = import_result["is_new_product"]
    except mk_import_svc.DuplicateError:
        existing = mk_import_svc.find_existing_product_item_by_meta(mk_video_metadata)
        if not existing or not existing.get("item_id"):
            raise
        product_id = existing["product_id"]
        item_id = existing["item_id"]
        is_new = False
    parent_id = create_parent_task(
        media_product_id=product_id,
        media_item_id=item_id,
        countries=countries,
        translator_id=int(translator_id),
        created_by=int(actor_user_id),
    )
    return {
        "parent_task_id": parent_id,
        "media_product_id": product_id,
        "media_item_id": item_id,
        "is_new_product": is_new,
    }


class ConflictError(RuntimeError):
    """Optimistic concurrency violation, e.g., already claimed."""


class StateError(RuntimeError):
    """Invalid state transition / precondition violation."""


def mark_uploaded(*, task_id: int, actor_user_id: int) -> None:
    """处理人标"已上传"，转入待审核。"""
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, assignee_id, media_item_id "
                    "FROM tasks WHERE id=%s AND parent_task_id IS NULL FOR UPDATE",
                    (int(task_id),),
                )
                row = cur.fetchone()
                if not row:
                    raise StateError("parent task not found")
                if row["status"] != PARENT_RAW_IN_PROGRESS:
                    raise StateError(
                        f"expected status raw_in_progress, got {row['status']}"
                    )
                if row["assignee_id"] != int(actor_user_id):
                    raise StateError("only assignee can mark uploaded")
                if row["media_item_id"] is None:
                    raise StateError("media_item not bound; upload first")
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=NULL, updated_at=NOW() "
                    "WHERE id=%s",
                    (PARENT_RAW_REVIEW, int(task_id)),
                )
                _write_event(cur, task_id, "raw_uploaded", actor_user_id, None)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def claim_parent(*, task_id: int, actor_user_id: int) -> None:
    """处理人认领父任务。乐观锁防并发。"""
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET assignee_id=%s, status=%s, "
                    "claimed_at=NOW(), updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NULL AND status=%s",
                    (int(actor_user_id), PARENT_RAW_IN_PROGRESS,
                     int(task_id), PARENT_PENDING),
                )
                if cur.rowcount == 0:
                    raise ConflictError("task not pending or already claimed")
                _write_event(cur, task_id, "claimed", actor_user_id, None)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def approve_raw(*, task_id: int, actor_user_id: int) -> None:
    """管理员审核通过原始视频，自动 unblock 所有 blocked 子任务。"""
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=NULL, updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NULL AND status=%s",
                    (PARENT_RAW_DONE, int(task_id), PARENT_RAW_REVIEW),
                )
                if cur.rowcount == 0:
                    raise StateError("parent not in raw_review")
                _write_event(cur, task_id, "approved", actor_user_id, None)

                cur.execute(
                    "SELECT id FROM tasks WHERE parent_task_id=%s AND status=%s",
                    (int(task_id), CHILD_BLOCKED),
                )
                child_ids = [r["id"] for r in cur.fetchall()]
                if child_ids:
                    fmt = ",".join(["%s"] * len(child_ids))
                    cur.execute(
                        f"UPDATE tasks SET status=%s, updated_at=NOW() "
                        f"WHERE id IN ({fmt})",
                        (CHILD_ASSIGNED, *child_ids),
                    )
                    for cid in child_ids:
                        _write_event(cur, cid, "unblocked", None, None)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


MIN_REASON_LEN = 10


def reject_raw(*, task_id: int, actor_user_id: int, reason: str) -> None:
    """管理员打回原始视频，状态回 raw_in_progress（同 assignee）。"""
    if not reason or len(reason.strip()) < MIN_REASON_LEN:
        raise ValueError(f"reason must be at least {MIN_REASON_LEN} characters")
    reason = reason.strip()
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=%s, updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NULL AND status=%s",
                    (PARENT_RAW_IN_PROGRESS, reason, int(task_id), PARENT_RAW_REVIEW),
                )
                if cur.rowcount == 0:
                    raise StateError("parent not in raw_review")
                _write_event(cur, task_id, "rejected", actor_user_id, {"reason": reason})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


class NotReadyError(RuntimeError):
    """compute_readiness gate failed; carries missing keys."""
    def __init__(self, missing: list[str], detail: str = ""):
        self.missing = missing
        super().__init__(detail or f"missing: {missing}")


def _find_target_lang_item(product_id: int, lang: str) -> dict | None:
    lang_code = (lang or "").strip().lower()
    return query_one(
        "SELECT * FROM media_items "
        "WHERE product_id=%s AND lang=%s AND deleted_at IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (int(product_id), lang_code),
    )


def _find_product(product_id: int) -> dict | None:
    return query_one(
        "SELECT * FROM media_products WHERE id=%s", (int(product_id),)
    )


def get_child_readiness(task_id: int) -> dict:
    from appcore import pushes

    row = query_one(
        "SELECT t.media_product_id, t.country_code "
        "FROM tasks t WHERE t.id=%s AND t.parent_task_id IS NOT NULL",
        (int(task_id),),
    )
    if not row:
        raise StateError("child task not found")

    item = _find_target_lang_item(row["media_product_id"], row["country_code"])
    if not item:
        return {
            "ready": False,
            "missing": ["lang_item_missing"],
            "country_code": row["country_code"],
            "readiness": {},
        }

    product = _find_product(row["media_product_id"])
    readiness = pushes.compute_readiness(item, product)
    missing = [
        key
        for key, value in readiness.items()
        if not str(key).endswith("_reason") and not value
    ]
    return {
        "ready": pushes.is_ready(readiness),
        "missing": missing,
        "readiness": {
            key: bool(value)
            for key, value in readiness.items()
            if not str(key).endswith("_reason")
        },
        "country_code": row["country_code"],
        "media_item_id": item["id"],
    }


def list_unbound_items_for_task(task_id: int) -> list[dict]:
    """List media_items matching this task's product+lang but not yet bound."""
    row = _row(task_id)
    if not row:
        raise StateError("task not found")
    product_id = row["media_product_id"]
    if row["parent_task_id"] is not None:
        lang = (row["country_code"] or "").strip().lower()
        rows = query_all(
            "SELECT mi.* FROM media_items mi "
            "WHERE mi.product_id=%s AND mi.lang=%s AND mi.deleted_at IS NULL "
            "AND mi.task_id IS NULL "
            "ORDER BY mi.id DESC",
            (int(product_id), lang),
        )
    else:
        child_langs = [
            r["country_code"] for r in query_all(
                "SELECT DISTINCT country_code FROM tasks WHERE parent_task_id=%s AND country_code IS NOT NULL",
                (int(task_id),),
            )
        ]
        if not child_langs:
            return []
        langs_lower = [c.strip().lower() for c in child_langs]
        placeholders = ",".join(["%s"] * len(langs_lower))
        rows = query_all(
            f"SELECT mi.* FROM media_items mi "
            f"WHERE mi.product_id=%s AND mi.lang IN ({placeholders}) "
            f"AND mi.deleted_at IS NULL AND mi.task_id IS NULL "
            f"ORDER BY mi.lang, mi.id DESC",
            [int(product_id)] + langs_lower,
        )
    return [dict(r) for r in rows]


def list_task_artifacts(
    *, task_id: int, is_parent: bool = False
) -> list[dict]:
    """List media_items produced by a task.

    - For child tasks: items with task_id = this child task
    - For parent tasks: items produced by all child tasks under this parent
    """
    if not is_parent:
        rows = query_all(
            "SELECT mi.*, p.name AS product_name "
            "FROM media_items mi JOIN media_products p ON p.id=mi.product_id "
            "WHERE mi.task_id=%s AND mi.deleted_at IS NULL "
            "ORDER BY mi.lang, mi.id DESC",
            (int(task_id),),
        )
    else:
        child_ids = [
            row["id"]
            for row in query_all(
                "SELECT id FROM tasks WHERE parent_task_id=%s",
                (int(task_id),),
            )
        ]
        if not child_ids:
            return []
        placeholders = ",".join(["%s"] * len(child_ids))
        rows = query_all(
            f"SELECT mi.*, p.name AS product_name "
            f"FROM media_items mi JOIN media_products p ON p.id=mi.product_id "
            f"WHERE mi.task_id IN ({placeholders}) AND mi.deleted_at IS NULL "
            f"ORDER BY mi.lang, mi.id DESC",
            child_ids,
        )
    return [dict(row) for row in rows]


def submit_child(*, task_id: int, actor_user_id: int) -> None:
    """翻译员提交子任务；调 compute_readiness 做产物齐全 gate。"""
    from appcore import pushes
    row = query_one(
        "SELECT * FROM tasks WHERE id=%s AND parent_task_id IS NOT NULL",
        (int(task_id),),
    )
    if not row:
        raise StateError("child task not found")
    if row["status"] != CHILD_ASSIGNED:
        raise StateError(f"expected status assigned, got {row['status']}")
    if row["assignee_id"] != int(actor_user_id):
        raise StateError("only assignee can submit")

    item = _find_target_lang_item(row["media_product_id"], row["country_code"])
    if not item:
        raise NotReadyError(missing=["lang_item_missing"],
                            detail=f"missing: lang_item_missing (no media_item with lang={row['country_code']})")
    product = _find_product(row["media_product_id"])
    readiness = pushes.compute_readiness(item, product)
    if not pushes.is_ready(readiness):
        missing = [k for k, v in readiness.items()
                   if not str(k).endswith("_reason") and not v]
        raise NotReadyError(missing=missing, detail=f"readiness failed: {missing}")

    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=NULL, updated_at=NOW() "
                    "WHERE id=%s AND status=%s",
                    (CHILD_REVIEW, int(task_id), CHILD_ASSIGNED),
                )
                if cur.rowcount == 0:
                    raise StateError("child not in assigned (race)")
                _write_event(cur, task_id, "submitted", actor_user_id, None)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def approve_child(*, task_id: int, actor_user_id: int) -> None:
    """管理员审核通过翻译；若该父任务下所有子都 done/cancelled 且至少一条 done，
    则父任务自动 all_done。"""
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=NULL, "
                    "completed_at=NOW(), updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NOT NULL AND status=%s",
                    (CHILD_DONE, int(task_id), CHILD_REVIEW),
                )
                if cur.rowcount == 0:
                    raise StateError("child not in review")
                _write_event(cur, task_id, "approved", actor_user_id, None)

                cur.execute(
                    "SELECT parent_task_id FROM tasks WHERE id=%s",
                    (int(task_id),),
                )
                parent_id = cur.fetchone()["parent_task_id"]
                cur.execute(
                    "SELECT status FROM tasks WHERE parent_task_id=%s", (parent_id,)
                )
                statuses = [r["status"] for r in cur.fetchall()]
                terminal = all(s in (CHILD_DONE, CHILD_CANCELLED) for s in statuses)
                any_done = any(s == CHILD_DONE for s in statuses)
                if terminal and any_done:
                    cur.execute(
                        "UPDATE tasks SET status=%s, completed_at=NOW(), updated_at=NOW() "
                        "WHERE id=%s AND status=%s",
                        (PARENT_ALL_DONE, int(parent_id), PARENT_RAW_DONE),
                    )
                    if cur.rowcount:
                        _write_event(cur, parent_id, "completed", None, None)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def cancel_parent(*, task_id: int, actor_user_id: int, reason: str) -> None:
    """admin 取消父任务；级联取消所有非 done 子任务，已 done 保留。"""
    if not reason or len(reason.strip()) < MIN_REASON_LEN:
        raise ValueError(f"reason must be at least {MIN_REASON_LEN} characters")
    reason = reason.strip()
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=%s, "
                    "cancelled_at=NOW(), updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NULL "
                    "AND status IN (%s,%s,%s,%s)",
                    (PARENT_CANCELLED, reason, int(task_id),
                     PARENT_PENDING, PARENT_RAW_IN_PROGRESS,
                     PARENT_RAW_REVIEW, PARENT_RAW_DONE),
                )
                if cur.rowcount == 0:
                    raise StateError("parent not in cancellable state")
                cur.execute(
                    "SELECT id FROM tasks WHERE parent_task_id=%s "
                    "AND status IN (%s,%s,%s)",
                    (int(task_id), CHILD_BLOCKED, CHILD_ASSIGNED, CHILD_REVIEW),
                )
                cascaded = [r["id"] for r in cur.fetchall()]
                if cascaded:
                    fmt = ",".join(["%s"] * len(cascaded))
                    cur.execute(
                        f"UPDATE tasks SET status=%s, last_reason=%s, "
                        f"cancelled_at=NOW(), updated_at=NOW() WHERE id IN ({fmt})",
                        (CHILD_CANCELLED, "parent cancelled: " + reason, *cascaded),
                    )
                    for cid in cascaded:
                        _write_event(cur, cid, "cancelled", actor_user_id,
                                     {"cascaded_from": int(task_id)})
                _write_event(cur, task_id, "cancelled", actor_user_id,
                             {"reason": reason, "cascaded_child_count": len(cascaded)})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def reject_child(*, task_id: int, actor_user_id: int, reason: str) -> None:
    """管理员打回翻译；状态回 assigned（同 assignee）。"""
    if not reason or len(reason.strip()) < MIN_REASON_LEN:
        raise ValueError(f"reason must be at least {MIN_REASON_LEN} characters")
    reason = reason.strip()
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=%s, updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NOT NULL AND status=%s",
                    (CHILD_ASSIGNED, reason, int(task_id), CHILD_REVIEW),
                )
                if cur.rowcount == 0:
                    raise StateError("child not in review")
                _write_event(cur, task_id, "rejected", actor_user_id,
                             {"reason": reason})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def cancel_child(*, task_id: int, actor_user_id: int, reason: str) -> None:
    """admin 取消单个子任务；父任务状态不变。"""
    if not reason or len(reason.strip()) < MIN_REASON_LEN:
        raise ValueError(f"reason must be at least {MIN_REASON_LEN} characters")
    reason = reason.strip()
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=%s, "
                    "cancelled_at=NOW(), updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NOT NULL "
                    "AND status IN (%s,%s,%s)",
                    (CHILD_CANCELLED, reason, int(task_id),
                     CHILD_BLOCKED, CHILD_ASSIGNED, CHILD_REVIEW),
                )
                if cur.rowcount == 0:
                    raise StateError("child not in cancellable state")
                _write_event(cur, task_id, "cancelled", actor_user_id,
                             {"reason": reason})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def on_product_owner_changed(
    *, product_id: int, new_user_id: int, actor_user_id: int | None = None,
) -> int:
    """素材产品负责人变更时被调用。把状态非 done/cancelled 的子任务的
    assignee_id 同步到 new_user_id。返回受影响的子任务数。"""
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, assignee_id FROM tasks "
                    "WHERE media_product_id=%s AND parent_task_id IS NOT NULL "
                    "AND status NOT IN (%s, %s)",
                    (int(product_id), CHILD_DONE, CHILD_CANCELLED),
                )
                rows = cur.fetchall()
                affected = 0
                for r in rows:
                    if r["assignee_id"] == int(new_user_id):
                        continue
                    cur.execute(
                        "UPDATE tasks SET assignee_id=%s, updated_at=NOW() "
                        "WHERE id=%s",
                        (int(new_user_id), r["id"]),
                    )
                    _write_event(cur, r["id"], "assignee_changed", actor_user_id,
                                 {"old": r["assignee_id"], "new": int(new_user_id)})
                    affected += 1
            conn.commit()
            return affected
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
