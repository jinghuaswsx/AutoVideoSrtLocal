"""Append-only system security audit logging."""
from __future__ import annotations

import json
import logging
from typing import Any

from appcore.db import execute, query, query_one

log = logging.getLogger(__name__)

MEDIA_DOWNLOAD_ACTIONS = (
    "media_video_access",
    "raw_source_video_access",
    "detail_images_zip_download",
    "localized_detail_images_zip_download",
)


def _json_dumps(data: dict[str, Any] | None) -> str | None:
    if not data:
        return None
    return json.dumps(data, ensure_ascii=False, default=str)


def _clean_str(value: Any, limit: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if limit and len(text) > limit:
        return text[:limit]
    return text


def record(
    *,
    actor_user_id: int | None,
    actor_username: str | None,
    action: str,
    module: str,
    target_type: str | None = None,
    target_id: int | str | None = None,
    target_label: str | None = None,
    status: str = "success",
    request_method: str | None = None,
    request_path: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    detail: dict[str, Any] | None = None,
) -> int | None:
    """Record an audit event and never raise to callers."""
    try:
        return execute(
            """
            INSERT INTO system_audit_logs
              (actor_user_id, actor_username, action, module, target_type,
               target_id, target_label, status, request_method, request_path,
               ip_address, user_agent, detail_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(actor_user_id) if actor_user_id is not None else None,
                _clean_str(actor_username, 64),
                _clean_str(action, 64),
                _clean_str(module, 64),
                _clean_str(target_type, 64),
                _clean_str(target_id, 64),
                _clean_str(target_label, 255),
                _clean_str(status or "success", 16),
                _clean_str(request_method, 8),
                _clean_str(request_path, 512),
                _clean_str(ip_address, 64),
                _clean_str(user_agent, 512),
                _json_dumps(detail),
            ),
        ) or None
    except Exception:
        log.debug("system_audit.record failed", exc_info=True)
        return None


def record_from_request(
    *,
    user: Any,
    request_obj: Any,
    action: str,
    module: str,
    target_type: str | None = None,
    target_id: int | str | None = None,
    target_label: str | None = None,
    status: str = "success",
    detail: dict[str, Any] | None = None,
) -> int | None:
    actor_user_id = getattr(user, "id", None)
    actor_username = getattr(user, "username", None)
    headers = getattr(request_obj, "headers", {}) or {}
    forwarded_for = headers.get("X-Forwarded-For", "")
    ip_address = (
        forwarded_for.split(",", 1)[0].strip()
        if forwarded_for else None
    ) or getattr(request_obj, "remote_addr", None)
    user_agent = getattr(getattr(request_obj, "user_agent", None), "string", None)
    request_path = getattr(request_obj, "full_path", None) or getattr(request_obj, "path", None)
    if request_path and request_path.endswith("?"):
        request_path = request_path[:-1]
    return record(
        actor_user_id=actor_user_id if actor_user_id is not None else None,
        actor_username=actor_username,
        action=action,
        module=module,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        status=status,
        request_method=getattr(request_obj, "method", None),
        request_path=request_path,
        ip_address=ip_address,
        user_agent=user_agent,
        detail=detail,
    )


def _log_filters(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    actor_user_id: int | None = None,
    module: str | None = None,
    action: str | None = None,
    keyword: str | None = None,
) -> tuple[list[str], list[Any]]:
    where = ["1=1"]
    args: list[Any] = []
    if date_from:
        where.append("DATE(created_at) >= %s")
        args.append(date_from)
    if date_to:
        where.append("DATE(created_at) <= %s")
        args.append(date_to)
    if actor_user_id:
        where.append("actor_user_id = %s")
        args.append(int(actor_user_id))
    if module:
        where.append("module = %s")
        args.append(module)
    if action:
        where.append("action = %s")
        args.append(action)
    if keyword:
        where.append(
            "(target_label LIKE %s OR target_id LIKE %s OR "
            "request_path LIKE %s OR actor_username LIKE %s)"
        )
        like = f"%{keyword}%"
        args.extend([like, like, like, like])
    return where, args


def _bounded_limit(limit: int) -> int:
    return max(1, min(int(limit), 200))


def _bounded_offset(offset: int) -> int:
    return max(0, int(offset))


def list_logs(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    actor_user_id: int | None = None,
    module: str | None = None,
    action: str | None = None,
    keyword: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    where, args = _log_filters(
        date_from=date_from,
        date_to=date_to,
        actor_user_id=actor_user_id,
        module=module,
        action=action,
        keyword=keyword,
    )
    args.extend([_bounded_limit(limit), _bounded_offset(offset)])
    return query(
        f"""
        SELECT * FROM system_audit_logs
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC, id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(args),
    )


def count_logs(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    actor_user_id: int | None = None,
    module: str | None = None,
    action: str | None = None,
    keyword: str | None = None,
) -> int:
    where, args = _log_filters(
        date_from=date_from,
        date_to=date_to,
        actor_user_id=actor_user_id,
        module=module,
        action=action,
        keyword=keyword,
    )
    row = query_one(
        f"SELECT COUNT(*) AS cnt FROM system_audit_logs WHERE {' AND '.join(where)}",
        tuple(args),
    )
    return int((row or {}).get("cnt") or 0)


def _media_download_filters(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    actor_user_id: int | None = None,
    keyword: str | None = None,
) -> tuple[list[str], list[Any]]:
    where = [
        "action IN ('media_video_access', 'raw_source_video_access', "
        "'detail_images_zip_download', 'localized_detail_images_zip_download')"
    ]
    args: list[Any] = []
    if date_from:
        where.append("DATE(created_at) >= %s")
        args.append(date_from)
    if date_to:
        where.append("DATE(created_at) <= %s")
        args.append(date_to)
    if actor_user_id:
        where.append("actor_user_id = %s")
        args.append(int(actor_user_id))
    if keyword:
        where.append("(target_label LIKE %s OR target_id LIKE %s OR detail_json LIKE %s)")
        like = f"%{keyword}%"
        args.extend([like, like, like])
    return where, args


def list_daily_media_downloads(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    actor_user_id: int | None = None,
    keyword: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    where, args = _media_download_filters(
        date_from=date_from,
        date_to=date_to,
        actor_user_id=actor_user_id,
        keyword=keyword,
    )
    args.extend([_bounded_limit(limit), _bounded_offset(offset)])
    return query(
        f"""
        SELECT * FROM system_audit_logs
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC, id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(args),
    )


def count_daily_media_downloads(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    actor_user_id: int | None = None,
    keyword: str | None = None,
) -> int:
    where, args = _media_download_filters(
        date_from=date_from,
        date_to=date_to,
        actor_user_id=actor_user_id,
        keyword=keyword,
    )
    row = query_one(
        f"SELECT COUNT(*) AS cnt FROM system_audit_logs WHERE {' AND '.join(where)}",
        tuple(args),
    )
    return int((row or {}).get("cnt") or 0)
