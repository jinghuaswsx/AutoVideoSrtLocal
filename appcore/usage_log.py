"""Fire-and-forget usage logging. Never raises."""
from __future__ import annotations
from decimal import Decimal
import logging
import math
from typing import Any

from appcore import medias
from appcore.db import query

log = logging.getLogger(__name__)


AI_USAGE_GROUP_BY_FIELDS = {
    "module": ("ul.module", "group_value"),
    "use_case": ("ul.use_case_code", "group_value"),
    "provider": ("ul.provider", "group_value"),
    "model": ("ul.model_name", "group_value"),
    "user": ("__user_display__", "group_value"),
}

REQUEST_PAYLOAD_BYTES_SQL = """
COALESCE(
    CAST(NULLIF(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(p.request_data, '$.network_estimate.estimated_base64_payload_bytes')), 'null'), '') AS UNSIGNED),
    CAST(NULLIF(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(p.request_data, '$.estimated_base64_payload_bytes')), 'null'), '') AS UNSIGNED),
    OCTET_LENGTH(CAST(p.request_data AS CHAR))
)
"""

RESPONSE_PAYLOAD_BYTES_SQL = "OCTET_LENGTH(CAST(p.response_data AS CHAR))"


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


def normalize_ai_usage_group_by(raw: str | None, *, admin: bool) -> str:
    group_by = (raw or "module").strip().lower()
    if group_by not in AI_USAGE_GROUP_BY_FIELDS:
        group_by = "module"
    if not admin and group_by == "user":
        group_by = "module"
    return group_by


def get_ai_usage_report(
    *,
    filters: dict,
    detail_filters: dict,
    admin: bool,
    paged: bool,
    page_size: int = 50,
) -> dict:
    where_sql, where_args = _build_ai_usage_where_clause(filters=filters, admin=admin)
    detail_where_sql, detail_where_args = _build_ai_usage_detail_where_clause(
        filters=filters,
        detail_filters=detail_filters,
        admin=admin,
    )
    detail_filter_options = _query_ai_usage_detail_filter_options(
        filters=filters,
        admin=admin,
    )
    user_display_expr = _ai_usage_user_display_expr()
    group_field, group_alias = AI_USAGE_GROUP_BY_FIELDS[filters["group_by"]]
    if group_field == "__user_display__":
        group_field = user_display_expr

    summary_rows = query(
        f"""
        SELECT
            COALESCE(SUM(ul.cost_cny), 0) AS total_cost_cny,
            COUNT(*) AS total_calls,
            COALESCE(SUM(CASE WHEN ul.cost_source <> 'unknown' AND ul.cost_cny IS NOT NULL THEN 1 ELSE 0 END), 0) AS billed_calls,
            COALESCE(SUM(CASE WHEN ul.cost_source = 'unknown' OR ul.cost_cny IS NULL THEN 1 ELSE 0 END), 0) AS unbilled_calls
        FROM usage_logs ul
        LEFT JOIN users u ON u.id = ul.user_id
        {where_sql}
        """,
        tuple(where_args),
    )
    summary = summary_rows[0] if summary_rows else {
        "total_cost_cny": Decimal("0"),
        "total_calls": 0,
        "billed_calls": 0,
        "unbilled_calls": 0,
    }

    groups = query(
        f"""
        SELECT
            {group_field} AS {group_alias},
            COUNT(*) AS calls,
            COALESCE(SUM(ul.request_units), 0) AS request_units,
            COALESCE(SUM(ul.cost_cny), 0) AS cost_cny
        FROM usage_logs ul
        LEFT JOIN users u ON u.id = ul.user_id
        {where_sql}
        GROUP BY {group_field}
        ORDER BY cost_cny DESC, {group_alias} ASC
        """,
        tuple(where_args),
    )

    detail_summary_rows = query(
        f"""
        SELECT
            COUNT(*) AS detail_total_calls,
            COALESCE(SUM(ul.cost_cny), 0) AS detail_total_cost_cny,
            COALESCE(SUM(
                COALESCE({REQUEST_PAYLOAD_BYTES_SQL}, 0)
                + COALESCE({RESPONSE_PAYLOAD_BYTES_SQL}, 0)
            ), 0) AS detail_payload_bytes,
            COALESCE(SUM(CASE
                WHEN p.request_data IS NOT NULL OR p.response_data IS NOT NULL THEN 1
                ELSE 0
            END), 0) AS payload_recorded_calls
        FROM usage_logs ul
        LEFT JOIN users u ON u.id = ul.user_id
        LEFT JOIN usage_log_payloads p ON p.log_id = ul.id
        {detail_where_sql}
        """,
        tuple(detail_where_args),
    )
    detail_summary = detail_summary_rows[0] if detail_summary_rows else {
        "detail_total_calls": 0,
        "detail_total_cost_cny": Decimal("0"),
        "detail_payload_bytes": 0,
        "payload_recorded_calls": 0,
    }
    detail_summary["detail_payload_mb"] = _format_ai_usage_mb(
        detail_summary.get("detail_payload_bytes")
    )

    rows_sql = f"""
        SELECT
            ul.id,
            ul.called_at,
            ul.user_id,
            u.username,
            {user_display_expr} AS user_display_name,
            ul.project_id,
            ul.service,
            ul.use_case_code,
            ul.module,
            ul.provider,
            ul.model_name,
            ul.success,
            ul.input_tokens,
            ul.output_tokens,
            ul.audio_duration_seconds,
            ul.request_units,
            ul.units_type,
            ul.cost_cny,
            ul.cost_source,
            ul.extra_data,
            {REQUEST_PAYLOAD_BYTES_SQL} AS request_payload_bytes,
            {RESPONSE_PAYLOAD_BYTES_SQL} AS response_payload_bytes
        FROM usage_logs ul
        LEFT JOIN users u ON u.id = ul.user_id
        LEFT JOIN usage_log_payloads p ON p.log_id = ul.id
        {detail_where_sql}
        ORDER BY ul.called_at DESC, ul.id DESC
    """
    row_args = list(detail_where_args)
    if paged:
        offset = (filters["page"] - 1) * page_size
        rows_sql += " LIMIT %s OFFSET %s"
        row_args.extend([page_size, offset])
    rows = query(rows_sql, tuple(row_args))
    for row in rows:
        row["request_payload_mb"] = _format_ai_usage_mb(row.get("request_payload_bytes"))
        row["response_payload_mb"] = _format_ai_usage_mb(row.get("response_payload_bytes"))

    total_calls = int(detail_summary.get("detail_total_calls") or 0)
    total_pages = max(1, math.ceil(total_calls / page_size)) if paged else 1

    return {
        "summary": summary,
        "detail_summary": detail_summary,
        "groups": groups,
        "rows": rows,
        "filters": filters,
        "detail_filters": detail_filters,
        "detail_filter_options": detail_filter_options,
        "group_by": filters["group_by"],
        "page": filters["page"],
        "total_pages": total_pages,
    }


def _build_ai_usage_where_clause(*, filters: dict, admin: bool) -> tuple[str, list]:
    clauses, args = _build_ai_usage_clause_parts(filters=filters, admin=admin)
    if not clauses:
        return "", args
    return "WHERE " + " AND ".join(clauses), args


def _build_ai_usage_detail_where_clause(
    *,
    filters: dict,
    detail_filters: dict,
    admin: bool,
) -> tuple[str, list]:
    clauses, args = _build_ai_usage_clause_parts(filters=filters, admin=admin)

    if admin:
        _append_ai_usage_in_clause(clauses, args, "ul.user_id", detail_filters["user_ids"])

    _append_ai_usage_in_clause(clauses, args, "ul.module", detail_filters["modules"])
    _append_ai_usage_in_clause(clauses, args, "ul.use_case_code", detail_filters["use_cases"])
    _append_ai_usage_in_clause(clauses, args, "ul.provider", detail_filters["providers"])
    _append_ai_usage_in_clause(
        clauses,
        args,
        "ul.success",
        [1 if status else 0 for status in detail_filters["statuses"]],
    )

    if not clauses:
        return "", args
    return "WHERE " + " AND ".join(clauses), args


def _append_ai_usage_in_clause(clauses: list[str], args: list, field: str, values: list) -> None:
    if not values:
        return
    placeholders = ", ".join(["%s"] * len(values))
    clauses.append(f"{field} IN ({placeholders})")
    args.extend(values)


def _build_ai_usage_clause_parts(*, filters: dict, admin: bool) -> tuple[list[str], list]:
    clauses: list[str] = []
    args: list = []

    if admin:
        if filters["user_id"] is not None:
            clauses.append("ul.user_id = %s")
            args.append(filters["user_id"])
    else:
        clauses.append("ul.user_id = %s")
        args.append(filters["user_id"])

    if filters["date_from"]:
        clauses.append("DATE(ul.called_at) >= %s")
        args.append(filters["date_from"])
    if filters["date_to"]:
        clauses.append("DATE(ul.called_at) <= %s")
        args.append(filters["date_to"])
    if filters["module"]:
        clauses.append("ul.module = %s")
        args.append(filters["module"])
    if filters["use_case"]:
        clauses.append("ul.use_case_code = %s")
        args.append(filters["use_case"])
    if filters["provider"]:
        clauses.append("ul.provider = %s")
        args.append(filters["provider"])
    if filters["model"]:
        clauses.append("ul.model_name = %s")
        args.append(filters["model"])
    if filters["status"] is not None:
        clauses.append("ul.success = %s")
        args.append(1 if filters["status"] else 0)
    if filters["q"]:
        clauses.append("ul.project_id LIKE %s")
        args.append(f"%{filters['q']}%")

    return clauses, args


def _query_ai_usage_detail_filter_options(*, filters: dict, admin: bool) -> dict:
    clauses, args = _build_ai_usage_clause_parts(filters=filters, admin=admin)
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    return {
        "statuses": [
            {"value": "success", "label": "成功"},
            {"value": "failed", "label": "失败"},
        ],
        "modules": _query_ai_usage_distinct_options("ul.module", where_sql, args),
        "use_cases": _query_ai_usage_distinct_options("ul.use_case_code", where_sql, args),
        "providers": _query_ai_usage_distinct_options("ul.provider", where_sql, args),
        "users": _query_ai_usage_user_options(where_sql, args) if admin else [],
    }


def _query_ai_usage_distinct_options(field: str, where_sql: str, args: list) -> list[dict]:
    rows = query(
        f"""
        SELECT DISTINCT {field} AS value
        FROM usage_logs ul
        LEFT JOIN users u ON u.id = ul.user_id
        {_ai_usage_where_with_extra(where_sql, f"{field} IS NOT NULL AND {field} <> ''")}
        ORDER BY value ASC
        """,
        tuple(args),
    )
    return [{"value": row["value"], "label": row["value"]} for row in rows]


def _query_ai_usage_user_options(where_sql: str, args: list) -> list[dict]:
    user_display_expr = _ai_usage_user_display_expr()
    rows = query(
        f"""
        SELECT DISTINCT ul.user_id AS value, {user_display_expr} AS label
        FROM usage_logs ul
        LEFT JOIN users u ON u.id = ul.user_id
        {_ai_usage_where_with_extra(where_sql, "ul.user_id IS NOT NULL")}
        ORDER BY label ASC, value ASC
        """,
        tuple(args),
    )
    return [
        {"value": int(row["value"]), "label": (row.get("label") or f"用户 {row['value']}")}
        for row in rows
    ]


def _ai_usage_user_display_expr() -> str:
    return medias._media_product_owner_name_expr()


def _ai_usage_where_with_extra(where_sql: str, extra_condition: str) -> str:
    if where_sql:
        return f"{where_sql} AND {extra_condition}"
    return f"WHERE {extra_condition}"


def _format_ai_usage_mb(raw_bytes) -> str | None:
    if raw_bytes is None:
        return None
    try:
        size = int(raw_bytes)
    except (TypeError, ValueError):
        return None
    if size <= 0:
        return None
    return f"{size / 1024 / 1024:.2f} MB"


def get_usage_payload(log_id: int) -> dict | None:
    rows = query(
        "SELECT request_data, response_data FROM usage_log_payloads WHERE log_id = %s",
        (int(log_id),),
    )
    return rows[0] if rows else None


def get_user_usage_payload(log_id: int, *, user_id: int) -> dict | None:
    rows = query(
        """SELECT p.request_data, p.response_data
           FROM usage_log_payloads p
           JOIN usage_logs ul ON ul.id = p.log_id
           WHERE p.log_id = %s AND ul.user_id = %s""",
        (int(log_id), int(user_id)),
    )
    return rows[0] if rows else None


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
