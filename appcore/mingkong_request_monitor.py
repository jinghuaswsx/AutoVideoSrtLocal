from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from appcore.db import execute, query

log = logging.getLogger(__name__)

TASK_CODE = "mingkong_request_rate_monitor"
TABLE_NAME = "mingkong_outbound_request_logs"
DEFAULT_THRESHOLD_PER_MINUTE = 60
DEFAULT_WINDOW_MINUTES = 10
DEFAULT_RETENTION_DAYS = 14

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  called_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  source VARCHAR(160) NOT NULL,
  method VARCHAR(12) NOT NULL,
  host VARCHAR(255) NOT NULL,
  path VARCHAR(768) NOT NULL,
  status_code INT NULL,
  duration_ms INT UNSIGNED NULL,
  response_bytes BIGINT UNSIGNED NULL,
  error_type VARCHAR(120) NULL,
  error_message VARCHAR(512) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_mk_outbound_called_at (called_at),
  KEY idx_mk_outbound_host_called (host, called_at),
  KEY idx_mk_outbound_source_called (source, called_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else f"{text[:limit]}..."


def _auto_db_recording_disabled() -> bool:
    if os.getenv("AUTOVIDEOSRT_ENABLE_MINGKONG_REQUEST_MONITOR_IN_TESTS") == "1":
        return False
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


def ensure_table(*, execute_fn: Callable[..., Any] = execute) -> None:
    execute_fn(_CREATE_TABLE_SQL)


def cleanup_old_logs(
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    execute_fn: Callable[..., Any] = execute,
) -> int:
    days = max(1, int(retention_days or DEFAULT_RETENTION_DAYS))
    return int(execute_fn(
        f"DELETE FROM {TABLE_NAME} WHERE called_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
        (days,),
    ) or 0)


def _url_parts(url: str) -> tuple[str, str]:
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").strip().lower()
    path = parsed.path or "/"
    return _clip(host, 255), _clip(path, 768)


def is_mingkong_url(url: str, *, base_url: str | None = None) -> bool:
    host, _path = _url_parts(url)
    if not host:
        return False
    base_host = _url_parts(base_url or "https://os.wedev.vip")[0]
    return host == base_host


def _response_bytes(response: Any, *, stream: bool) -> int | None:
    headers = getattr(response, "headers", None) or {}
    try:
        raw = headers.get("content-length")
    except AttributeError:
        raw = None
    if raw not in (None, ""):
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            pass
    if stream:
        return None
    content = getattr(response, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return len(content)
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return len(text.encode("utf-8"))
    return None


def record_request(
    *,
    source: str,
    method: str,
    url: str,
    status_code: int | None = None,
    duration_ms: int | None = None,
    response_bytes: int | None = None,
    error: BaseException | str | None = None,
    called_at: datetime | None = None,
    execute_fn: Callable[..., Any] | None = None,
) -> bool:
    if execute_fn is None and _auto_db_recording_disabled():
        return False
    exec_fn = execute_fn or execute
    host, path = _url_parts(url)
    error_type = None
    error_message = None
    if error is not None:
        error_type = error.__class__.__name__ if isinstance(error, BaseException) else "error"
        error_message = _clip(error, 512)
    try:
        ensure_table(execute_fn=exec_fn)
        exec_fn(
            f"""
            INSERT INTO {TABLE_NAME}
              (called_at, source, method, host, path, status_code, duration_ms,
               response_bytes, error_type, error_message)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                called_at or datetime.now(),
                _clip(source or "unknown", 160),
                _clip((method or "GET").upper(), 12),
                host,
                path,
                status_code,
                None if duration_ms is None else max(0, int(duration_ms)),
                response_bytes,
                _clip(error_type, 120) if error_type else None,
                error_message,
            ),
        )
        return True
    except Exception:
        log.debug("failed to record Mingkong outbound request", exc_info=True)
        return False


def _tracked_call(
    *,
    method: str,
    url: str,
    source: str,
    stream: bool,
    call_fn: Callable[[], Any],
) -> Any:
    start = time.monotonic()
    try:
        response = call_fn()
    except Exception as exc:
        duration_ms = int(round((time.monotonic() - start) * 1000))
        record_request(
            source=source,
            method=method,
            url=url,
            duration_ms=duration_ms,
            error=exc,
        )
        raise

    duration_ms = int(round((time.monotonic() - start) * 1000))
    status_code = getattr(response, "status_code", None)
    record_request(
        source=source,
        method=method,
        url=url,
        status_code=int(status_code) if status_code is not None else None,
        duration_ms=duration_ms,
        response_bytes=_response_bytes(response, stream=stream),
    )
    return response


def tracked_get(
    url: str,
    *,
    source: str,
    request_fn: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> Any:
    fn = request_fn or requests.get
    stream = bool(kwargs.get("stream"))
    return _tracked_call(
        method="GET",
        url=url,
        source=source,
        stream=stream,
        call_fn=lambda: fn(url, **kwargs),
    )


def tracked_post(
    url: str,
    *,
    source: str,
    request_fn: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> Any:
    fn = request_fn or requests.post
    stream = bool(kwargs.get("stream"))
    return _tracked_call(
        method="POST",
        url=url,
        source=source,
        stream=stream,
        call_fn=lambda: fn(url, **kwargs),
    )


def tracked_request(
    method: str,
    url: str,
    *,
    source: str,
    request_fn: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> Any:
    resolved_method = (method or "GET").upper()
    fn = request_fn or requests.request
    stream = bool(kwargs.get("stream"))
    return _tracked_call(
        method=resolved_method,
        url=url,
        source=source,
        stream=stream,
        call_fn=lambda: fn(resolved_method, url, **kwargs),
    )


def evaluate_minute_buckets(
    rows: list[dict[str, Any]],
    *,
    threshold_per_minute: int = DEFAULT_THRESHOLD_PER_MINUTE,
) -> dict[str, Any]:
    threshold = max(1, int(threshold_per_minute or DEFAULT_THRESHOLD_PER_MINUTE))
    buckets = []
    breached = []
    max_count = 0
    for row in rows or []:
        count = int(row.get("request_count") or 0)
        max_count = max(max_count, count)
        bucket = {
            "minute": str(row.get("minute_bucket") or ""),
            "request_count": count,
            "error_count": int(row.get("error_count") or 0),
            "first_called_at": str(row.get("first_called_at") or ""),
            "last_called_at": str(row.get("last_called_at") or ""),
            "sources": str(row.get("sources") or ""),
        }
        buckets.append(bucket)
        if count > threshold:
            breached.append(bucket)
    return {
        "threshold_per_minute": threshold,
        "max_requests_per_minute": max_count,
        "breached": bool(breached),
        "breached_minutes": breached,
        "top_buckets": buckets[:10],
    }


def inspect_recent_window(
    *,
    now: datetime | None = None,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    threshold_per_minute: int = DEFAULT_THRESHOLD_PER_MINUTE,
    query_fn: Callable[..., list[dict[str, Any]]] = query,
    execute_fn: Callable[..., Any] = execute,
) -> dict[str, Any]:
    end_at = now or datetime.now()
    minutes = max(1, int(window_minutes or DEFAULT_WINDOW_MINUTES))
    start_at = end_at - timedelta(minutes=minutes)
    ensure_table(execute_fn=execute_fn)
    cleanup_old_logs(execute_fn=execute_fn)
    rows = query_fn(
        f"""
        SELECT DATE_FORMAT(called_at, '%%Y-%%m-%%d %%H:%%i:00') AS minute_bucket,
               COUNT(*) AS request_count,
               COALESCE(SUM(CASE
                   WHEN error_type IS NOT NULL OR status_code IS NULL OR status_code >= 400 THEN 1
                   ELSE 0
               END), 0) AS error_count,
               MIN(called_at) AS first_called_at,
               MAX(called_at) AS last_called_at,
               GROUP_CONCAT(DISTINCT source ORDER BY source SEPARATOR ', ') AS sources
        FROM {TABLE_NAME}
        WHERE called_at >= %s AND called_at < %s
        GROUP BY minute_bucket
        ORDER BY request_count DESC, minute_bucket DESC
        LIMIT 20
        """,
        (start_at, end_at),
    )
    summary = evaluate_minute_buckets(rows, threshold_per_minute=threshold_per_minute)
    summary.update({
        "window_minutes": minutes,
        "window_start": start_at.isoformat(sep=" ", timespec="seconds"),
        "window_end": end_at.isoformat(sep=" ", timespec="seconds"),
        "buckets_checked": len(rows or []),
    })
    return summary


def _breach_error(summary: dict[str, Any]) -> str:
    breach = (summary.get("breached_minutes") or [{}])[0]
    return (
        "明空外呼频率超过阈值："
        f"{breach.get('minute') or '-'} 共有 {breach.get('request_count') or 0} 次请求，"
        f"阈值为每分钟 {summary.get('threshold_per_minute') or DEFAULT_THRESHOLD_PER_MINUTE} 次"
    )


def run_scheduled_check(
    *,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    threshold_per_minute: int = DEFAULT_THRESHOLD_PER_MINUTE,
) -> dict[str, Any]:
    from appcore import scheduled_tasks

    run_id = scheduled_tasks.start_run(TASK_CODE)
    try:
        summary = inspect_recent_window(
            window_minutes=window_minutes,
            threshold_per_minute=threshold_per_minute,
        )
        if summary.get("breached"):
            scheduled_tasks.finish_run(
                run_id,
                status="failed",
                summary=summary,
                error_message=_breach_error(summary),
            )
            return summary
        scheduled_tasks.finish_run(run_id, status="success", summary=summary)
        return summary
    except Exception as exc:
        scheduled_tasks.finish_run(run_id, status="failed", error_message=str(exc))
        raise
