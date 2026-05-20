# appcore/tasks.py
"""任务中心 service 层 — 双层任务模型 + 状态机。

- 父任务（parent_task_id IS NULL）: 素材级，原始视频段
- 子任务（parent_task_id IS NOT NULL）: 国家级，翻译段

完整设计见 docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md。
"""
from __future__ import annotations

import json
from typing import Any, Iterable
from urllib.parse import urlencode

from appcore import mk_import as mk_import_svc
from appcore import user_notifications as notifications_svc
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
        "SELECT code, name_zh FROM media_languages "
        "WHERE enabled=1 AND code <> 'en' ORDER BY code"
    )
    languages = []
    for row in rows:
        code = str(row["code"] or "").strip().upper()
        if not code:
            continue
        name_zh = str(row.get("name_zh") or code).strip() or code
        languages.append({
            "code": code,
            "name_zh": name_zh,
            "label": f"{name_zh} ({code})",
        })
    return languages


def list_product_english_items(product_id: int) -> list[dict]:
    rows = query_all(
        "SELECT id, filename, object_key FROM media_items "
        "WHERE product_id=%s AND lang='en' AND deleted_at IS NULL ORDER BY id DESC",
        (int(product_id),),
    )
    return [{"id": row["id"], "filename": row["filename"]} for row in rows]


# ---- 共用 helpers (后续 task 用) ----
def _user_display_name_expr(alias: str) -> str:
    row = query_one(
        "SELECT 1 AS ok FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' "
        "AND COLUMN_NAME = 'xingming'"
    )
    prefix = f"{alias}." if alias else ""
    if row:
        return f"COALESCE(NULLIF(TRIM({prefix}xingming), ''), {prefix}username)"
    return f"{prefix}username"


def _parse_event_payload_obj(payload_json: Any) -> dict:
    value = payload_json
    for _ in range(2):
        if not isinstance(value, str):
            break
        text = value.strip()
        if not text or text[0] not in "{[\"":
            break
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            break
    return value if isinstance(value, dict) else {}


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _payload_user_ids(payload: dict) -> set[int]:
    ids: set[int] = set()
    for key in ("translator_id", "assignee_id", "old", "new"):
        user_id = _positive_int(payload.get(key))
        if user_id is not None:
            ids.add(user_id)
    return ids


def _load_user_display_context(user_ids: Iterable[int]) -> dict[str, dict]:
    normalized_ids = sorted({int(uid) for uid in user_ids if int(uid) > 0})
    if not normalized_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(normalized_ids))
    display_expr = _user_display_name_expr("u")
    rows = query_all(
        f"SELECT u.id, u.username, {display_expr} AS display_name "
        "FROM users u "
        f"WHERE u.id IN ({placeholders})",
        tuple(normalized_ids),
    )
    result: dict[str, dict] = {}
    for row in rows:
        user_id = int(row["id"])
        username = str(row.get("username") or "")
        display_name = str(row.get("display_name") or username).strip()
        result[str(user_id)] = {
            "id": user_id,
            "username": username,
            "display_name": display_name or username,
        }
    return result


def list_task_events(task_id: int) -> list[dict]:
    actor_name_expr = _user_display_name_expr("u")
    rows = query_all(
        f"SELECT te.*, u.username AS actor_username, {actor_name_expr} AS actor_display_name "
        "FROM task_events te LEFT JOIN users u ON u.id=te.actor_user_id "
        "WHERE te.task_id=%s ORDER BY te.id ASC",
        (int(task_id),),
    )
    payload_by_event_id: dict[int, dict] = {}
    payload_user_ids: set[int] = set()
    for row in rows:
        payload = _parse_event_payload_obj(row.get("payload_json"))
        payload_by_event_id[int(row["id"])] = payload
        payload_user_ids.update(_payload_user_ids(payload))

    user_context = _load_user_display_context(payload_user_ids)
    events = []
    for row in rows:
        event_id = int(row["id"])
        payload = payload_by_event_id.get(event_id, {})
        event_user_context = {
            str(user_id): user_context[str(user_id)]
            for user_id in _payload_user_ids(payload)
            if str(user_id) in user_context
        }
        item = {
            "id": row["id"],
            "task_id": row["task_id"],
            "event_type": row["event_type"],
            "actor_user_id": row["actor_user_id"],
            "actor_username": row["actor_username"],
            "actor_display_name": row.get("actor_display_name") or row["actor_username"],
            "payload_json": row["payload_json"],
            "created_at": (
                row["created_at"].isoformat() if row.get("created_at") else None
            ),
        }
        if event_user_context:
            item["payload_context"] = {"users": event_user_context}
        events.append(item)
    return events


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

    assignee_name_expr = _user_display_name_expr("u")
    sql = (
        "SELECT t.*, p.name AS product_name, p.product_code AS product_code, "
        f"       u.username AS assignee_username, {assignee_name_expr} AS assignee_display_name "
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
                "product_code": row.get("product_code"),
                "country_code": row["country_code"],
                "assignee_id": row["assignee_id"],
                "assignee_username": row["assignee_username"],
                "assignee_display_name": (
                    row.get("assignee_display_name") or row["assignee_username"]
                ),
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


def _product_name_for_notification(cur, product_id: int) -> str:
    cur.execute("SELECT name FROM media_products WHERE id=%s", (int(product_id),))
    row = cur.fetchone()
    if row and row.get("name"):
        return str(row["name"])
    return f"产品 #{int(product_id)}"


def _task_product_id_for_notification(cur, task_id: int) -> int | None:
    cur.execute("SELECT media_product_id FROM tasks WHERE id=%s", (int(task_id),))
    row = cur.fetchone()
    if not row:
        return None
    return int(row["media_product_id"])


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
    raw_processor_id: int | None = None,
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
                if raw_processor_id is not None:
                    cur.execute(
                        "INSERT INTO tasks "
                        "(parent_task_id, media_product_id, media_item_id, assignee_id, status, claimed_at, created_by) "
                        "VALUES (NULL, %s, %s, %s, %s, NOW(), %s)",
                        (
                            int(media_product_id),
                            int(media_item_id) if media_item_id is not None else None,
                            int(raw_processor_id),
                            PARENT_RAW_IN_PROGRESS,
                            int(created_by),
                        ),
                    )
                else:
                    cur.execute(
                        "INSERT INTO tasks "
                        "(parent_task_id, media_product_id, media_item_id, status, created_by) "
                        "VALUES (NULL, %s, %s, %s, %s)",
                        (
                            int(media_product_id),
                            int(media_item_id) if media_item_id is not None else None,
                            PARENT_PENDING,
                            int(created_by),
                        ),
                    )
                parent_id = cur.lastrowid
                created_payload = {
                    "countries": norm_countries,
                    "translator_id": int(translator_id),
                }
                if raw_processor_id is not None:
                    created_payload["raw_processor_id"] = int(raw_processor_id)
                _write_event(cur, parent_id, "created", created_by, created_payload)
                product_name = _product_name_for_notification(cur, int(media_product_id))
                if raw_processor_id is not None:
                    notifications_svc.notify_parent_assigned(
                        cur,
                        task_id=parent_id,
                        assignee_id=int(raw_processor_id),
                        product_name=product_name,
                    )
                else:
                    notifications_svc.notify_pending_raw_task(
                        cur,
                        task_id=parent_id,
                        product_name=product_name,
                    )
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
                    notifications_svc.notify_child_blocked(
                        cur,
                        task_id=child_id,
                        assignee_id=int(translator_id),
                        product_name=product_name,
                        country_code=country,
                    )
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
        warnings = list(import_result.get("warnings") or [])
    except mk_import_svc.DuplicateError:
        existing = mk_import_svc.find_existing_product_item_by_meta(mk_video_metadata)
        if not existing or not existing.get("item_id"):
            raise
        product_id = existing["product_id"]
        item_id = existing["item_id"]
        is_new = False
        warnings = list(existing.get("warnings") or [])
    parent_id = create_parent_task(
        media_product_id=product_id,
        media_item_id=item_id,
        countries=countries,
        translator_id=int(translator_id),
        raw_processor_id=None,
        created_by=int(actor_user_id),
    )
    result = {
        "parent_task_id": parent_id,
        "media_product_id": product_id,
        "media_item_id": item_id,
        "is_new_product": is_new,
    }
    if warnings:
        result["warnings"] = warnings
    return result


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
    from appcore import task_raw_source_bridge

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

                raw_result = task_raw_source_bridge.ensure_raw_source_for_parent_task(
                    task_id=task_id,
                    actor_user_id=actor_user_id,
                )
                _write_event(
                    cur,
                    task_id,
                    "raw_source_created" if raw_result.get("created") else "raw_source_updated",
                    actor_user_id,
                    {"raw_source_id": raw_result.get("raw_source_id")},
                )

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
                    product_id = _task_product_id_for_notification(cur, task_id)
                    product_name = (
                        _product_name_for_notification(cur, product_id)
                        if product_id is not None else f"任务 #{int(task_id)}"
                    )
                    for cid in child_ids:
                        notifications_svc.notify_child_assigned(
                            cur,
                            task_id=cid,
                            product_name=product_name,
                        )
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
                product_id = _task_product_id_for_notification(cur, task_id)
                product_name = (
                    _product_name_for_notification(cur, product_id)
                    if product_id is not None else f"任务 #{int(task_id)}"
                )
                notifications_svc.notify_parent_rejected(
                    cur,
                    task_id=task_id,
                    product_name=product_name,
                )
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


def _medias_search_url(
    *,
    product_code: str | None,
    task_id: int | None,
    product_id: int | None,
    lang: str | None,
    action: str = "translate",
) -> str:
    params: list[tuple[str, str]] = []
    code = (product_code or "").strip()
    if code:
        params.append(("q", code))
    if task_id:
        params.append(("from_task", str(int(task_id))))
    if product_id:
        params.append(("product", str(int(product_id))))
    lang_code = (lang or "").strip().lower()
    if lang_code:
        params.append(("lang", lang_code))
    if action:
        params.append(("action", action))
    return "/medias/?" + urlencode(params)


def _acceptance_check(
    key: str,
    label: str,
    ok: bool,
    *,
    required: bool = True,
    reason: str = "",
    **extra: Any,
) -> dict:
    payload = {
        "key": key,
        "label": label,
        "ok": bool(ok),
        "required": bool(required),
        "reason": reason or "",
    }
    payload.update(extra)
    return payload


def _readiness_bool(readiness: dict, key: str) -> bool:
    return bool((readiness or {}).get(key))


def _detail_images_status(product_id: int, lang: str) -> dict:
    from appcore import medias

    def _static(rows: list[dict]) -> list[dict]:
        return [row for row in rows if not medias.detail_image_is_gif(row)]

    source_rows = _static(medias.list_detail_images(int(product_id), "en") or [])
    target_rows = _static(medias.list_detail_images(int(product_id), (lang or "").lower()) or [])
    source_count = len(source_rows)
    target_count = len(target_rows)
    required = source_count > 0
    ok = (not required) or target_count > 0
    if ok:
        reason = "" if required else "英文无静态详情图，不要求"
    else:
        reason = f"英文详情图 {source_count} 张，目标语种详情图 {target_count} 张"
    return {
        "ok": ok,
        "required": required,
        "source_count": source_count,
        "target_count": target_count,
        "reason": reason,
    }


def _product_link_availability_status(
    product_id: int,
    lang: str,
    product: dict | None,
) -> dict:
    from appcore import link_availability, product_link_domains, pushes

    link_rows = pushes.resolve_product_page_urls(lang, product or {})
    if not link_rows:
        return {
            "ok": False,
            "required": True,
            "reason": "未配置目标语种商品链接",
            "links": [],
        }

    latest_rows = link_availability.list_results(int(product_id), lang) or []
    latest_by_domain = {
        str(row.get("domain") or "").strip().lower(): row
        for row in latest_rows
    }
    links: list[dict] = []
    failures: list[str] = []
    for row in link_rows:
        url = str(row.get("url") or "").strip()
        domain = (
            str(row.get("domain") or "").strip().lower()
            or product_link_domains.domain_from_url(url)
        )
        latest = latest_by_domain.get(domain)
        ok = bool(latest and latest.get("ok"))
        error = ""
        http_status = None
        checked_at = ""
        if latest:
            error = latest.get("error") or ""
            http_status = latest.get("http_status")
            checked_at = latest.get("checked_at") or ""
        else:
            error = "missing_probe"
        if not ok:
            failures.append(f"{domain or url} 未探活" if error == "missing_probe" else f"{domain or url} {error}")
        links.append(
            {
                "domain": domain,
                "url": url,
                "ok": ok,
                "error": error or None,
                "http_status": http_status,
                "checked_at": checked_at,
            }
        )

    return {
        "ok": not failures,
        "required": True,
        "reason": "；".join(failures),
        "links": links,
    }


def _child_acceptance_payload(
    *,
    task_id: int,
    row: dict,
    item: dict | None,
    product: dict | None,
    readiness: dict,
) -> dict:
    product_id = int(row["media_product_id"])
    lang = (row.get("country_code") or "").strip().lower()
    product_code = (
        (product or {}).get("product_code")
        or row.get("product_code")
        or ""
    )

    if not item:
        checks = [
            _acceptance_check(
                "localized_media_item",
                "目标语种素材",
                False,
                reason="未找到该语种 media_item",
            )
        ]
        return {
            "ready": False,
            "missing": ["lang_item_missing"],
            "readiness": {},
            "checks": checks,
            "country_code": row["country_code"],
            "product_code": product_code,
            "media_search_url": _medias_search_url(
                product_code=product_code,
                task_id=task_id,
                product_id=product_id,
                lang=lang,
            ),
        }

    detail_status = _detail_images_status(product_id, lang)
    link_status = _product_link_availability_status(product_id, lang, product)
    checks = [
        _acceptance_check("localized_media_item", "目标语种素材", True),
        _acceptance_check(
            "translated_video",
            "视频翻译结果",
            _readiness_bool(readiness, "has_object"),
        ),
        _acceptance_check(
            "translated_cover",
            "封面翻译结果",
            _readiness_bool(readiness, "has_cover"),
        ),
        _acceptance_check(
            "translated_copywriting",
            "文案翻译结果",
            _readiness_bool(readiness, "has_copywriting"),
        ),
        _acceptance_check(
            "push_texts",
            "推送文案格式",
            _readiness_bool(readiness, "has_push_texts"),
        ),
        _acceptance_check(
            "product_listed",
            "商品在架状态",
            _readiness_bool(readiness, "is_listed"),
        ),
        _acceptance_check(
            "language_supported",
            "广告语言配置",
            _readiness_bool(readiness, "lang_supported"),
        ),
        _acceptance_check(
            "detail_images",
            "产品详情图翻译",
            bool(detail_status.get("ok")),
            required=bool(detail_status.get("required")),
            reason=detail_status.get("reason") or "",
            source_count=int(detail_status.get("source_count") or 0),
            target_count=int(detail_status.get("target_count") or 0),
        ),
        _acceptance_check(
            "shopify_images",
            "链接商品图替换",
            _readiness_bool(readiness, "shopify_image_confirmed"),
            reason=(readiness or {}).get("shopify_image_reason") or "",
        ),
        _acceptance_check(
            "product_links",
            "商品链接探活",
            bool(link_status.get("ok")),
            required=bool(link_status.get("required")),
            reason=link_status.get("reason") or "",
            links=link_status.get("links") or [],
        ),
    ]
    missing = [
        check["key"]
        for check in checks
        if check.get("required") and not check.get("ok")
    ]
    return {
        "ready": not missing,
        "missing": missing,
        "readiness": {
            key: bool(value)
            for key, value in (readiness or {}).items()
            if not str(key).endswith("_reason") and key != "shopify_image_domain_details"
        },
        "checks": checks,
        "country_code": row["country_code"],
        "product_code": product_code,
        "media_item_id": item["id"],
        "media_search_url": _medias_search_url(
            product_code=product_code,
            task_id=task_id,
            product_id=product_id,
            lang=lang,
        ),
    }


def get_child_readiness(task_id: int) -> dict:
    from appcore import pushes

    row = query_one(
        "SELECT t.media_product_id, t.country_code, p.product_code "
        "FROM tasks t JOIN media_products p ON p.id=t.media_product_id "
        "WHERE t.id=%s AND t.parent_task_id IS NOT NULL",
        (int(task_id),),
    )
    if not row:
        raise StateError("child task not found")

    item = _find_target_lang_item(row["media_product_id"], row["country_code"])
    if not item:
        return _child_acceptance_payload(
            task_id=int(task_id),
            row=row,
            item=None,
            product=None,
            readiness={},
        )

    product = _find_product(row["media_product_id"])
    readiness = pushes.compute_readiness(item, product)
    return _child_acceptance_payload(
        task_id=int(task_id),
        row=row,
        item=item,
        product=product,
        readiness=readiness,
    )


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
            "SELECT mi.*, p.name AS product_name, p.product_code AS product_code "
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
            f"SELECT mi.*, p.name AS product_name, p.product_code AS product_code "
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
    payload = _child_acceptance_payload(
        task_id=int(task_id),
        row=row,
        item=item,
        product=product,
        readiness=readiness,
    )
    if not payload["ready"]:
        missing = payload["missing"]
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
                product_id = _task_product_id_for_notification(cur, task_id)
                product_name = (
                    _product_name_for_notification(cur, product_id)
                    if product_id is not None else f"任务 #{int(task_id)}"
                )
                notifications_svc.notify_child_rejected(
                    cur,
                    task_id=task_id,
                    product_name=product_name,
                )
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
