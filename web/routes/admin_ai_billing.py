from __future__ import annotations

import csv
import io
import math
from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context
from flask_login import current_user, login_required

from appcore import medias
from appcore.db import query, query_one
from web.auth import admin_required


PAGE_SIZE = 50

GROUP_BY_FIELDS = {
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

CSV_COLUMNS = [
    "id",
    "called_at",
    "user_id",
    "username",
    "project_id",
    "service",
    "use_case_code",
    "module",
    "provider",
    "model_name",
    "success",
    "input_tokens",
    "output_tokens",
    "audio_duration_seconds",
    "request_units",
    "units_type",
    "cost_cny",
    "cost_source",
    "extra_data",
]


admin_ai_billing_bp = Blueprint("admin_ai_billing", __name__, url_prefix="/admin")
user_ai_billing_bp = Blueprint("user_ai_billing", __name__)


@admin_ai_billing_bp.route("/ai-usage")
@login_required
@admin_required
def admin_ai_usage():
    return _render(admin=True)


@admin_ai_billing_bp.route("/ai-usage/export.csv")
@login_required
@admin_required
def export_admin_ai_usage_csv():
    report = _query_report(admin=True, paged=False)
    filename = f"ai-usage-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "text/csv; charset=utf-8",
    }
    return Response(
        stream_with_context(_stream_csv(report["rows"])),
        headers=headers,
    )


@admin_ai_billing_bp.route("/ai-usage/payload/<int:log_id>")
@login_required
@admin_required
def get_ai_usage_payload(log_id: int):
    row = query_one(
        "SELECT request_data, response_data FROM usage_log_payloads WHERE log_id = %s",
        (log_id,),
    )
    if not row:
        return jsonify({"request_data": None, "response_data": None})
    return jsonify({
        "request_data": row["request_data"],
        "response_data": row["response_data"],
    })


@user_ai_billing_bp.route("/my-ai-usage/payload/<int:log_id>")
@login_required
def get_my_ai_usage_payload(log_id: int):
    row = query_one(
        """SELECT p.request_data, p.response_data
           FROM usage_log_payloads p
           JOIN usage_logs ul ON ul.id = p.log_id
           WHERE p.log_id = %s AND ul.user_id = %s""",
        (log_id, current_user.id),
    )
    if not row:
        return jsonify({"request_data": None, "response_data": None})
    return jsonify({
        "request_data": row["request_data"],
        "response_data": row["response_data"],
    })


@user_ai_billing_bp.route("/my-ai-usage")
@login_required
def my_ai_usage():
    return _render(admin=False)


def _render(admin: bool):
    report = _query_report(admin=admin, paged=True)
    return render_template(
        "admin_ai_billing.html",
        summary=report["summary"],
        groups=report["groups"],
        rows=report["rows"],
        filters=report["filters"],
        detail_filters=report["detail_filters"],
        detail_filter_options=report["detail_filter_options"],
        detail_summary=report["detail_summary"],
        group_by=report["group_by"],
        page=report["page"],
        page_size=PAGE_SIZE,
        total_pages=report["total_pages"],
        total_calls=report["summary"]["total_calls"],
        admin_mode=admin,
    )


def _query_report(*, admin: bool, paged: bool) -> dict:
    filters = _parse_filters(admin=admin)
    detail_filters = _parse_detail_filters(admin=admin)
    where_sql, where_args = _build_where_clause(filters=filters, admin=admin)
    detail_where_sql, detail_where_args = _build_detail_where_clause(
        filters=filters,
        detail_filters=detail_filters,
        admin=admin,
    )
    detail_filter_options = _query_detail_filter_options(filters=filters, admin=admin)
    user_display_expr = _user_display_expr()
    group_field, group_alias = GROUP_BY_FIELDS[filters["group_by"]]
    if group_field == "__user_display__":
        group_field = user_display_expr

    summary_sql = f"""
        SELECT
            COALESCE(SUM(ul.cost_cny), 0) AS total_cost_cny,
            COUNT(*) AS total_calls,
            COALESCE(SUM(CASE WHEN ul.cost_source <> 'unknown' AND ul.cost_cny IS NOT NULL THEN 1 ELSE 0 END), 0) AS billed_calls,
            COALESCE(SUM(CASE WHEN ul.cost_source = 'unknown' OR ul.cost_cny IS NULL THEN 1 ELSE 0 END), 0) AS unbilled_calls
        FROM usage_logs ul
        LEFT JOIN users u ON u.id = ul.user_id
        {where_sql}
    """
    summary_rows = query(summary_sql, tuple(where_args))
    summary = summary_rows[0] if summary_rows else {
        "total_cost_cny": Decimal("0"),
        "total_calls": 0,
        "billed_calls": 0,
        "unbilled_calls": 0,
    }

    groups_sql = f"""
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
    """
    groups = query(groups_sql, tuple(where_args))

    detail_summary_sql = f"""
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
    """
    detail_summary_rows = query(detail_summary_sql, tuple(detail_where_args))
    detail_summary = detail_summary_rows[0] if detail_summary_rows else {
        "detail_total_calls": 0,
        "detail_total_cost_cny": Decimal("0"),
        "detail_payload_bytes": 0,
        "payload_recorded_calls": 0,
    }
    detail_summary["detail_payload_mb"] = _format_mb(detail_summary.get("detail_payload_bytes"))

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
        offset = (filters["page"] - 1) * PAGE_SIZE
        rows_sql += " LIMIT %s OFFSET %s"
        row_args.extend([PAGE_SIZE, offset])
    rows = query(rows_sql, tuple(row_args))
    for row in rows:
        row["request_payload_mb"] = _format_mb(row.get("request_payload_bytes"))
        row["response_payload_mb"] = _format_mb(row.get("response_payload_bytes"))

    total_calls = int(detail_summary.get("detail_total_calls") or 0)
    total_pages = max(1, math.ceil(total_calls / PAGE_SIZE)) if paged else 1

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


def _parse_filters(*, admin: bool) -> dict:
    today = date.today().isoformat()
    group_by = (request.args.get("group_by", "module") or "module").strip().lower()
    if group_by not in GROUP_BY_FIELDS:
        group_by = "module"
    if not admin and group_by == "user":
        group_by = "module"

    return {
        "date_from": (request.args.get("from") or today).strip(),
        "date_to": (request.args.get("to") or today).strip(),
        "user_id": _parse_user_id(request.args.get("user_id")) if admin else current_user.id,
        "module": (request.args.get("module") or "").strip(),
        "use_case": (request.args.get("use_case") or "").strip(),
        "provider": (request.args.get("provider") or "").strip(),
        "model": (request.args.get("model") or "").strip(),
        "status": _parse_status(request.args.get("status")),
        "q": (request.args.get("q") or "").strip(),
        "group_by": group_by,
        "page": _parse_page(request.args.get("page")),
    }


def _parse_detail_filters(*, admin: bool) -> dict:
    return {
        "user_ids": _parse_user_ids(request.args.getlist("detail_user_id")) if admin else [current_user.id],
        "modules": _parse_text_values(request.args.getlist("detail_module")),
        "use_cases": _parse_text_values(request.args.getlist("detail_use_case")),
        "providers": _parse_text_values(request.args.getlist("detail_provider")),
        "statuses": _parse_status_values(request.args.getlist("detail_status")),
    }


def _parse_page(raw: str | None) -> int:
    try:
        page = int(raw or "1")
    except (TypeError, ValueError):
        return 1
    return max(1, page)


def _parse_user_id(raw: str | None) -> int | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        user_id = int(value)
    except (TypeError, ValueError):
        return -1
    return user_id if user_id > 0 else -1


def _parse_user_ids(raw_values: list[str]) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for raw in raw_values:
        user_id = _parse_user_id(raw)
        if user_id is None or user_id in seen:
            continue
        ids.append(user_id)
        seen.add(user_id)
    return ids


def _parse_status(raw: str | None) -> bool | None:
    value = (raw or "").strip().lower()
    if value in {"success", "1", "true"}:
        return True
    if value in {"failed", "0", "false"}:
        return False
    return None


def _parse_status_values(raw_values: list[str]) -> list[bool]:
    statuses: list[bool] = []
    for raw in raw_values:
        status = _parse_status(raw)
        if status is None or status in statuses:
            continue
        statuses.append(status)
    return statuses


def _parse_text_values(raw_values: list[str]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        value = (raw or "").strip()
        if not value or value in seen:
            continue
        values.append(value)
        seen.add(value)
    return values


def _build_where_clause(*, filters: dict, admin: bool) -> tuple[str, list]:
    clauses, args = _build_clause_parts(filters=filters, admin=admin)
    if not clauses:
        return "", args
    return "WHERE " + " AND ".join(clauses), args


def _build_detail_where_clause(*, filters: dict, detail_filters: dict, admin: bool) -> tuple[str, list]:
    clauses, args = _build_clause_parts(filters=filters, admin=admin)

    if admin:
        _append_in_clause(clauses, args, "ul.user_id", detail_filters["user_ids"])

    _append_in_clause(clauses, args, "ul.module", detail_filters["modules"])
    _append_in_clause(clauses, args, "ul.use_case_code", detail_filters["use_cases"])
    _append_in_clause(clauses, args, "ul.provider", detail_filters["providers"])
    _append_in_clause(clauses, args, "ul.success", [1 if status else 0 for status in detail_filters["statuses"]])

    if not clauses:
        return "", args
    return "WHERE " + " AND ".join(clauses), args


def _append_in_clause(clauses: list[str], args: list, field: str, values: list) -> None:
    if not values:
        return
    placeholders = ", ".join(["%s"] * len(values))
    clauses.append(f"{field} IN ({placeholders})")
    args.extend(values)


def _build_clause_parts(*, filters: dict, admin: bool) -> tuple[list[str], list]:
    clauses: list[str] = []
    args: list = []

    if admin:
        if filters["user_id"] is not None:
            clauses.append("ul.user_id = %s")
            args.append(filters["user_id"])
    else:
        clauses.append("ul.user_id = %s")
        args.append(current_user.id)

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


def _query_detail_filter_options(*, filters: dict, admin: bool) -> dict:
    clauses, args = _build_clause_parts(filters=filters, admin=admin)
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    return {
        "statuses": [
            {"value": "success", "label": "成功"},
            {"value": "failed", "label": "失败"},
        ],
        "modules": _query_distinct_options("ul.module", where_sql, args),
        "use_cases": _query_distinct_options("ul.use_case_code", where_sql, args),
        "providers": _query_distinct_options("ul.provider", where_sql, args),
        "users": _query_user_options(where_sql, args) if admin else [],
    }


def _query_distinct_options(field: str, where_sql: str, args: list) -> list[dict]:
    rows = query(
        f"""
        SELECT DISTINCT {field} AS value
        FROM usage_logs ul
        LEFT JOIN users u ON u.id = ul.user_id
        {_where_with_extra(where_sql, f"{field} IS NOT NULL AND {field} <> ''")}
        ORDER BY value ASC
        """,
        tuple(args),
    )
    return [{"value": row["value"], "label": row["value"]} for row in rows]


def _query_user_options(where_sql: str, args: list) -> list[dict]:
    user_display_expr = _user_display_expr()
    rows = query(
        f"""
        SELECT DISTINCT ul.user_id AS value, {user_display_expr} AS label
        FROM usage_logs ul
        LEFT JOIN users u ON u.id = ul.user_id
        {_where_with_extra(where_sql, "ul.user_id IS NOT NULL")}
        ORDER BY label ASC, value ASC
        """,
        tuple(args),
    )
    return [
        {"value": int(row["value"]), "label": (row.get("label") or f"用户 {row['value']}")}
        for row in rows
    ]


def _user_display_expr() -> str:
    return medias._media_product_owner_name_expr()


def _where_with_extra(where_sql: str, extra_condition: str) -> str:
    if where_sql:
        return f"{where_sql} AND {extra_condition}"
    return f"WHERE {extra_condition}"


def _format_mb(raw_bytes) -> str | None:
    if raw_bytes is None:
        return None
    try:
        size = int(raw_bytes)
    except (TypeError, ValueError):
        return None
    if size <= 0:
        return None
    return f"{size / 1024 / 1024:.2f} MB"


def _stream_csv(rows: list[dict]):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    for row in rows:
        writer.writerow([_csv_cell(row.get(column)) for column in CSV_COLUMNS])
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


def _csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value, ensure_ascii=False)
    return str(value)
