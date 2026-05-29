# appcore/tasks.py
"""任务中心 service 层 — 双层任务模型 + 状态机。

- 父任务（parent_task_id IS NULL）: 素材级，原始视频段
- 子任务（parent_task_id IS NOT NULL）: 国家级，翻译段

完整设计见 docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md。
"""
from __future__ import annotations

import json
from typing import Any, Iterable
from urllib.parse import quote, urlencode

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
CHILD_MANUAL_STEP_CONFIRMED_EVENT = "manual_step_confirmed"
CHILD_MANUAL_STEP_OUTPUT_EVENT = "manual_step_output_submitted"
CHILD_PUSH_REWORK_REJECTED_EVENT = "push_rework_rejected"
CHILD_PUSH_MATERIAL_APPROVED_EVENT = "push_material_approved"
TASK_ARCHIVED_EVENT = "archived"
FINAL_MATERIAL_CONFIRM_LABEL = "最终素材和链接确认"
FINAL_MATERIAL_CONFIRM_HINT = (
    "所有元素确认没问题后勾选，勾选后即表示你确认这个素材可推送了"
)
CHILD_ACCEPTANCE_STEP_LABELS = {
    "localized_media_item": "目标语种素材",
    "translated_video": "视频翻译结果",
    "translated_cover": "封面翻译结果",
    "translated_copywriting": "文案翻译结果",
    "push_texts": "推送文案格式",
    "product_listed": "商品在架状态",
    "detail_images": "产品详情图翻译",
    "shopify_images": "链接商品图替换",
    "product_links": "商品链接探活",
    "language_supported": FINAL_MATERIAL_CONFIRM_LABEL,
}
CHILD_ACCEPTANCE_STEP_KEYS = tuple(CHILD_ACCEPTANCE_STEP_LABELS)
PUSH_REWORK_ISSUE_DEFS = {
    "has_object": {
        "task_check_key": "translated_video",
        "label": "视频",
    },
    "has_cover": {
        "task_check_key": "translated_cover",
        "label": "封面",
    },
    "has_copywriting": {
        "task_check_key": "translated_copywriting",
        "label": "文案",
    },
    "lang_supported": {
        "task_check_key": "language_supported",
        "label": "链接",
    },
    "has_push_texts": {
        "task_check_key": "push_texts",
        "label": "英文文案格式",
    },
    "shopify_image_confirmed": {
        "task_check_key": "shopify_images",
        "label": "图片/链接确认",
    },
}
PUSH_REWORK_ISSUE_KEYS = tuple(PUSH_REWORK_ISSUE_DEFS)
CHILD_ACCEPTANCE_MISSING_ALIASES = {
    "localized_media_item": "lang_item_missing",
}
CHILD_MANUAL_OUTPUT_STEP_KINDS = {
    "localized_media_item": "video",
    "translated_video": "video",
    "translated_cover": "image",
    "translated_copywriting": "text",
    "push_texts": "text",
    "detail_images": "images",
}
RAW_NIUMA_EVENT_TYPES = {
    "raw_niuma_submitted",
    "raw_niuma_done",
    "raw_niuma_failed",
    "raw_niuma_timeout",
}
SUBTITLE_REMOVAL_STATUS_LABELS = {
    "submitted": "已提交",
    "queued": "排队中",
    "running": "运行中",
    "done": "已完成",
    "failed": "失败",
    "timeout": "超时",
}

# ---- 高层状态 rollup ----
def high_level_status(status: str) -> str:
    if status in (PARENT_RAW_DONE, PARENT_ALL_DONE, CHILD_DONE):
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


def get_existing_task_languages_for_item(media_item_id: int) -> list[str]:
    """获取该素材已创建的、会阻止同语种重建的子任务语言列表"""
    rows = query_all(
        "SELECT child.country_code, child.status AS child_status, "
        "parent.status AS parent_status "
        "FROM tasks child "
        "LEFT JOIN tasks parent ON parent.id = child.parent_task_id "
        "WHERE child.media_item_id=%s AND child.parent_task_id IS NOT NULL "
        "ORDER BY child.id",
        (int(media_item_id),),
    )
    languages: list[str] = []
    seen: set[str] = set()
    for row in rows:
        code = str(row.get("country_code") or "").strip().upper()
        if not code or code in seen:
            continue
        child_status = str(row.get("child_status") or "").strip()
        parent_status = str(row.get("parent_status") or "").strip()
        if child_status == CHILD_CANCELLED:
            continue
        if child_status != CHILD_DONE and parent_status == PARENT_CANCELLED:
            continue
        seen.add(code)
        languages.append(code)
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


def _projects_recent_order_clause() -> str:
    row = query_one(
        "SELECT 1 AS ok FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'projects' "
        "AND COLUMN_NAME = 'updated_at'"
    )
    if row:
        return "ORDER BY updated_at DESC, created_at DESC"
    return "ORDER BY created_at DESC"


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


def infer_single_child_task_id_for_media_item(
    product_id: int,
    lang: str,
    *,
    assignee_id: int | None = None,
) -> int | None:
    product_id_int = _positive_int(product_id)
    lang_norm = str(lang or "").strip().lower()
    if not product_id_int or not lang_norm:
        return None
    args: list[Any] = [product_id_int, lang_norm]
    assignee_id_int = _positive_int(assignee_id)
    assignee_filter = ""
    if assignee_id_int is not None:
        assignee_filter = "AND assignee_id=%s "
        args.append(assignee_id_int)
    args.extend([CHILD_ASSIGNED, CHILD_REVIEW, CHILD_DONE])
    rows = query_all(
        "SELECT id FROM tasks "
        "WHERE media_product_id=%s "
        "AND LOWER(TRIM(COALESCE(country_code, '')))=%s "
        "AND parent_task_id IS NOT NULL "
        f"{assignee_filter}"
        "AND status IN (%s,%s,%s) "
        "ORDER BY id DESC",
        tuple(args),
    )
    task_ids = []
    for row in rows or []:
        task_id = _positive_int((row or {}).get("id"))
        if task_id is not None and task_id not in task_ids:
            task_ids.append(task_id)
    return task_ids[0] if len(task_ids) == 1 else None


def infer_single_child_task_id_from_raw_source(
    product_id: int,
    lang: str,
    source_raw_id: int,
) -> int | None:
    product_id_int = _positive_int(product_id)
    raw_source_id_int = _positive_int(source_raw_id)
    lang_norm = str(lang or "").strip().lower()
    if not product_id_int or not lang_norm or raw_source_id_int is None:
        return None

    rows = query_all(
        "SELECT c.id, e.payload_json FROM tasks c "
        "JOIN tasks p ON p.id=c.parent_task_id "
        "JOIN task_events e ON e.task_id=p.id AND e.event_type='raw_source_reused' "
        "WHERE c.media_product_id=%s "
        "AND LOWER(TRIM(COALESCE(c.country_code, '')))=%s "
        "AND c.parent_task_id IS NOT NULL "
        "AND c.status IN (%s,%s,%s) "
        "ORDER BY c.id DESC",
        (
            product_id_int,
            lang_norm,
            CHILD_ASSIGNED,
            CHILD_REVIEW,
            CHILD_DONE,
        ),
    )
    task_ids: list[int] = []
    for row in rows or []:
        payload = _parse_event_payload_obj((row or {}).get("payload_json"))
        if _positive_int(payload.get("raw_source_id")) != raw_source_id_int:
            continue
        task_id = _positive_int((row or {}).get("id"))
        if task_id is not None and task_id not in task_ids:
            task_ids.append(task_id)
    return task_ids[0] if len(task_ids) == 1 else None


def latest_child_task_id_for_media_item(product_id: int, lang: str) -> int | None:
    product_id_int = _positive_int(product_id)
    lang_norm = str(lang or "").strip().lower()
    if not product_id_int or not lang_norm:
        return None

    row = query_one(
        "SELECT id FROM tasks "
        "WHERE media_product_id=%s "
        "AND LOWER(TRIM(COALESCE(country_code, '')))=%s "
        "AND parent_task_id IS NOT NULL "
        "AND status IN (%s,%s,%s) "
        "ORDER BY id DESC LIMIT 1",
        (
            product_id_int,
            lang_norm,
            CHILD_ASSIGNED,
            CHILD_REVIEW,
            CHILD_DONE,
        ),
    )
    return _positive_int((row or {}).get("id"))


def resolve_child_task_for_media_item_upload(
    *,
    task_id: int,
    product_id: int,
    lang: str,
    actor_user_id: int,
    is_admin: bool = False,
) -> int:
    task_id_int = _positive_int(task_id)
    product_id_int = _positive_int(product_id)
    lang_norm = str(lang or "").strip().lower()
    if not task_id_int:
        raise ValueError("task_id invalid")
    if not product_id_int:
        raise ValueError("product_id invalid")
    if not lang_norm:
        raise ValueError("lang invalid")

    row = query_one(
        "SELECT id, assignee_id, status, media_product_id, country_code "
        "FROM tasks WHERE id=%s AND parent_task_id IS NOT NULL",
        (task_id_int,),
    )
    if not row:
        raise StateError("child task not found")
    if int(row.get("media_product_id") or 0) != product_id_int:
        raise StateError("child task product mismatch")
    task_lang = str(row.get("country_code") or "").strip().lower()
    if task_lang != lang_norm:
        raise StateError("child task language mismatch")
    if row.get("status") not in (CHILD_ASSIGNED, CHILD_REVIEW, CHILD_DONE):
        raise StateError("child task not accepting output")
    if not is_admin and int(row.get("assignee_id") or 0) != int(actor_user_id):
        raise PermissionError("forbidden")
    return task_id_int


def _payload_user_ids(payload: dict) -> set[int]:
    ids: set[int] = set()
    for key in ("translator_id", "assignee_id", "old", "new"):
        user_id = _positive_int(payload.get(key))
        if user_id is not None:
            ids.add(user_id)
    assignments = payload.get("language_assignments")
    if isinstance(assignments, dict):
        for value in assignments.values():
            user_id = _positive_int(value)
            if user_id is not None:
                ids.add(user_id)
    return ids


def _event_subtitle_removal_task_id(event_type: str, payload: dict) -> str:
    if event_type not in RAW_NIUMA_EVENT_TYPES:
        return ""
    raw = payload.get("subtitle_task_id") or payload.get("task_id") or ""
    return str(raw or "").strip()


def _isoformat_value(value: Any) -> str:
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _subtitle_removal_detail_url(task_id: str) -> str:
    return f"/subtitle-removal/{quote(str(task_id), safe='')}"


def _subtitle_removal_source_video_url(task_id: str) -> str:
    return f"/api/subtitle-removal/{quote(str(task_id), safe='')}/artifact/source-video"


def _subtitle_removal_result_video_url(task_id: str) -> str:
    return f"/api/subtitle-removal/{quote(str(task_id), safe='')}/artifact/result"


def _parse_subtitle_removal_state(row: dict) -> dict:
    raw = row.get("state_json")
    if isinstance(raw, dict):
        state = dict(raw)
    else:
        try:
            state = json.loads(raw or "{}")
        except (TypeError, ValueError):
            state = {}
    if not isinstance(state, dict):
        state = {}
    state.setdefault("id", row.get("id"))
    state.setdefault("status", row.get("status"))
    state["_project_created_at"] = row.get("created_at")
    state["_project_updated_at"] = row.get("updated_at")
    return state


def _load_subtitle_removal_context(task_ids: Iterable[str]) -> dict[str, dict]:
    normalized_ids = sorted({str(task_id).strip() for task_id in task_ids if str(task_id).strip()})
    if not normalized_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(normalized_ids))
    rows = query_all(
        "SELECT id, status, state_json, created_at "
        "FROM projects "
        "WHERE type='subtitle_removal' AND deleted_at IS NULL "
        f"AND id IN ({placeholders})",
        tuple(normalized_ids),
    )
    return {str(row["id"]): _parse_subtitle_removal_state(row) for row in rows}


def _subtitle_removal_summary_status(event_type: str, payload: dict, state: dict) -> str:
    if event_type == "raw_niuma_timeout":
        return "timeout"
    if event_type == "raw_niuma_failed":
        return "failed"
    if event_type == "raw_niuma_done":
        return "done"
    status = str(state.get("status") or "").strip().lower()
    provider_status = str(state.get("provider_status") or "").strip().lower()
    if status == "done" or provider_status in {"done", "success"}:
        return "done"
    if status == "error" or provider_status in {"error", "failed", "fail"}:
        return "failed"
    if status == "queued" or provider_status in {"queued", "waiting"}:
        return "queued"
    if status == "running" or provider_status in {"running", "processing", "polling"}:
        return "running"
    if payload.get("error"):
        return "failed"
    return "submitted"


def _subtitle_removal_error(payload: dict, state: dict) -> str:
    for key in ("error", "provider_emsg", "message"):
        value = payload.get(key)
        if value:
            return str(value)
    for key in ("error", "provider_emsg"):
        value = state.get(key)
        if value:
            return str(value)
    return ""


def _subtitle_removal_comparison(task_id: str, state: dict) -> dict:
    has_source = bool(str(state.get("video_path") or "").strip())
    has_result = any(
        str(state.get(key) or "").strip()
        for key in ("result_video_path", "result_tos_key", "vod_result_vid", "provider_result_url")
    )
    if not (has_source and has_result):
        return {}
    return {
        "source_video_url": _subtitle_removal_source_video_url(task_id),
        "result_video_url": _subtitle_removal_result_video_url(task_id),
        "source_label": "提交去字幕源视频",
        "source_hint": "原始带字幕英文视频",
        "result_label": "去字幕输出结果视频",
        "result_hint": "原始视频素材审核结果",
    }


def _event_subtitle_removal_context(
    *,
    task_id: str,
    event_type: str,
    submitted_at: str,
    payload: dict,
    state: dict | None = None,
) -> dict:
    state = state or {}
    status = _subtitle_removal_summary_status(event_type, payload, state)
    last_updated_at = (
        str(state.get("last_polled_at") or "").strip()
        or _isoformat_value(state.get("_project_updated_at"))
        or _isoformat_value(state.get("updated_at"))
    )
    context = {
        "task_id": task_id,
        "detail_url": _subtitle_removal_detail_url(task_id),
        "summary_status": status,
        "summary_label": SUBTITLE_REMOVAL_STATUS_LABELS.get(status, status),
        "submitted_at": submitted_at or "",
        "last_updated_at": last_updated_at,
        "error": _subtitle_removal_error(payload, state),
    }
    comparison = _subtitle_removal_comparison(task_id, state)
    if comparison:
        context["comparison"] = comparison
    return context


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
    payload_subtitle_task_ids: set[str] = set()
    raw_source_ids: set[int] = set()
    for row in rows:
        payload = _parse_event_payload_obj(row.get("payload_json"))
        payload_by_event_id[int(row["id"])] = payload
        payload_user_ids.update(_payload_user_ids(payload))
        subtitle_task_id = _event_subtitle_removal_task_id(
            str(row.get("event_type") or ""),
            payload,
        )
        if subtitle_task_id:
            payload_subtitle_task_ids.add(subtitle_task_id)
        if row.get("event_type") == "raw_source_reused":
            rs_id = payload.get("raw_source_id")
            if rs_id:
                raw_source_ids.add(int(rs_id))

    user_context = _load_user_display_context(payload_user_ids)
    subtitle_context = _load_subtitle_removal_context(payload_subtitle_task_ids)

    raw_source_filenames: dict[int, str] = {}
    if raw_source_ids:
        placeholders = ", ".join(["%s"] * len(raw_source_ids))
        rs_rows = query_all(
            f"SELECT id, display_name FROM media_raw_sources WHERE id IN ({placeholders})",
            tuple(raw_source_ids),
        )
        for rs_row in rs_rows:
            raw_source_filenames[int(rs_row["id"])] = rs_row.get("display_name") or ""

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
        context = {}
        if event_user_context:
            context["users"] = event_user_context
        subtitle_task_id = _event_subtitle_removal_task_id(
            str(row.get("event_type") or ""),
            payload,
        )
        if subtitle_task_id:
            context["subtitle_removal"] = _event_subtitle_removal_context(
                task_id=subtitle_task_id,
                event_type=str(row.get("event_type") or ""),
                submitted_at=item["created_at"] or "",
                payload=payload,
                state=subtitle_context.get(subtitle_task_id, {}),
            )
        if row.get("event_type") == "raw_source_reused":
            rs_id = payload.get("raw_source_id")
            if rs_id and int(rs_id) in raw_source_filenames:
                context["raw_source_filename"] = raw_source_filenames[int(rs_id)]
                context["raw_source_video_url"] = f"/medias/raw-sources/{int(rs_id)}/video"
        if context:
            item["payload_context"] = context
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
    bucket: str = "",
    task_id: int | None = None,
    task_type: str = "",
    assignee_id: int | None = None,
    parent_only: bool = False,
    task_status: str = "",
    archived: bool | None = False,
    urgency: str = "",
) -> dict:
    page_size = max(1, int(page_size))
    requested_page = max(1, int(page))
    where = ["1=1"]
    args: list = []

    if tab == "mine":
        where.append("t.assignee_id=%s")
        args.append(int(user_id))
    elif tab != "all":
        raise ValueError("invalid tab")
    if parent_only:
        where.append("t.parent_task_id IS NULL")
    if task_type == "raw":
        where.append("t.parent_task_id IS NULL")
    elif task_type == "translate":
        where.append("t.parent_task_id IS NOT NULL")
    elif task_type:
        raise ValueError("invalid task_type")
    if assignee_id:
        where.append("t.assignee_id=%s")
        args.append(int(assignee_id))
    if archived is True:
        where.append("t.archived_at IS NOT NULL")
    elif archived is False:
        where.append("t.archived_at IS NULL")
    if urgency == "urgent":
        where.append("t.is_urgent=1")
    elif urgency == "normal":
        where.append("t.is_urgent=0")
    elif urgency:
        raise ValueError("invalid urgency")

    if task_id:
        where.append("t.id=%s")
        args.append(int(task_id))
    if keyword:
        like = f"%{keyword}%"
        where.append("(p.name LIKE %s OR p.product_code LIKE %s)")
        args.extend([like, like])
    if high_status == "in_progress":
        where.append("t.status NOT IN (%s, %s, %s, %s)")
        args.extend([PARENT_RAW_DONE, PARENT_ALL_DONE, CHILD_DONE, PARENT_CANCELLED])
    elif high_status == "completed":
        where.append("t.status IN (%s, %s, %s)")
        args.extend([PARENT_RAW_DONE, PARENT_ALL_DONE, CHILD_DONE])
    elif high_status == "terminated":
        where.append("t.status=%s")
        args.append(PARENT_CANCELLED)
    if bucket == "todo":
        where.append("t.status IN (%s, %s)")
        args.extend([PARENT_RAW_IN_PROGRESS, CHILD_ASSIGNED])
    elif bucket == "review":
        where.append("t.status IN (%s, %s)")
        args.extend([PARENT_RAW_REVIEW, CHILD_REVIEW])
    elif bucket == "blocked":
        where.append("t.status = %s")
        args.append(CHILD_BLOCKED)
    elif bucket == "done":
        where.append("t.status IN (%s, %s, %s)")
        args.extend([PARENT_RAW_DONE, PARENT_ALL_DONE, CHILD_DONE])
    elif bucket:
        raise ValueError("invalid bucket")

    if task_status == "todo":
        where.append("t.status IN (%s, %s, %s)")
        args.extend([PARENT_PENDING, PARENT_RAW_IN_PROGRESS, CHILD_ASSIGNED])
    elif task_status == "review":
        where.append("t.status IN (%s, %s)")
        args.extend([PARENT_RAW_REVIEW, CHILD_REVIEW])
    elif task_status == "blocked":
        where.append("t.status = %s")
        args.append(CHILD_BLOCKED)
    elif task_status == "done":
        where.append("t.status IN (%s, %s, %s)")
        args.extend([PARENT_RAW_DONE, PARENT_ALL_DONE, CHILD_DONE])
    elif task_status == "cancelled":
        where.append("t.status IN (%s, %s)")
        args.extend([PARENT_CANCELLED, CHILD_CANCELLED])
    elif task_status and task_status != "all":
        raise ValueError("invalid task_status")

    where_sql = " AND ".join(where)
    count_sql = (
        "SELECT COUNT(*) AS total "
        "FROM tasks t "
        "JOIN media_products p ON p.id=t.media_product_id "
        f"WHERE {where_sql}"
    )
    count_args = tuple(args)
    total_row = query_one(count_sql, count_args) or {}
    total = int(total_row.get("total") or 0)

    # Calculate base tasks count (only tab & archived filter applied)
    base_where = ["1=1"]
    base_args = []
    if tab == "mine":
        base_where.append("t.assignee_id=%s")
        base_args.append(int(user_id))
    if archived is True:
        base_where.append("t.archived_at IS NOT NULL")
    elif archived is False:
        base_where.append("t.archived_at IS NULL")

    base_where_sql = " AND ".join(base_where)
    base_count_sql = (
        "SELECT COUNT(*) AS total_all "
        "FROM tasks t "
        "JOIN media_products p ON p.id=t.media_product_id "
        f"WHERE {base_where_sql}"
    )
    base_total_row = query_one(base_count_sql, tuple(base_args)) or {}
    total_all = int(base_total_row.get("total_all") or 0)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(requested_page, total_pages)
    offset = (page - 1) * page_size

    assignee_name_expr = _user_display_name_expr("u")
    sql = (
        "SELECT t.*, p.name AS product_name, p.product_code AS product_code, "
        "       source_mi.filename AS source_media_filename, "
        "       (SELECT GROUP_CONCAT(c.country_code ORDER BY c.country_code SEPARATOR ',') "
        "        FROM tasks c WHERE c.parent_task_id = t.id) AS child_country_codes, "
        f"       u.username AS assignee_username, {assignee_name_expr} AS assignee_display_name "
        "FROM tasks t "
        "JOIN media_products p ON p.id=t.media_product_id "
        "LEFT JOIN media_items source_mi ON source_mi.id=t.media_item_id "
        "LEFT JOIN users u ON u.id=t.assignee_id "
        f"WHERE {where_sql} "
        "ORDER BY t.is_urgent DESC, t.created_at DESC, t.id DESC "
        "LIMIT %s OFFSET %s"
    )
    rows = query_all(sql, (*count_args, page_size, offset))
    return {
        "items": [
            {
                "id": row["id"],
                "parent_task_id": row["parent_task_id"],
                "media_product_id": row["media_product_id"],
                "media_item_id": row["media_item_id"],
                "product_name": row["product_name"],
                "product_code": row.get("product_code"),
                "source_media_filename": row.get("source_media_filename"),
                "child_country_codes": row.get("child_country_codes") or "",
                "country_code": row["country_code"],
                "assignee_id": row["assignee_id"],
                "assignee_username": row["assignee_username"],
                "assignee_display_name": (
                    row.get("assignee_display_name") or row["assignee_username"]
                ),
                "status": row["status"],
                "is_urgent": bool(row.get("is_urgent")),
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
                "archived_at": (
                    row["archived_at"].isoformat()
                    if row.get("archived_at")
                    else None
                ),
                "archived_by": row.get("archived_by"),
                "last_reason": row["last_reason"],
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_all": total_all,
        "total_pages": total_pages,
    }


def archive_task(*, task_id: int, actor_user_id: int, is_admin: bool) -> bool:
    """Hide a task from active task-center lists."""
    if not is_admin:
        raise PermissionError("only admin can archive tasks")
    row = query_one(
        "SELECT id, status, archived_at FROM tasks WHERE id=%s",
        (int(task_id),),
    )
    if not row:
        raise StateError("task not found")
    if row.get("archived_at"):
        return False

    affected = execute(
        "UPDATE tasks SET archived_at=NOW(), archived_by=%s, updated_at=NOW() "
        "WHERE id=%s AND archived_at IS NULL",
        (int(actor_user_id), int(task_id)),
    )
    if not affected:
        return False
    execute(
        "INSERT INTO task_events (task_id, event_type, actor_user_id, payload_json) "
        "VALUES (%s, %s, %s, %s)",
        (int(task_id), TASK_ARCHIVED_EVENT, int(actor_user_id), None),
    )
    return True


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


def update_task_assignee(
    *,
    task_id: int,
    assignee_id: int,
    actor_user_id: int,
    is_admin: bool,
) -> None:
    """超级管理员/管理员修改任务负责人"""
    if not is_admin:
        raise PermissionError("仅管理员可修改负责人")

    row = _row(task_id)
    if not row:
        raise StateError("任务不存在")

    current_status = row.get("status")
    is_parent = row.get("parent_task_id") is None

    # 校验终态
    if is_parent:
        if current_status in PARENT_TERMINAL:
            raise StateError(f"父任务当前处于终态 '{current_status}'，无法修改负责人")
    else:
        if current_status in CHILD_TERMINAL:
            raise StateError(f"子任务当前处于终态 '{current_status}'，无法修改负责人")

    # 校验负责人 ID 是否在翻译或任务工作人员范围内
    from appcore.users import ensure_translation_work_user
    try:
        ensure_translation_work_user(assignee_id)
    except ValueError as exc:
        raise ValueError(f"指派失败: {exc}")

    old_assignee_id = row.get("assignee_id")
    if old_assignee_id == assignee_id:
        return

    # 更新负责人
    execute(
        "UPDATE tasks SET assignee_id=%s, updated_at=NOW() WHERE id=%s",
        (int(assignee_id), int(task_id)),
    )

    # 记录 assignee_changed 审计事件到 task_events
    payload = {
        "old_assignee_id": old_assignee_id,
        "new_assignee_id": int(assignee_id),
    }
    execute(
        "INSERT INTO task_events (task_id, event_type, actor_user_id, payload_json) "
        "VALUES (%s, 'assignee_changed', %s, %s)",
        (
            int(task_id),
            int(actor_user_id),
            json.dumps(payload, ensure_ascii=False),
        ),
    )


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


def record_push_material_approved(
    *,
    task_id: int,
    actor_user_id: int | None,
    item_id: int,
    product_code: str | None = "",
    lang: str | None = "",
    upstream_status: int | None = None,
) -> dict:
    payload = {
        "source": "push_management",
        "item_id": int(item_id),
        "product_code": str(product_code or "").strip(),
        "lang": str(lang or "").strip().lower(),
        "upstream_status": upstream_status,
    }
    execute(
        "INSERT INTO task_events (task_id, event_type, actor_user_id, payload_json) "
        "VALUES (%s, %s, %s, %s)",
        (
            int(task_id),
            CHILD_PUSH_MATERIAL_APPROVED_EVENT,
            int(actor_user_id) if actor_user_id is not None else None,
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    return {
        "task_id": int(task_id),
        "event_type": CHILD_PUSH_MATERIAL_APPROVED_EVENT,
        "payload": payload,
    }


def create_parent_task(
    *,
    media_product_id: int,
    media_item_id: int | None,
    countries: list[str],
    translator_id: int | None = None,
    language_assignments: dict[str, int] | None = None,
    raw_processor_id: int | None = None,
    reused_raw_source_id: int | None = None,
    created_by: int,
    force: bool = False,
    is_urgent: bool = False,
) -> int:
    """创建父任务 + 一并物化子任务 (status=blocked)。返回父任务 id。"""
    if not countries:
        raise ValueError("countries must be non-empty")
    norm_countries = [c.strip().upper() for c in countries if c and c.strip()]
    if not norm_countries:
        raise ValueError("countries must be non-empty after normalization")

    if media_item_id is not None and not force:
        existing_langs = get_existing_task_languages_for_item(media_item_id)
        duplicates = [c for c in norm_countries if c in existing_langs]
        if duplicates:
            raise ValueError(f"以下语言已存在活跃任务，不能重复创建: {', '.join(duplicates)}")

    assignment_map = _normalize_language_assignments(
        countries=norm_countries,
        translator_id=translator_id,
        language_assignments=language_assignments,
    )
    reuse_raw_source_id = _positive_int(reused_raw_source_id)
    urgent_value = 1 if is_urgent else 0

    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                if reuse_raw_source_id is not None:
                    cur.execute(
                        "INSERT INTO tasks "
                        "(parent_task_id, media_product_id, media_item_id, assignee_id, status, is_urgent, claimed_at, completed_at, created_by) "
                        "VALUES (NULL, %s, %s, %s, %s, %s, NOW(), NOW(), %s)",
                        (
                            int(media_product_id),
                            int(media_item_id) if media_item_id is not None else None,
                            int(raw_processor_id) if raw_processor_id is not None else None,
                            PARENT_ALL_DONE,
                            urgent_value,
                            int(created_by),
                        ),
                    )
                elif raw_processor_id is not None:
                    cur.execute(
                        "INSERT INTO tasks "
                        "(parent_task_id, media_product_id, media_item_id, assignee_id, status, is_urgent, claimed_at, created_by) "
                        "VALUES (NULL, %s, %s, %s, %s, %s, NOW(), %s)",
                        (
                            int(media_product_id),
                            int(media_item_id) if media_item_id is not None else None,
                            int(raw_processor_id),
                            PARENT_RAW_IN_PROGRESS,
                            urgent_value,
                            int(created_by),
                        ),
                    )
                else:
                    cur.execute(
                        "INSERT INTO tasks "
                        "(parent_task_id, media_product_id, media_item_id, status, is_urgent, created_by) "
                        "VALUES (NULL, %s, %s, %s, %s, %s)",
                        (
                            int(media_product_id),
                            int(media_item_id) if media_item_id is not None else None,
                            PARENT_PENDING,
                            urgent_value,
                            int(created_by),
                        ),
                    )
                parent_id = cur.lastrowid
                created_payload = {
                    "countries": norm_countries,
                    "is_urgent": bool(is_urgent),
                }
                if translator_id is not None:
                    created_payload["translator_id"] = int(translator_id)
                if language_assignments:
                    created_payload["language_assignments"] = assignment_map
                if raw_processor_id is not None:
                    created_payload["raw_processor_id"] = int(raw_processor_id)
                if reuse_raw_source_id is not None:
                    created_payload["raw_source_id"] = reuse_raw_source_id
                    created_payload["raw_processing_skipped"] = True
                _write_event(cur, parent_id, "created", created_by, created_payload)
                product_name = _product_name_for_notification(cur, int(media_product_id))
                if reuse_raw_source_id is not None:
                    _write_event(
                        cur,
                        parent_id,
                        "raw_source_reused",
                        created_by,
                        {
                            "raw_source_id": reuse_raw_source_id,
                            "media_item_id": int(media_item_id) if media_item_id is not None else None,
                        },
                    )
                elif raw_processor_id is not None:
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
                child_status = CHILD_ASSIGNED if reuse_raw_source_id is not None else CHILD_BLOCKED
                for country in norm_countries:
                    child_assignee_id = assignment_map[country]
                    cur.execute(
                        "INSERT INTO tasks "
                        "(parent_task_id, media_product_id, media_item_id, "
                        " country_code, assignee_id, status, is_urgent, created_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (parent_id, int(media_product_id),
                         int(media_item_id) if media_item_id is not None else None,
                         country, child_assignee_id, child_status, urgent_value, int(created_by)),
                    )
                    child_id = cur.lastrowid
                    _write_event(cur, child_id, "created", created_by,
                                 {"country": country, "is_urgent": bool(is_urgent)})
                    if reuse_raw_source_id is not None:
                        notifications_svc.notify_child_assigned(
                            cur,
                            task_id=child_id,
                            product_name=product_name,
                        )
                    else:
                        notifications_svc.notify_child_blocked(
                            cur,
                            task_id=child_id,
                            assignee_id=child_assignee_id,
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


class ConflictError(RuntimeError):
    """Optimistic concurrency violation, e.g., already claimed."""


class StateError(RuntimeError):
    """Invalid state transition / precondition violation."""


def set_task_urgency(
    *,
    task_id: int,
    actor_user_id: int,
    is_urgent: bool,
) -> dict:
    """Set an independent urgent flag for one task and audit actual changes."""
    target = bool(is_urgent)
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, is_urgent FROM tasks WHERE id=%s FOR UPDATE",
                    (int(task_id),),
                )
                row = cur.fetchone()
                if not row:
                    raise StateError("task not found")
                previous = bool(row.get("is_urgent"))
                if previous == target:
                    conn.commit()
                    return {
                        "changed": False,
                        "is_urgent": target,
                        "previous_is_urgent": previous,
                    }
                cur.execute(
                    "UPDATE tasks SET is_urgent=%s, updated_at=NOW() WHERE id=%s",
                    (1 if target else 0, int(task_id)),
                )
                _write_event(
                    cur,
                    int(task_id),
                    "urgent_marked",
                    int(actor_user_id),
                    {
                        "is_urgent": target,
                        "previous_is_urgent": previous,
                    },
                )
            conn.commit()
            return {
                "changed": True,
                "is_urgent": target,
                "previous_is_urgent": previous,
            }
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def _normalize_language_assignments(
    *,
    countries: list[str],
    translator_id: int | None,
    language_assignments: dict[str, int] | None,
) -> dict[str, int]:
    if language_assignments:
        normalized = {
            str(country or "").strip().upper(): int(assignee_id)
            for country, assignee_id in language_assignments.items()
            if str(country or "").strip()
        }
        missing = [country for country in countries if country not in normalized]
        extras = [country for country in normalized if country not in countries]
        if missing or extras:
            raise ValueError(
                "language_assignments must cover exactly the requested countries"
            )
        return normalized
    if translator_id is None:
        raise ValueError("translator_id or language_assignments required")
    assignee_id = int(translator_id)
    return {country: assignee_id for country in countries}


def mark_uploaded(*, task_id: int, actor_user_id: int) -> None:
    """处理人标"已上传"，转入待人工审核。"""
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


def _ensure_parent_raw_approval_allowed(
    row: dict,
    *,
    actor_user_id: int,
    is_admin: bool,
) -> None:
    if is_admin:
        return
    if int(row.get("assignee_id") or 0) == int(actor_user_id):
        return
    raise PermissionError("only assignee or admin can approve")


def approve_raw(*, task_id: int, actor_user_id: int, is_admin: bool = False) -> None:
    """人工确认原视频可用后，写入原始素材库并解锁子任务。"""
    row = query_one(
        "SELECT id, status, assignee_id FROM tasks "
        "WHERE id=%s AND parent_task_id IS NULL",
        (int(task_id),),
    )
    if not row:
        raise StateError("parent task not found")
    _ensure_parent_raw_approval_allowed(
        row,
        actor_user_id=actor_user_id,
        is_admin=is_admin,
    )
    if row.get("status") != PARENT_RAW_REVIEW:
        raise StateError("parent not in raw_review")
    result = complete_raw_parent_if_ready(
        task_id=task_id,
        actor_user_id=actor_user_id,
        approved_actor_user_id=actor_user_id,
    )
    if not result.get("completed"):
        raise StateError("raw source not ready")


def complete_raw_parent_if_ready(
    *,
    task_id: int,
    actor_user_id: int | None = None,
    approved_actor_user_id: int | None = None,
) -> dict:
    """Complete a reviewed raw-processing parent task once its raw source exists."""
    from appcore import task_raw_source_bridge

    try:
        raw_result = task_raw_source_bridge.ensure_raw_source_for_parent_task(
            task_id=task_id,
            actor_user_id=actor_user_id,
        )
    except task_raw_source_bridge.RawSourceBridgeError:
        task_row = query_one(
            "SELECT media_item_id FROM tasks WHERE id=%s AND parent_task_id IS NULL",
            (int(task_id),),
        ) or {}
        media_item_id = _positive_int(task_row.get("media_item_id"))
        existing = (
            task_raw_source_bridge.find_ready_raw_source_for_media_item(media_item_id)
            if media_item_id else None
        )
        if not existing:
            raise
        raw_result = {
            "raw_source_id": int(existing["id"]),
            "created": False,
            "updated": False,
        }
    raw_source_id = raw_result.get("raw_source_id")
    raw_event_type = (
        "raw_source_created" if raw_result.get("created")
        else "raw_source_updated" if raw_result.get("updated")
        else "raw_source_ready"
    )

    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=NULL, completed_at=COALESCE(completed_at, NOW()), updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NULL AND status=%s",
                    (
                        PARENT_ALL_DONE,
                        int(task_id),
                        PARENT_RAW_REVIEW,
                    ),
                )
                if cur.rowcount == 0:
                    raise StateError("parent not in raw_review")
                if approved_actor_user_id is not None:
                    _write_event(cur, task_id, "approved", approved_actor_user_id, None)
                _write_event(
                    cur,
                    task_id,
                    raw_event_type,
                    actor_user_id,
                    {"raw_source_id": raw_source_id},
                )
                _write_event(
                    cur,
                    task_id,
                    "auto_completed",
                    actor_user_id,
                    {"reason": "raw_source_ready", "raw_source_id": raw_source_id},
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
    return {"completed": True, "raw_source_id": raw_source_id}


def recover_pending_manual_raw_results(*, limit: int = 5) -> dict:
    limit_int = _positive_int(limit) or 5
    limit_int = max(1, min(limit_int, 50))
    rows = query_all(
        """
        SELECT t.id AS task_id, te.actor_user_id, te.id AS manual_event_id
        FROM tasks t
        JOIN (
            SELECT task_id, MAX(id) AS manual_event_id
            FROM task_events
            WHERE event_type='raw_manual_uploaded'
            GROUP BY task_id
        ) latest ON latest.task_id=t.id
        JOIN task_events te ON te.id=latest.manual_event_id
        LEFT JOIN task_events done
          ON done.task_id=t.id
         AND done.id > latest.manual_event_id
         AND done.event_type IN (
             'approved',
             'raw_source_created',
             'raw_source_updated',
             'raw_source_ready',
             'auto_completed'
         )
        WHERE t.parent_task_id IS NULL
          AND t.status=%s
          AND done.id IS NULL
        ORDER BY latest.manual_event_id ASC
        LIMIT %s
        """,
        (PARENT_RAW_REVIEW, limit_int),
    )
    result = {
        "scanned": len(rows),
        "completed": 0,
        "failed": 0,
        "task_ids": [],
        "errors": [],
    }
    for row in rows:
        task_id = _positive_int(row.get("task_id"))
        actor_user_id = _positive_int(row.get("actor_user_id"))
        if not task_id or not actor_user_id:
            result["failed"] += 1
            result["errors"].append({
                "task_id": task_id,
                "error": "invalid pending manual raw result row",
            })
            continue
        try:
            completed = complete_raw_parent_if_ready(
                task_id=task_id,
                actor_user_id=actor_user_id,
                approved_actor_user_id=actor_user_id,
            )
        except Exception as exc:
            result["failed"] += 1
            result["errors"].append({"task_id": task_id, "error": str(exc)})
            continue
        if completed.get("completed"):
            result["completed"] += 1
            result["task_ids"].append(task_id)
    return result


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
    extra: dict[str, Any] | None = None,
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
    for key, value in (extra or {}).items():
        if value is None or value == "":
            continue
        params.append((str(key), str(value)))
    return "/medias/?" + urlencode(params)


def _action(
    label: str,
    url: str,
    kind: str,
    *,
    primary: bool = False,
    disabled_reason: str = "",
) -> dict:
    payload = {"label": label, "url": url, "kind": kind}
    if primary:
        payload["primary"] = True
    if disabled_reason:
        payload["disabled_reason"] = disabled_reason
    return payload


def _review_media_object_url(object_key: str | None) -> str:
    key = str(object_key or "").strip()
    if not key:
        return ""
    return "/medias/object?object_key=" + quote(key, safe="")


def _review_object_filename(object_key: str | None) -> str:
    key = str(object_key or "").strip()
    return key.rsplit("/", 1)[-1] if key else ""


def _review_video_asset(
    item: dict | None,
    *,
    label: str,
) -> dict | None:
    if not item:
        return None
    url = _review_media_object_url(item.get("object_key"))
    if not url:
        return None
    filename = (
        str(item.get("filename") or "").strip()
        or _review_object_filename(item.get("object_key"))
    )
    asset = {
        "type": "video",
        "label": label,
        "url": url,
        "filename": filename,
        "display_name": item.get("display_name") or filename,
        "file_size": item.get("file_size"),
        "lang": str(item.get("lang") or "").strip().lower(),
        "media_item_id": item.get("id"),
    }
    item_id = _positive_int(item.get("id"))
    if item_id and item.get("cover_object_key"):
        asset["poster_url"] = f"/medias/item-cover/{item_id}"
    asset["display_shape"] = "portrait_9_16"
    return asset


def _review_item_cover_asset(
    item: dict | None,
    *,
    label: str = "封面",
) -> dict | None:
    if not item or not item.get("cover_object_key"):
        return None
    filename = _review_object_filename(item.get("cover_object_key"))
    return {
        "type": "image",
        "label": label,
        "url": f"/medias/item-cover/{int(item['id'])}",
        "display_shape": "portrait_9_16",
        "filename": filename,
        "display_name": filename or "封面",
        "file_size": None,
        "lang": str(item.get("lang") or "").strip().lower(),
        "media_item_id": item.get("id"),
    }


def _review_detail_image_asset(row: dict, index: int) -> dict:
    filename = _review_object_filename(row.get("object_key"))
    return {
        "type": "image",
        "label": f"详情图 {int(index)}",
        "url": f"/medias/detail-image/{int(row['id'])}",
        "filename": filename,
        "display_name": filename or f"详情图 {int(index)}",
        "file_size": row.get("file_size"),
        "width": row.get("width"),
        "height": row.get("height"),
        "detail_image_id": row.get("id"),
    }


def _evidence_link(
    *,
    label: str,
    url: str,
    meta: str = "",
    ok: bool | None = None,
) -> dict:
    payload = {
        "type": "link",
        "label": label,
        "url": url,
    }
    if meta:
        payload["meta"] = meta
    if ok is not None:
        payload["ok"] = bool(ok)
    return payload


def _evidence_status(*, label: str, meta: str, ok: bool | None = None) -> dict:
    payload = {
        "type": "status",
        "label": label,
        "meta": meta,
    }
    if ok is not None:
        payload["ok"] = bool(ok)
    return payload


def _evidence_text_value(value: Any) -> str:
    return str(value or "").strip()


def _copywriting_structured_text(row: dict) -> dict[str, str]:
    from appcore.pushes import CopywritingParseError, parse_copywriting_body

    raw_body = _evidence_text_value(row.get("body"))
    try:
        parsed = parse_copywriting_body(raw_body)
    except CopywritingParseError:
        parsed = {}
    return {
        "title": _evidence_text_value(
            parsed.get("title") or row.get("title") or row.get("ad_title")
        ),
        "body": _evidence_text_value(
            parsed.get("message")
            or row.get("body")
            or row.get("ad_body")
            or row.get("primary_text")
        ),
        "description": _evidence_text_value(
            parsed.get("description") or row.get("description")
        ),
    }


def _copywriting_evidence(product_id: int, lang: str) -> list[dict]:
    from appcore import medias

    rows = medias.list_copywritings(int(product_id), (lang or "").strip().lower()) or []
    evidence: list[dict] = []
    for index, row in enumerate(rows[:3], start=1):
        structured = _copywriting_structured_text(row or {})
        evidence.append(
            {
                "type": "text",
                "label": f"文案 {index}",
                "title": structured["title"],
                "body": structured["body"],
                "description": structured["description"],
                "lines": [
                    {"label": "标题", "value": structured["title"]},
                    {"label": "文案", "value": structured["body"]},
                    {"label": "描述", "value": structured["description"]},
                ],
            }
        )
    return evidence


def _evidence_if(items: list[dict]) -> dict:
    return {"evidence": items} if items else {}


def _link_rows_by_domain(link_status: dict) -> dict[str, dict]:
    rows = link_status.get("links") if isinstance(link_status, dict) else []
    result: dict[str, dict] = {}
    for row in rows or []:
        domain = str(row.get("domain") or "").strip().lower()
        if domain:
            result[domain] = row
    return result


def _shopify_image_evidence(readiness: dict, link_status: dict) -> list[dict]:
    details = (readiness or {}).get("shopify_image_domain_details") or []
    if not details:
        reason = str((readiness or {}).get("shopify_image_reason") or "").strip()
        if not reason:
            return []
        confirmed = bool((readiness or {}).get("shopify_image_confirmed"))
        return [
            _evidence_status(
                label="shopify 小语种链接图片状态",
                meta="图片正常" if confirmed else "未替换",
                ok=confirmed,
            )
        ]

    links_by_domain = _link_rows_by_domain(link_status)
    evidence: list[dict] = []
    for detail in details:
        domain = str(detail.get("domain") or "").strip().lower()
        confirmed = bool(detail.get("confirmed"))
        meta = "图片正常" if confirmed else "未替换"
        link = links_by_domain.get(domain) or {}
        url = str(link.get("url") or "").strip()
        label = (
            f"{domain} shopify 小语种链接图片状态"
            if domain else "shopify 小语种链接图片状态"
        )
        if url:
            evidence.append(_evidence_link(label=label, url=url, meta=meta, ok=confirmed))
        else:
            evidence.append(_evidence_status(label=label, meta=meta, ok=confirmed))
    return evidence


def _product_link_evidence(link_status: dict) -> list[dict]:
    evidence: list[dict] = []
    for link in (link_status or {}).get("links") or []:
        url = str(link.get("url") or "").strip()
        if not url:
            continue
        domain = str(link.get("domain") or "").strip()
        ok = bool(link.get("ok"))
        meta = "ok" if ok else str(
            link.get("error") or link.get("http_status") or "not_ready"
        )
        evidence.append(
            _evidence_link(
                label=f"{domain} 商品链接" if domain else "商品链接",
                url=url,
                meta=meta,
                ok=ok,
            )
        )
    return evidence


def _review_step(
    event_type: str,
    title: str,
    *,
    review_target: bool,
    assets: list[dict],
) -> dict:
    return {
        "event_type": event_type,
        "title": title,
        "review_target": bool(review_target),
        "assets": assets,
    }


def _load_task_review_row(task_id: int) -> dict | None:
    return query_one(
        "SELECT t.*, p.product_code AS product_code, p.name AS product_name "
        "FROM tasks t JOIN media_products p ON p.id=t.media_product_id "
        "WHERE t.id=%s",
        (int(task_id),),
    )


def _load_review_media_item(media_item_id: Any) -> dict | None:
    item_id = _positive_int(media_item_id)
    if item_id is None:
        return None
    return query_one(
        "SELECT id, filename, display_name, object_key, cover_object_key, "
        "file_size, lang FROM media_items "
        "WHERE id=%s AND deleted_at IS NULL",
        (item_id,),
    )


def _parent_review_assets_payload(row: dict) -> dict:
    item = _load_review_media_item(row.get("media_item_id"))
    asset = _review_video_asset(item, label="去字幕原始视频素材")
    assets = [asset] if asset else []
    review_target = row.get("status") == PARENT_RAW_REVIEW
    steps = [
        _review_step(
            "raw_niuma_done",
            "牛马去字幕完成",
            review_target=False,
            assets=assets,
        ),
        _review_step(
            "raw_manual_uploaded",
            "手动上传原始视频",
            review_target=False,
            assets=assets,
        ),
        _review_step(
            "raw_uploaded",
            "提交原始视频审核",
            review_target=review_target,
            assets=assets,
        ),
    ]
    current_review = None
    if review_target:
        current_review = {
            "event_type": "raw_uploaded",
            "title": "当前待审核：去字幕原始视频素材",
            "asset_count": len(assets),
        }
    return {
        "task_id": row.get("id"),
        "current_review": current_review,
        "steps": steps,
    }


def _load_child_review_items(task_id: int) -> list[dict]:
    return [
        dict(row)
        for row in query_all(
            "SELECT mi.id, mi.filename, mi.display_name, mi.object_key, "
            "mi.cover_object_key, mi.file_size, mi.lang "
            "FROM media_items mi "
            "WHERE mi.task_id=%s AND mi.deleted_at IS NULL "
            "ORDER BY mi.lang, mi.id DESC",
            (int(task_id),),
        )
    ]


def _load_child_review_detail_images(product_id: int, lang: str) -> list[dict]:
    return [
        dict(row)
        for row in query_all(
            "SELECT id, object_key, file_size, width, height "
            "FROM media_product_detail_images "
            "WHERE product_id=%s AND lang=%s AND deleted_at IS NULL "
            "ORDER BY sort_order ASC, id ASC",
            (int(product_id), (lang or "").strip().lower()),
        )
    ]


def _child_review_assets_payload(row: dict) -> dict:
    task_id = int(row["id"])
    lang = (row.get("country_code") or "").strip().lower()
    assets: list[dict] = []
    for item in _load_child_review_items(task_id):
        video = _review_video_asset(item, label="翻译视频")
        if video:
            assets.append(video)
        cover = _review_item_cover_asset(item)
        if cover:
            assets.append(cover)
    for index, image in enumerate(
        _load_child_review_detail_images(int(row["media_product_id"]), lang),
        start=1,
    ):
        assets.append(_review_detail_image_asset(image, index))

    review_target = row.get("status") == CHILD_REVIEW
    steps = [
        _review_step(
            "submitted",
            "提交翻译验收",
            review_target=review_target,
            assets=assets,
        )
    ]
    current_review = None
    if review_target:
        current_review = {
            "event_type": "submitted",
            "title": "当前待审核：翻译产物",
            "asset_count": len(assets),
        }
    return {
        "task_id": task_id,
        "current_review": current_review,
        "steps": steps,
    }


def get_task_review_assets(task_id: int) -> dict:
    """Return reviewable media assets grouped by task process step.

    Docs-anchor:
    docs/superpowers/specs/2026-05-20-task-center-step-review-assets-design.md
    """
    row = _load_task_review_row(int(task_id))
    if not row:
        raise StateError("task not found")
    if row.get("parent_task_id") is None:
        return _parent_review_assets_payload(row)
    return _child_review_assets_payload(row)


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


def _normalize_child_acceptance_step_key(step_key: str) -> str:
    key = str(step_key or "").strip().lower()
    if key not in CHILD_ACCEPTANCE_STEP_LABELS:
        raise ValueError("unknown step")
    return key


def _manual_confirmed_child_step_keys(task_id: int) -> set[str]:
    rows = query_all(
        "SELECT payload_json FROM task_events "
        "WHERE task_id=%s AND event_type=%s ORDER BY id ASC",
        (int(task_id), CHILD_MANUAL_STEP_CONFIRMED_EVENT),
    )
    confirmed: set[str] = set()
    for row in rows or []:
        payload = _parse_event_payload_obj(row.get("payload_json"))
        try:
            confirmed.add(_normalize_child_acceptance_step_key(payload.get("key") or ""))
        except ValueError:
            continue
    return confirmed


def _normalize_push_rework_issue_keys(issue_keys: Iterable[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_key in issue_keys or []:
        key = str(raw_key or "").strip()
        if not key:
            continue
        if key not in PUSH_REWORK_ISSUE_DEFS:
            raise ValueError("unknown issue key")
        if key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    if not normalized:
        raise ValueError("issue_keys must be non-empty")
    return normalized


def _push_rework_issue_labels(issue_keys: Iterable[str]) -> list[str]:
    return [
        str(PUSH_REWORK_ISSUE_DEFS[key]["label"])
        for key in issue_keys
        if key in PUSH_REWORK_ISSUE_DEFS
    ]


def _push_rework_task_check_keys(issue_keys: Iterable[str]) -> list[str]:
    return [
        str(PUSH_REWORK_ISSUE_DEFS[key]["task_check_key"])
        for key in issue_keys
        if key in PUSH_REWORK_ISSUE_DEFS
    ]


def _latest_push_rework_rejection(task_id: int) -> dict:
    row = query_one(
        "SELECT payload_json FROM task_events "
        "WHERE task_id=%s AND event_type=%s "
        "ORDER BY id DESC LIMIT 1",
        (int(task_id), CHILD_PUSH_REWORK_REJECTED_EVENT),
    )
    return _parse_event_payload_obj((row or {}).get("payload_json"))


def active_push_rework_readiness_keys(task_id: int) -> list[str]:
    row = query_one(
        "SELECT status FROM tasks WHERE id=%s AND parent_task_id IS NOT NULL",
        (int(task_id),),
    )
    if not row or row.get("status") != CHILD_ASSIGNED:
        return []
    payload = _latest_push_rework_rejection(int(task_id))
    if not payload:
        return []
    try:
        return _normalize_push_rework_issue_keys(payload.get("issue_keys") or [])
    except ValueError:
        return []


def _apply_child_push_rework_rejections(
    checks: list[dict],
    *,
    rejection: dict | None,
) -> list[dict]:
    payload = rejection or {}
    issue_keys = payload.get("issue_keys") or []
    task_check_keys = payload.get("task_check_keys") or _push_rework_task_check_keys(issue_keys)
    rejected_keys = {
        str(key or "").strip()
        for key in task_check_keys
        if str(key or "").strip()
    }
    if not rejected_keys:
        return checks
    reason = str(payload.get("reason") or "").strip()
    reason_text = f"管理员已拒绝：{reason}" if reason else "管理员已拒绝"
    for check in checks:
        key = str(check.get("key") or "").strip()
        if key not in rejected_keys:
            continue
        check["ok"] = False
        check["admin_rejected"] = True
        check["reason"] = reason_text
    return checks


def _manual_output_config_for_child_step(key: str) -> dict | None:
    kind = CHILD_MANUAL_OUTPUT_STEP_KINDS.get(str(key or "").strip())
    if not kind:
        return None
    accept_by_kind = {
        "video": "video/mp4,video/quicktime",
        "image": "image/jpeg,image/png,image/webp,image/gif",
        "images": "image/jpeg,image/png,image/webp,image/gif",
        "text": "",
    }
    multiple_by_kind = {"images": True}
    return {
        "kind": kind,
        "accept": accept_by_kind.get(kind, ""),
        "multiple": bool(multiple_by_kind.get(kind)),
    }


def _apply_child_manual_confirmations(
    checks: list[dict],
    *,
    confirmed_keys: set[str],
    task_id: int,
    product_id: int,
    product_code: str,
    lang: str,
    item: dict | None,
) -> list[dict]:
    normalized_confirmed = {
        _normalize_child_acceptance_step_key(key)
        for key in (confirmed_keys or set())
        if str(key or "").strip()
    }
    for check in checks:
        key = str(check.get("key") or "").strip()
        manual_output = _manual_output_config_for_child_step(key)
        if manual_output:
            check["manual_output"] = manual_output
        if key not in normalized_confirmed:
            continue
        check["manual_confirmed"] = True
    return checks


def _missing_child_acceptance_keys(checks: list[dict]) -> list[str]:
    missing = []
    for check in checks:
        if not check.get("required") or check.get("ok"):
            continue
        key = str(check.get("key") or "").strip()
        missing.append(CHILD_ACCEPTANCE_MISSING_ALIASES.get(key, key))
    return missing


def _missing_item_child_acceptance_checks() -> list[dict]:
    checks = [
        _acceptance_check(
            "localized_media_item",
            CHILD_ACCEPTANCE_STEP_LABELS["localized_media_item"],
            False,
            reason="未找到该语种 media_item",
        )
    ]
    for key in CHILD_ACCEPTANCE_STEP_KEYS:
        if key == "localized_media_item":
            continue
        checks.append(
            _acceptance_check(
                key,
                CHILD_ACCEPTANCE_STEP_LABELS[key],
                False,
                reason="系统未找到目标语种素材，需手动提交对应结果或补齐目标语种素材",
            )
        )
    return checks


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
        "evidence": [
            _review_detail_image_asset(row, index)
            for index, row in enumerate(target_rows, start=1)
        ],
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


def _recent_copywriting_translate_task_id(product_id: int, lang: str) -> str:
    lang_code = (lang or "").strip().lower()
    if not product_id or not lang_code:
        return ""
    order_clause = _projects_recent_order_clause()
    rows = query_all(
        "SELECT id, state_json FROM projects "
        "WHERE type='copywriting_translate' AND deleted_at IS NULL "
        f"{order_clause} LIMIT 100"
    )
    for row in rows or []:
        state = _parse_event_payload_obj(row.get("state_json"))
        if (str(state.get("target_lang") or "").strip().lower()) != lang_code:
            continue
        target_copy_id = _positive_int(state.get("target_copy_id"))
        if not target_copy_id:
            continue
        copy_row = query_one(
            "SELECT product_id FROM media_copywritings WHERE id=%s",
            (target_copy_id,),
        )
        if copy_row and int(copy_row.get("product_id") or 0) == int(product_id):
            return str(row.get("id") or "").strip()
    return ""


def _detail_image_preview_rows(product_id: int, lang: str) -> list[dict]:
    from appcore import medias

    rows = medias.list_detail_images(int(product_id), (lang or "").strip().lower()) or []
    return [
        dict(row)
        for row in rows
        if not medias.detail_image_is_gif(row)
    ][:3]


def _recent_detail_image_translate_task_id(product_id: int, lang: str) -> str:
    lang_code = (lang or "").strip().lower()
    if not product_id or not lang_code:
        return ""
    row = query_one(
        "SELECT image_translate_task_id "
        "FROM media_product_detail_images "
        "WHERE product_id=%s AND lang=%s AND deleted_at IS NULL "
        "  AND image_translate_task_id IS NOT NULL AND image_translate_task_id<>'' "
        "ORDER BY updated_at DESC, created_at DESC LIMIT 1",
        (int(product_id), lang_code),
    )
    return str((row or {}).get("image_translate_task_id") or "").strip()


def _readiness_locate_url(
    *,
    task_id: int,
    product_id: int,
    product_code: str,
    lang: str,
    action: str,
    item_id: int | None = None,
    focus: str = "",
) -> str:
    extra: dict[str, Any] = {}
    if item_id:
        extra["item"] = int(item_id)
    if focus:
        extra["focus"] = focus
    return _medias_search_url(
        product_code=product_code,
        task_id=task_id,
        product_id=product_id,
        lang=lang,
        action=action,
        extra=extra,
    )


def _child_check_actions(
    *,
    key: str,
    task_id: int,
    product_id: int,
    product_code: str,
    lang: str,
    item: dict | None,
    links: list[dict] | None = None,
) -> list[dict]:
    item_id = int(item["id"]) if item and item.get("id") else None
    if key == "localized_media_item":
        if item_id:
            return [
                _action(
                    "定位素材",
                    _readiness_locate_url(
                        task_id=task_id,
                        product_id=product_id,
                        product_code=product_code,
                        lang=lang,
                        action="video",
                        item_id=item_id,
                    ),
                    "locate",
                    primary=True,
                )
            ]
        return [
            _action(
                "去生成/绑定素材",
                _readiness_locate_url(
                    task_id=task_id,
                    product_id=product_id,
                    product_code=product_code,
                    lang=lang,
                    action="translate",
                ),
                "locate",
                primary=True,
            )
        ]
    if key == "translated_video":
        actions: list[dict] = []
        object_url = _review_media_object_url((item or {}).get("object_key"))
        if object_url:
            actions.append(_action("预览视频", object_url, "preview", primary=True))
        actions.append(
            _action(
                "定位素材",
                _readiness_locate_url(
                    task_id=task_id,
                    product_id=product_id,
                    product_code=product_code,
                    lang=lang,
                    action="video",
                    item_id=item_id,
                ),
                "locate",
                primary=not bool(object_url),
            )
        )
        return actions
    if key == "translated_cover":
        actions = []
        if item_id and (item or {}).get("cover_object_key"):
            actions.append(_action("查看封面", f"/medias/item-cover/{item_id}", "preview", primary=True))
        actions.append(
            _action(
                "定位封面",
                _readiness_locate_url(
                    task_id=task_id,
                    product_id=product_id,
                    product_code=product_code,
                    lang=lang,
                    action="cover",
                    item_id=item_id,
                ),
                "locate",
                primary=not actions,
            )
        )
        return actions
    if key == "translated_copywriting":
        actions = [
            _action(
                "定位文案",
                _readiness_locate_url(
                    task_id=task_id,
                    product_id=product_id,
                    product_code=product_code,
                    lang=lang,
                    action="copywriting",
                ),
                "locate",
                primary=True,
            )
        ]
        copy_task_id = _recent_copywriting_translate_task_id(product_id, lang)
        if copy_task_id:
            actions.append(
                _action(
                    "查看文案翻译任务",
                    f"/copywriting-translate/{quote(copy_task_id, safe='')}",
                    "task",
                )
            )
        return actions
    if key == "detail_images":
        actions = [
            _action(
                "定位详情图",
                _readiness_locate_url(
                    task_id=task_id,
                    product_id=product_id,
                    product_code=product_code,
                    lang=lang,
                    action="detail_images",
                ),
                "locate",
                primary=True,
            )
        ]
        for idx, row in enumerate(_detail_image_preview_rows(product_id, lang)[:3], start=1):
            image_id = row.get("id")
            if image_id:
                actions.append(_action(f"查看详情图 {idx}", f"/medias/detail-image/{int(image_id)}", "preview"))
        image_task_id = _recent_detail_image_translate_task_id(product_id, lang)
        if image_task_id:
            actions.append(
                _action(
                    "查看图片翻译任务",
                    f"/image-translate/{quote(image_task_id, safe='')}",
                    "task",
                )
            )
        return actions
    if key == "shopify_images":
        return [
            _action(
                "检查商品图替换",
                _readiness_locate_url(
                    task_id=task_id,
                    product_id=product_id,
                    product_code=product_code,
                    lang=lang,
                    action="product_links",
                    focus="shopify_images",
                ),
                "locate",
                primary=True,
            )
        ]
    if key == "product_links":
        actions = [
            _action(
                "检查产品链接",
                _readiness_locate_url(
                    task_id=task_id,
                    product_id=product_id,
                    product_code=product_code,
                    lang=lang,
                    action="product_links",
                    focus="product_links",
                ),
                "locate",
                primary=True,
            )
        ]
        for row in links or []:
            url = str(row.get("url") or "").strip()
            if not url:
                continue
            domain = str(row.get("domain") or "").strip() or "链接"
            actions.append(_action(f"打开 {domain}", url, "external"))
        return actions
    if key == "push_texts":
        return [
            _action(
                "定位文案",
                _readiness_locate_url(
                    task_id=task_id,
                    product_id=product_id,
                    product_code=product_code,
                    lang=lang,
                    action="copywriting",
                ),
                "locate",
            )
        ]
    if key in {"product_listed", "language_supported"}:
        return [
            _action(
                "检查产品配置",
                _readiness_locate_url(
                    task_id=task_id,
                    product_id=product_id,
                    product_code=product_code,
                    lang=lang,
                    action="product_links",
                ),
                "locate",
            )
        ]
    return []


def _artifact_actions(row: dict) -> list[dict]:
    actions: list[dict] = []
    object_url = _review_media_object_url(row.get("object_key"))
    if object_url:
        actions.append(_action("预览视频", object_url, "preview", primary=True))
    item_id = _positive_int(row.get("id"))
    if item_id and row.get("cover_object_key"):
        actions.append(_action("查看封面", f"/medias/item-cover/{item_id}", "preview"))
    locate_url = _medias_search_url(
        product_code=row.get("product_code"),
        task_id=_positive_int(row.get("task_id")),
        product_id=_positive_int(row.get("product_id")),
        lang=row.get("lang"),
        action="history",
        extra={"item": item_id},
    )
    actions.append(_action("定位素材", locate_url, "locate"))
    actions.append(_action("翻译任务记录", locate_url, "task"))
    return actions


def _child_acceptance_payload(
    *,
    task_id: int,
    row: dict,
    item: dict | None,
    product: dict | None,
    readiness: dict,
    include_evidence: bool = True,
    manual_confirmed_keys: set[str] | None = None,
    include_rework_rejections: bool = True,
) -> dict:
    product_id = int(row["media_product_id"])
    lang = (row.get("country_code") or "").strip().lower()
    product_code = (
        (product or {}).get("product_code")
        or row.get("product_code")
        or ""
    )
    ad_supported_langs = (
        (product or {}).get("ad_supported_langs")
        if product is not None
        else row.get("ad_supported_langs")
    ) or row.get("ad_supported_langs") or ""
    if manual_confirmed_keys is None:
        manual_confirmed_keys = _manual_confirmed_child_step_keys(int(task_id))
    task_status = str(row.get("status") or "").strip()
    rework_rejection = (
        _latest_push_rework_rejection(int(task_id))
        if include_rework_rejections and task_status in (CHILD_ASSIGNED, CHILD_REVIEW)
        else {}
    )

    if not item:
        media_search_url = _medias_search_url(
            product_code=product_code,
            task_id=task_id,
            product_id=product_id,
            lang=lang,
        )
        checks = _missing_item_child_acceptance_checks()
        checks = _apply_child_manual_confirmations(
            checks,
            confirmed_keys=manual_confirmed_keys,
            task_id=task_id,
            product_id=product_id,
            product_code=product_code,
            lang=lang,
            item=None,
        )
        checks = _apply_child_push_rework_rejections(
            checks,
            rejection=rework_rejection,
        )
        missing = _missing_child_acceptance_keys(checks)
        return {
            "ready": not missing,
            "missing": missing,
            "readiness": {},
            "checks": checks,
            "country_code": row["country_code"],
            "product_code": product_code,
            "media_product_id": product_id,
            "ad_supported_langs": ad_supported_langs,
            "media_search_url": media_search_url,
            "manual_confirmed_steps": sorted(manual_confirmed_keys),
        }

    media_search_url = _medias_search_url(
        product_code=product_code,
        task_id=task_id,
        product_id=product_id,
        lang=lang,
    )
    detail_status = _detail_images_status(product_id, lang)
    link_status = _product_link_availability_status(product_id, lang, product)
    video_asset = _review_video_asset(item, label="视频翻译结果") if include_evidence else None
    cover_asset = (
        _review_item_cover_asset(item, label="封面翻译结果")
        if include_evidence
        else None
    )
    copywriting_evidence = (
        _copywriting_evidence(product_id, lang) if include_evidence else []
    )
    shopify_evidence = (
        _shopify_image_evidence(readiness, link_status) if include_evidence else []
    )
    product_link_evidence = (
        _product_link_evidence(link_status) if include_evidence else []
    )
    checks = [
        _acceptance_check(
            "localized_media_item",
            "目标语种素材",
            True,
            **_evidence_if(
                [
                    _evidence_link(
                        label="打开目标语种素材",
                        url=media_search_url,
                        meta=f"media_item #{int(item['id'])}",
                    )
                ]
            ),
        ),
        _acceptance_check(
            "translated_video",
            "视频翻译结果",
            _readiness_bool(readiness, "has_object"),
            **_evidence_if([video_asset] if video_asset else []),
        ),
        _acceptance_check(
            "translated_cover",
            "封面翻译结果",
            _readiness_bool(readiness, "has_cover"),
            **_evidence_if([cover_asset] if cover_asset else []),
        ),
        _acceptance_check(
            "translated_copywriting",
            "文案翻译结果",
            _readiness_bool(readiness, "has_copywriting"),
            **_evidence_if(copywriting_evidence),
        ),
        _acceptance_check(
            "push_texts",
            "推送文案格式",
            _readiness_bool(readiness, "has_push_texts"),
            **_evidence_if(
                [
                    _evidence_status(
                        label="推送文案格式",
                        meta="英文三段文案可解析"
                        if _readiness_bool(readiness, "has_push_texts")
                        else "英文三段文案不可解析",
                        ok=_readiness_bool(readiness, "has_push_texts"),
                    )
                ]
            ),
        ),
        _acceptance_check(
            "product_listed",
            "商品在架状态",
            _readiness_bool(readiness, "is_listed"),
            **_evidence_if(
                [
                    _evidence_status(
                        label="商品在架状态",
                        meta="已在架"
                        if _readiness_bool(readiness, "is_listed")
                        else "未在架",
                        ok=_readiness_bool(readiness, "is_listed"),
                    )
                ]
            ),
        ),
        _acceptance_check(
            "detail_images",
            "产品详情图翻译",
            bool(detail_status.get("ok")),
            required=bool(detail_status.get("required")),
            reason=detail_status.get("reason") or "",
            source_count=int(detail_status.get("source_count") or 0),
            target_count=int(detail_status.get("target_count") or 0),
            **_evidence_if(detail_status.get("evidence") or []),
        ),
        _acceptance_check(
            "shopify_images",
            "链接商品图替换",
            _readiness_bool(readiness, "shopify_image_confirmed"),
            reason=(readiness or {}).get("shopify_image_reason") or "",
            **_evidence_if(shopify_evidence),
        ),
        _acceptance_check(
            "product_links",
            "商品链接探活",
            bool(link_status.get("ok")),
            required=bool(link_status.get("required")),
            reason=link_status.get("reason") or "",
            links=link_status.get("links") or [],
            **_evidence_if(product_link_evidence),
        ),
        _acceptance_check(
            "language_supported",
            FINAL_MATERIAL_CONFIRM_LABEL,
            _readiness_bool(readiness, "lang_supported"),
            hint=FINAL_MATERIAL_CONFIRM_HINT,
            **_evidence_if(
                [
                    _evidence_status(
                        label=FINAL_MATERIAL_CONFIRM_LABEL,
                        meta=f"{str(row['country_code']).upper()} 已完成确认"
                        if _readiness_bool(readiness, "lang_supported")
                        else f"{str(row['country_code']).upper()} 未确认",
                        ok=_readiness_bool(readiness, "lang_supported"),
                    )
                ]
            ),
        ),
    ]
    for check in checks:
        check["actions"] = (
            _child_check_actions(
                key=str(check.get("key") or ""),
                task_id=task_id,
                product_id=product_id,
                product_code=product_code,
                lang=lang,
                item=item,
                links=check.get("links") or [],
            )
            if include_evidence
            else []
        )
    checks = _apply_child_manual_confirmations(
        checks,
        confirmed_keys=manual_confirmed_keys,
        task_id=task_id,
        product_id=product_id,
        product_code=product_code,
        lang=lang,
        item=item,
    )
    checks = _apply_child_push_rework_rejections(
        checks,
        rejection=rework_rejection,
    )
    missing = _missing_child_acceptance_keys(checks)
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
        "media_product_id": product_id,
        "ad_supported_langs": ad_supported_langs,
        "media_item_id": item["id"],
        "media_search_url": media_search_url,
        "manual_confirmed_steps": sorted(manual_confirmed_keys),
    }


def _child_readiness_payload_for_row(
    *,
    task_id: int,
    row: dict,
    include_evidence: bool = True,
    include_rework_rejections: bool = True,
) -> dict:
    from appcore import pushes

    item = _find_target_lang_item(row["media_product_id"], row["country_code"])
    if not item:
        return _child_acceptance_payload(
            task_id=int(task_id),
            row=row,
            item=None,
            product=None,
            readiness={},
            include_evidence=include_evidence,
            include_rework_rejections=include_rework_rejections,
        )

    product = _find_product(row["media_product_id"])
    if include_rework_rejections:
        readiness = pushes.compute_readiness(item, product)
    else:
        try:
            readiness = pushes.compute_readiness(
                item,
                product,
                include_rework_overrides=False,
            )
        except TypeError:
            readiness = pushes.compute_readiness(item, product)
    return _child_acceptance_payload(
        task_id=int(task_id),
        row=row,
        item=item,
        product=product,
        readiness=readiness,
        include_evidence=include_evidence,
        include_rework_rejections=include_rework_rejections,
    )


def get_child_readiness(task_id: int) -> dict:
    row = query_one(
        "SELECT t.media_product_id, t.country_code, t.status, p.product_code, p.ad_supported_langs "
        "FROM tasks t JOIN media_products p ON p.id=t.media_product_id "
        "WHERE t.id=%s AND t.parent_task_id IS NOT NULL",
        (int(task_id),),
    )
    if not row:
        raise StateError("child task not found")

    return _child_readiness_payload_for_row(
        task_id=int(task_id),
        row=row,
    )


def complete_child_if_ready(*, task_id: int, actor_user_id: int | None = None) -> dict:
    """Mark a translation child task done once its material is push-ready."""
    row = query_one(
        "SELECT * FROM tasks WHERE id=%s AND parent_task_id IS NOT NULL",
        (int(task_id),),
    )
    if not row:
        raise StateError("child task not found")
    if row["status"] == CHILD_DONE:
        return {"completed": True, "already_completed": True, "missing": []}
    if row["status"] not in (CHILD_ASSIGNED, CHILD_REVIEW):
        return {
            "completed": False,
            "status": row["status"],
            "missing": [],
        }

    payload = _child_readiness_payload_for_row(
        task_id=int(task_id),
        row=row,
        include_evidence=False,
        include_rework_rejections=False,
    )
    if not payload["ready"]:
        return {
            "completed": False,
            "status": row["status"],
            "missing": payload["missing"],
        }

    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=NULL, "
                    "completed_at=COALESCE(completed_at, NOW()), updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NOT NULL AND status IN (%s,%s)",
                    (CHILD_DONE, int(task_id), CHILD_ASSIGNED, CHILD_REVIEW),
                )
                if cur.rowcount == 0:
                    raise StateError("child not completable")
                _write_event(
                    cur,
                    task_id,
                    "auto_completed",
                    actor_user_id,
                    {"reason": "push_ready"},
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
    return {"completed": True, "missing": []}


def confirm_child_step(
    *,
    task_id: int,
    step_key: str,
    actor_user_id: int,
    is_admin: bool = False,
) -> dict:
    """Record a manual fallback confirmation for one child acceptance step."""
    normalized_key = _normalize_child_acceptance_step_key(step_key)
    row = query_one(
        "SELECT id, assignee_id, status FROM tasks "
        "WHERE id=%s AND parent_task_id IS NOT NULL",
        (int(task_id),),
    )
    if not row:
        raise StateError("child task not found")
    if row["assignee_id"] != int(actor_user_id) and not is_admin:
        raise PermissionError("forbidden")
    if row["status"] not in (CHILD_ASSIGNED, CHILD_REVIEW):
        raise StateError("child not confirmable")

    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                _write_event(
                    cur,
                    int(task_id),
                    CHILD_MANUAL_STEP_CONFIRMED_EVENT,
                    int(actor_user_id),
                    {"key": normalized_key},
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
    return {"step_key": normalized_key}


def _manual_text_payload(text: dict | None) -> dict:
    payload = text or {}
    title = str(payload.get("title") or "").strip()
    message = str(payload.get("message") or payload.get("body") or "").strip()
    description = str(payload.get("description") or "").strip()
    if not any((title, message, description)):
        raise ValueError("text payload required")
    if not title or not message or not description:
        raise ValueError("title, message and description are required")
    return {"title": title, "message": message, "description": description}


def _manual_push_text_body(fields: dict) -> str:
    return (
        f"标题: {fields['title']}\n"
        f"文案: {fields['message']}\n"
        f"描述: {fields['description']}"
    )


def _record_manual_output_event(
    *,
    task_id: int,
    actor_user_id: int,
    payload: dict,
) -> None:
    execute(
        "INSERT INTO task_events (task_id, event_type, actor_user_id, payload_json) "
        "VALUES (%s, %s, %s, %s)",
        (
            int(task_id),
            CHILD_MANUAL_STEP_OUTPUT_EVENT,
            int(actor_user_id),
            json.dumps(payload, ensure_ascii=False),
        ),
    )


def _create_manual_copywriting(
    *,
    product_id: int,
    lang: str,
    step_key: str,
    fields: dict,
) -> int:
    idx = 1 if step_key == "push_texts" else 0
    body = _manual_push_text_body(fields) if step_key == "push_texts" else fields["message"]
    return int(execute(
        "INSERT INTO media_copywritings "
        "(product_id, lang, idx, title, body, description, auto_translated, manually_edited_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, 0, NOW())",
        (
            int(product_id),
            lang,
            idx,
            fields["title"],
            body,
            fields["description"],
        ),
    ) or 0)


def submit_child_step_manual_output(
    *,
    task_id: int,
    step_key: str,
    actor_user_id: int,
    is_admin: bool = False,
    text: dict | None = None,
    files: list[dict] | None = None,
) -> dict:
    """Persist a manual result for a child acceptance step.

    Manual results are real media/text records. They do not mark a condition
    complete by themselves unless the resulting material satisfies readiness.
    """
    from appcore import medias

    normalized_key = _normalize_child_acceptance_step_key(step_key)
    kind = CHILD_MANUAL_OUTPUT_STEP_KINDS.get(normalized_key)
    if not kind:
        raise ValueError("step does not accept manual output")

    row = query_one(
        "SELECT id, assignee_id, status, media_product_id, country_code "
        "FROM tasks WHERE id=%s AND parent_task_id IS NOT NULL",
        (int(task_id),),
    )
    if not row:
        raise StateError("child task not found")
    if row["assignee_id"] != int(actor_user_id) and not is_admin:
        raise PermissionError("forbidden")
    if row["status"] not in (CHILD_ASSIGNED, CHILD_REVIEW, CHILD_DONE):
        raise StateError("child not editable")

    product_id = int(row["media_product_id"])
    lang = str(row.get("country_code") or "").strip().lower()
    file_items = list(files or [])
    result: dict[str, Any] = {"step_key": normalized_key, "kind": kind, "manual": True}

    if kind == "text":
        fields = _manual_text_payload(text)
        copywriting_id = _create_manual_copywriting(
            product_id=product_id,
            lang="en" if normalized_key == "push_texts" else lang,
            step_key=normalized_key,
            fields=fields,
        )
        result["copywriting_id"] = copywriting_id
    elif kind == "video":
        if len(file_items) != 1:
            raise ValueError("one video file required")
        file_info = file_items[0]
        filename = str(file_info.get("filename") or "manual-video.mp4")
        object_key = str(file_info["object_key"])
        display_name = filename

        # 查找是否存在同 product_id + 同 lang 的 media_item（即使被软删除了也取最新的一条，将其重新复用）
        existing_item = query_one(
            "SELECT * FROM media_items WHERE product_id=%s AND lang=%s ORDER BY id DESC LIMIT 1",
            (product_id, lang),
        )
        if existing_item:
            item_id = int(existing_item["id"])
            medias._ensure_video_filename_no_spaces(filename)
            medias._ensure_video_filename_no_spaces(object_key)
            medias._ensure_video_filename_no_spaces(display_name)
            execute(
                "UPDATE media_items SET "
                "filename=%s, display_name=%s, object_key=%s, file_size=%s, "
                "user_id=%s, task_id=%s, deleted_at=NULL "
                "WHERE id=%s",
                (
                    filename,
                    display_name,
                    object_key,
                    file_info.get("file_size"),
                    int(actor_user_id),
                    int(task_id),
                    item_id,
                ),
            )
        else:
            item_id = medias.create_item(
                product_id,
                int(actor_user_id),
                filename,
                object_key,
                display_name=display_name,
                file_size=file_info.get("file_size"),
                lang=lang,
                task_id=int(task_id),
            )
        result["media_item_id"] = int(item_id)
        result["object_key"] = object_key

        # 异步启动后台任务以同步最新的视频状态 (提取 duration, 制作缩略图，并刷新推送状态缓存)
        import threading
        from web.services.media_items import build_item_thumbnail
        from web.services import media_object_storage
        from appcore.pushes import _refresh_push_status_cache_for_item_safely

        def refresh_bg():
            try:
                build_item_thumbnail(
                    item_id=int(item_id),
                    product_id=product_id,
                    filename=filename,
                    object_key=object_key,
                    download_media_object_fn=media_object_storage.download_media_object,
                )
            except Exception:
                pass
            try:
                _refresh_push_status_cache_for_item_safely(int(item_id))
            except Exception:
                pass

        threading.Thread(target=refresh_bg, daemon=True).start()
    elif kind == "image":
        if len(file_items) != 1:
            raise ValueError("one image file required")
        item = _find_target_lang_item(product_id, lang)
        if not item or not item.get("object_key"):
            raise ValueError("target media item required before cover")
        file_info = file_items[0]
        medias.update_item_cover(item["id"], str(file_info["object_key"]))
        result["media_item_id"] = int(item["id"])
        result["object_key"] = file_info["object_key"]

        # 异步刷新推送状态缓存
        import threading
        from appcore.pushes import _refresh_push_status_cache_for_item_safely
        threading.Thread(
            target=lambda: _refresh_push_status_cache_for_item_safely(int(item["id"])),
            daemon=True,
        ).start()
    elif kind == "images":
        if not file_items:
            raise ValueError("image files required")
        created_ids = []
        for file_info in file_items:
            created_ids.append(medias.add_detail_image(
                product_id,
                lang,
                str(file_info["object_key"]),
                content_type=file_info.get("content_type") or None,
                file_size=file_info.get("file_size"),
                origin_type="manual",
            ))
        result["detail_image_ids"] = [int(image_id) for image_id in created_ids]

    _record_manual_output_event(
        task_id=int(task_id),
        actor_user_id=int(actor_user_id),
        payload=result,
    )
    result["completion"] = complete_child_if_ready(
        task_id=int(task_id),
        actor_user_id=int(actor_user_id),
    )
    return result


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
    items = []
    for row in rows:
        item = dict(row)
        item["actions"] = _artifact_actions(item)
        items.append(item)
    return items


def submit_child(*, task_id: int, actor_user_id: int) -> None:
    """翻译员提交子任务；调 compute_readiness 做产物齐全 gate。"""
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

    result = complete_child_if_ready(
        task_id=int(task_id),
        actor_user_id=int(actor_user_id),
    )
    if not result.get("completed"):
        missing = result.get("missing") or []
        raise NotReadyError(missing=missing, detail=f"readiness failed: {missing}")


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
    """admin 取消父任务；只终止当前父任务，不联动子任务。"""
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
                _write_event(cur, task_id, "cancelled", actor_user_id,
                             {"reason": reason})
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


def reject_child_from_push(
    *,
    task_id: int,
    actor_user_id: int,
    issue_keys: Iterable[Any],
    reason: str,
    image_urls: Iterable[str] = None,
) -> dict:
    """管理员从推送管理打回已产出的翻译素材，让负责人继续处理。"""
    if not reason or len(reason.strip()) < MIN_REASON_LEN:
        raise ValueError(f"reason must be at least {MIN_REASON_LEN} characters")
    reason = reason.strip()
    normalized_keys = _normalize_push_rework_issue_keys(issue_keys)
    issue_labels = _push_rework_issue_labels(normalized_keys)
    task_check_keys = _push_rework_task_check_keys(normalized_keys)
    label_text = "、".join(issue_labels)
    last_reason = f"管理员已拒绝：{label_text}。拒绝原因：{reason}"
    payload = {
        "source": "push_management",
        "reason": reason,
        "issue_keys": normalized_keys,
        "issue_labels": issue_labels,
        "task_check_keys": task_check_keys,
        "image_urls": list(image_urls) if image_urls else [],
    }

    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, parent_task_id, status, media_product_id "
                    "FROM tasks WHERE id=%s AND parent_task_id IS NOT NULL FOR UPDATE",
                    (int(task_id),),
                )
                row = cur.fetchone()
                if not row:
                    raise StateError("child task not found")
                if row.get("status") not in (CHILD_DONE, CHILD_REVIEW, CHILD_ASSIGNED):
                    raise StateError("child not rejectable from push")

                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=%s, completed_at=NULL, "
                    "updated_at=NOW() WHERE id=%s AND parent_task_id IS NOT NULL "
                    "AND status IN (%s,%s,%s)",
                    (
                        CHILD_ASSIGNED,
                        last_reason,
                        int(task_id),
                        CHILD_DONE,
                        CHILD_REVIEW,
                        CHILD_ASSIGNED,
                    ),
                )
                if cur.rowcount == 0:
                    raise StateError("child not rejectable from push")
                _write_event(
                    cur,
                    task_id,
                    CHILD_PUSH_REWORK_REJECTED_EVENT,
                    actor_user_id,
                    payload,
                )

                parent_id = _positive_int(row.get("parent_task_id"))
                if parent_id is not None:
                    cur.execute(
                        "UPDATE tasks SET status=%s, completed_at=NULL, updated_at=NOW() "
                        "WHERE id=%s AND parent_task_id IS NULL AND status=%s",
                        (PARENT_RAW_DONE, int(parent_id), PARENT_ALL_DONE),
                    )
                    if cur.rowcount:
                        _write_event(
                            cur,
                            parent_id,
                            "push_rework_parent_reopened",
                            actor_user_id,
                            {"child_task_id": int(task_id), "reason": reason},
                        )

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

    return {
        "task_id": int(task_id),
        "status": CHILD_ASSIGNED,
        "issue_keys": normalized_keys,
        "issue_labels": issue_labels,
        "task_check_keys": task_check_keys,
        "reason": reason,
        "image_urls": list(image_urls) if image_urls else [],
    }


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
    """Deprecated compatibility hook.

    产品负责人只代表素材归属；任务负责人以创建任务时的显式指派为准。
    保留该函数避免历史调用方崩溃，但不再读写 tasks。
    """
    return 0


def get_employee_task_stats(today_str: str) -> list[dict]:
    """获取当前所有被指派了任务的员工的任务统计数据。

    返回字段：
      - assignee_id (int)
      - employee_name (str)
      - today_completed (int)
      - today_pending (int)
      - total_tasks (int)
      - raw_tasks (int)
      - translate_tasks (int)
    """
    expr = _user_display_name_expr("u")
    sql = (
        "SELECT "
        "  t.assignee_id, "
        f"  {expr} AS employee_name, "
        "  SUM(CASE WHEN DATE(t.completed_at) = %s AND ("
        "      (t.parent_task_id IS NULL AND t.status IN ('raw_done', 'all_done')) OR "
        "      (t.parent_task_id IS NOT NULL AND t.status = 'done')"
        "  ) THEN 1 ELSE 0 END) AS today_completed, "
        "  SUM(CASE WHEN ("
        "      (t.parent_task_id IS NULL AND t.status IN ('pending', 'raw_in_progress', 'raw_review')) OR "
        "      (t.parent_task_id IS NOT NULL AND t.status IN ('blocked', 'assigned', 'review'))"
        "  ) THEN 1 ELSE 0 END) AS today_pending, "
        "  COUNT(t.id) AS total_tasks, "
        "  SUM(CASE WHEN t.parent_task_id IS NULL THEN 1 ELSE 0 END) AS raw_tasks, "
        "  SUM(CASE WHEN t.parent_task_id IS NOT NULL THEN 1 ELSE 0 END) AS translate_tasks "
        "FROM tasks t "
        "JOIN users u ON u.id = t.assignee_id "
        "WHERE t.assignee_id IS NOT NULL "
        "GROUP BY t.assignee_id, employee_name "
        "ORDER BY total_tasks DESC"
    )
    rows = query_all(sql, (today_str,))
    return [
        {
            "assignee_id": int(row["assignee_id"]),
            "employee_name": str(row["employee_name"]),
            "today_completed": int(row["today_completed"]),
            "today_pending": int(row["today_pending"]),
            "total_tasks": int(row["total_tasks"]),
            "raw_tasks": int(row["raw_tasks"]),
            "translate_tasks": int(row["translate_tasks"]),
        }
        for row in rows
    ]
