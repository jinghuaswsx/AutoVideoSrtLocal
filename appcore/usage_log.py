"""Fire-and-forget usage logging. Never raises."""
from __future__ import annotations
from decimal import Decimal
import logging
from typing import Any

from appcore.db import query

log = logging.getLogger(__name__)


def _build_usage_report_filter(
    *,
    admin: bool,
    user_id: int | None,
    service: str,
    date_from: str,
    date_to: str,
) -> tuple[str, tuple]:
    where = "WHERE 1=1"
    args = []

    if not admin:
        where += " AND ul.user_id = %s"
        args.append(user_id)

    if service:
        where += " AND ul.service = %s"
        args.append(service)
    if date_from:
        where += " AND DATE(ul.called_at) >= %s"
        args.append(date_from)
    if date_to:
        where += " AND DATE(ul.called_at) <= %s"
        args.append(date_to)

    return where, tuple(args)


def get_usage_report(
    *,
    admin: bool,
    user_id: int | None,
    service: str,
    date_from: str,
    date_to: str,
) -> dict:
    where, args = _build_usage_report_filter(
        admin=admin,
        user_id=user_id,
        service=service,
        date_from=date_from,
        date_to=date_to,
    )

    rows = query(
        f"""
        SELECT u.username, ul.service, ul.model_name,
               DATE(ul.called_at) AS day,
               COUNT(*) AS calls,
               SUM(ul.input_tokens) AS input_tokens,
               SUM(ul.output_tokens) AS output_tokens,
               SUM(ul.audio_duration_seconds) AS audio_seconds
        FROM usage_logs ul
        JOIN users u ON u.id = ul.user_id
        {where}
        GROUP BY u.username, ul.service, ul.model_name, day
        ORDER BY day DESC, u.username
        """,
        args,
    )

    summary_rows = query(
        f"""
        SELECT COUNT(*) AS total_calls,
               COALESCE(SUM(ul.input_tokens), 0) AS total_input_tokens,
               COALESCE(SUM(ul.output_tokens), 0) AS total_output_tokens,
               COALESCE(SUM(ul.audio_duration_seconds), 0) AS total_audio_seconds
        FROM usage_logs ul
        {where}
        """,
        args,
    )
    summary = summary_rows[0] if summary_rows else {
        "total_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_audio_seconds": 0,
    }

    services = query("SELECT DISTINCT service FROM usage_logs ORDER BY service")
    return {
        "rows": rows,
        "summary": summary,
        "service_list": [row["service"] for row in services],
    }


def record_payload(log_id: int, request_data: Any, response_data: Any) -> None:
    """Store request/response JSON for an existing usage log row. Never raises."""
    if not log_id:
        return
    try:
        import json
        from appcore.db import execute

        execute(
            """INSERT INTO usage_log_payloads (log_id, request_data, response_data)
               VALUES (%s, %s, %s)""",
            (
                log_id,
                json.dumps(request_data, ensure_ascii=False, default=str)
                if request_data is not None else None,
                json.dumps(response_data, ensure_ascii=False, default=str)
                if response_data is not None else None,
            ),
        )
    except Exception as e:
        log.debug("usage_log.record_payload failed: %s", e)


def record(
    user_id: int | None,
    project_id: str | None,
    service: str,
    *,
    use_case_code: str | None = None,
    module: str | None = None,
    provider: str | None = None,
    model_name: str | None = None,
    success: bool = True,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    audio_duration_seconds: float | None = None,
    request_units: int | None = None,
    units_type: str | None = None,
    cost_cny: Decimal | None = None,
    cost_source: str = "unknown",
    extra_data: dict | None = None,
) -> int | None:
    """Returns the new row's id, or None on failure."""
    if user_id is None:
        return None
    try:
        import json
        from appcore.db import execute
        return execute(
            """INSERT INTO usage_logs
               (user_id, project_id, service, use_case_code, module, provider,
                model_name, success, input_tokens, output_tokens,
                audio_duration_seconds, request_units, units_type, cost_cny,
                cost_source, extra_data)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, project_id, service, use_case_code, module, provider,
             model_name, int(success), input_tokens, output_tokens,
             audio_duration_seconds, request_units, units_type, cost_cny,
             cost_source, json.dumps(extra_data) if extra_data else None),
        ) or None
    except Exception as e:
        log.debug("usage_log.record failed: %s", e)
        return None
