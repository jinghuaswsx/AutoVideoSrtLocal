from __future__ import annotations
import json
import bcrypt

from appcore.db import query, query_one, execute
from appcore.permissions import (
    ROLE_SUPERADMIN,
    default_permissions_for_role,
    is_valid_role,
    merge_with_defaults,
    normalize_permissions,
)


SUPERADMIN_USERNAME = "admin"
OPTIONAL_USER_PROFILE_COLUMNS = ("xingming",)
_MISSING = object()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def update_password(user_id: int, password: str) -> None:
    pw_hash = hash_password(password)
    execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (pw_hash, user_id),
    )


def _user_column_exists(column: str) -> bool:
    if column not in OPTIONAL_USER_PROFILE_COLUMNS:
        return False
    row = query_one(
        "SELECT 1 AS ok FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s LIMIT 1",
        ("users", column),
    )
    return bool(row)


def editable_user_profile_fields() -> list[str]:
    return [column for column in OPTIONAL_USER_PROFILE_COLUMNS if _user_column_exists(column)]


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
    columns = ["id", "username", "role", "permissions", "is_active", "created_at"]
    columns.extend(editable_user_profile_fields())
    return query(
        f"SELECT {', '.join(columns)} FROM users ORDER BY id"
    )


def _coerce_permissions(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _has_bool_permission(row: dict, code: str) -> bool:
    return bool(_coerce_permissions(row.get("permissions")).get(code))


def _has_effective_bool_permission(row: dict, code: str) -> bool:
    if row.get("role") == ROLE_SUPERADMIN:
        return True
    return _has_bool_permission(row, code)


def _user_display_name_expr() -> str:
    row = query_one(
        "SELECT 1 AS ok FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' "
        "AND COLUMN_NAME = 'xingming'"
    )
    if row:
        return "COALESCE(NULLIF(TRIM(xingming), ''), username)"
    return "username"


def list_translators() -> list[dict]:
    rows = query(
        "SELECT id, username, role, permissions FROM users WHERE is_active=1 ORDER BY username ASC",
    )
    translators = []
    for row in rows:
        if _has_effective_bool_permission(row, "can_translate"):
            translators.append({"id": row["id"], "username": row["username"]})
    return translators


def list_translation_work_users() -> list[dict]:
    """列出翻译工作范围内的用户及其任务统计。

    统计口径与 ``appcore.tasks.get_user_workload_stats`` 对齐：
    - todo_count: 进行中（不含待推送、不含已归档）
    - urgent_count: 同 todo_count + is_urgent
    - completed_today_count: 今日已完成（含待推送状态、含已归档）
    """
    import datetime
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    today_start = f"{today_str} 00:00:00"
    counts_map: dict[int, dict] = {}
    try:
        from appcore.tasks import get_active_pending_push_task_ids
        pending_push_ids = get_active_pending_push_task_ids()
        exclude_clause = ""
        pending_push_include_clause = ""
        if pending_push_ids:
            ids_str = ",".join(str(i) for i in pending_push_ids)
            exclude_clause = f"AND id NOT IN ({ids_str}) "
            pending_push_include_clause = f"OR id IN ({ids_str}) "

        # todo_count / urgent_count: 进行中、未归档、排除待推送（和任务中心卡片 in_progress 一致）
        # completed_today_count: 今日已完成，不排除归档，包含待推送状态
        counts_rows = query(
            "SELECT assignee_id, "
            "  SUM(CASE WHEN ("
            "    (parent_task_id IS NULL AND status IN ('pending', 'raw_in_progress', 'raw_review')) OR "
            "    (parent_task_id IS NOT NULL AND status IN ('blocked', 'assigned', 'review'))"
            f"  ) AND archived_at IS NULL {exclude_clause} THEN 1 ELSE 0 END) AS todo_count, "
            "  SUM(CASE WHEN ("
            "    (parent_task_id IS NULL AND status IN ('pending', 'raw_in_progress', 'raw_review')) OR "
            "    (parent_task_id IS NOT NULL AND status IN ('blocked', 'assigned', 'review'))"
            f"  ) AND archived_at IS NULL AND is_urgent = 1 {exclude_clause} THEN 1 ELSE 0 END) AS urgent_count, "
            "  SUM(CASE WHEN ("
            "    ("
            "      (parent_task_id IS NULL AND status IN ('raw_done', 'all_done')) OR "
            "      (parent_task_id IS NOT NULL AND (status = 'done' "
            f"        {pending_push_include_clause}"
            "      ))"
            "    ) AND DATE(completed_at) = %s"
            "  ) THEN 1 ELSE 0 END) AS completed_today_count "
            "FROM tasks "
            "WHERE assignee_id IS NOT NULL "
            "GROUP BY assignee_id",
            (today_str,)
        )
        for row in counts_rows:
            if "assignee_id" in row:
                counts_map[int(row["assignee_id"])] = {
                    "todo_count": int(row.get("todo_count") or 0),
                    "urgent_count": int(row.get("urgent_count") or 0),
                    "completed_today_count": int(row.get("completed_today_count") or 0),
                }

        # 补充：查 task_events 中今日有 submitted/completed 事件的待推送任务
        # 这些任务可能 completed_at 不是今天，但今日确实提交了
        if pending_push_ids:
            event_rows = query(
                "SELECT DISTINCT t.assignee_id, t.id AS task_id "
                "FROM task_events te "
                "JOIN tasks t ON t.id = te.task_id "
                "WHERE te.event_type IN ('submitted', 'completed') "
                "  AND te.created_at >= %s "
                "  AND t.id IN (" + ",".join(str(i) for i in pending_push_ids) + ") "
                "  AND t.assignee_id IS NOT NULL",
                (today_start,)
            )
            # 收集每个 assignee 今日通过事件确认的待推送任务
            event_task_by_user: dict[int, set[int]] = {}
            for ev_row in event_rows:
                uid = int(ev_row["assignee_id"])
                tid = int(ev_row["task_id"])
                event_task_by_user.setdefault(uid, set()).add(tid)

            # 补充到 counts_map —— 对于那些 completed_at 不是今天但今日有事件的待推送任务
            for uid, task_ids in event_task_by_user.items():
                if uid not in counts_map:
                    counts_map[uid] = {"todo_count": 0, "urgent_count": 0, "completed_today_count": 0}
                # 需要查这些任务是否已经被 SQL 的 completed_today_count 计入
                # 如果 completed_at 是今天已经计入，只补充 completed_at 不是今天的
                already_rows = query(
                    "SELECT id FROM tasks WHERE id IN ("
                    + ",".join(str(i) for i in task_ids)
                    + ") AND DATE(completed_at) = %s",
                    (today_str,)
                )
                already_counted = {int(r["id"]) for r in already_rows}
                extra = len(task_ids - already_counted)
                if extra > 0:
                    counts_map[uid]["completed_today_count"] += extra
    except Exception:
        pass

    expr = _user_display_name_expr()
    rows = query(
        f"SELECT id, username, {expr} AS display_name, role, permissions "
        "FROM users WHERE is_active=1 ORDER BY display_name ASC, id ASC",
    )
    users = []
    for row in rows:
        if (
            _has_effective_bool_permission(row, "can_translate")
            and _has_effective_bool_permission(row, "work_scope_translation")
        ):
            stats = counts_map.get(int(row["id"]), {"todo_count": 0, "urgent_count": 0, "completed_today_count": 0})
            users.append({
                "id": int(row["id"]),
                "username": row["username"],
                "display_name": row.get("display_name") or row["username"],
                "todo_count": stats["todo_count"],
                "urgent_count": stats["urgent_count"],
                "completed_today_count": stats["completed_today_count"],
            })
    return users


def list_raw_processors() -> list[dict]:
    expr = _user_display_name_expr()
    rows = query(
        f"SELECT id, username, {expr} AS display_name, role, permissions "
        "FROM users WHERE is_active=1 ORDER BY display_name ASC, id ASC",
    )
    users = []
    for row in rows:
        if _has_effective_bool_permission(row, "can_process_raw_video"):
            users.append({
                "id": int(row["id"]),
                "username": row["username"],
                "display_name": row.get("display_name") or row["username"],
            })
    return users


def ensure_translation_work_user(user_id: int) -> dict:
    expr = _user_display_name_expr()
    row = query_one(
        f"SELECT id, username, {expr} AS display_name, role, permissions, is_active "
        "FROM users WHERE id=%s",
        (int(user_id),),
    )
    if not row:
        raise ValueError("翻译员不存在")
    if not row.get("is_active"):
        raise ValueError("翻译员已停用")
    if not _has_effective_bool_permission(row, "can_translate"):
        raise ValueError("该用户没有翻译能力")
    if not _has_effective_bool_permission(row, "work_scope_translation"):
        raise ValueError("该用户不在翻译工作范围")
    return row


def ensure_raw_processor_user(user_id: int) -> dict:
    expr = _user_display_name_expr()
    row = query_one(
        f"SELECT id, username, {expr} AS display_name, role, permissions, is_active "
        "FROM users WHERE id=%s",
        (int(user_id),),
    )
    if not row:
        raise ValueError("原视频处理人不存在")
    if not row.get("is_active"):
        raise ValueError("原视频处理人已停用")
    if not _has_effective_bool_permission(row, "can_process_raw_video"):
        raise ValueError("该用户没有原视频处理能力")
    return row


def set_active(user_id: int, active: bool) -> None:
    execute("UPDATE users SET is_active = %s WHERE id = %s", (int(active), user_id))


def update_user_profile(
    user_id: int,
    *,
    username: str,
    role: str,
    is_active: bool,
    xingming: str | None | object = _MISSING,
    work_scopes: list[str] | object = _MISSING,
) -> None:
    user = get_by_id(user_id)
    if user is None:
        raise ValueError(f"user not found: {user_id}")

    username = (username or "").strip()
    role = (role or "").strip()
    if not username:
        raise ValueError("username cannot be blank")
    if not is_valid_role(role):
        raise ValueError(f"invalid role: {role}")

    existing = get_by_username(username)
    if existing and int(existing["id"]) != int(user_id):
        raise ValueError(f"username already exists: {username}")

    current_role = user.get("role")
    if current_role == ROLE_SUPERADMIN:
        if role != ROLE_SUPERADMIN:
            raise ValueError("cannot demote the superadmin")
        if username != SUPERADMIN_USERNAME:
            raise ValueError("cannot rename the superadmin")
        is_active = True
        work_scopes = _MISSING
    elif role == ROLE_SUPERADMIN:
        raise ValueError("only the reserved username can hold superadmin role")

    assignments = ["username = %s", "role = %s", "is_active = %s"]
    args: list = [username, role, int(bool(is_active))]

    permissions_payload = None
    if current_role != role:
        permissions_payload = default_permissions_for_role(role)

    if work_scopes is not _MISSING:
        if permissions_payload is None:
            permissions_payload = merge_with_defaults(role, _coerce_permissions(user.get("permissions")))
        selected_scopes = {str(item).strip() for item in (work_scopes or []) if str(item).strip()}
        permissions_payload["work_scope_translation"] = "translation" in selected_scopes

    if permissions_payload is not None:
        assignments.append("permissions = %s")
        args.append(json.dumps(permissions_payload))

    if xingming is not _MISSING and _user_column_exists("xingming"):
        assignments.append("xingming = %s")
        args.append((str(xingming) if xingming is not None else "").strip())

    args.append(user_id)
    execute(
        f"UPDATE users SET {', '.join(assignments)} WHERE id = %s",
        tuple(args),
    )


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
