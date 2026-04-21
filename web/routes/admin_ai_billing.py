from __future__ import annotations

import csv
import io
import math
from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, Response, render_template, request, stream_with_context
from flask_login import current_user, login_required

from appcore.db import query
from web.auth import admin_required


PAGE_SIZE = 50

GROUP_BY_FIELDS = {
    "module": ("ul.module", "group_value"),
    "use_case": ("ul.use_case_code", "group_value"),
    "provider": ("ul.provider", "group_value"),
    "model": ("ul.model_name", "group_value"),
    "user": ("u.username", "group_value"),
}

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
        group_by=report["group_by"],
        page=report["page"],
        page_size=PAGE_SIZE,
        total_pages=report["total_pages"],
        total_calls=report["summary"]["total_calls"],
        admin_mode=admin,
    )


def _query_report(*, admin: bool, paged: bool) -> dict:
    filters = _parse_filters(admin=admin)
    where_sql, where_args = _build_where_clause(filters=filters, admin=admin)
    group_field, group_alias = GROUP_BY_FIELDS[filters["group_by"]]

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

    rows_sql = f"""
        SELECT
            ul.id,
            ul.called_at,
            ul.user_id,
            u.username,
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
            ul.extra_data
        FROM usage_logs ul
        LEFT JOIN users u ON u.id = ul.user_id
        {where_sql}
        ORDER BY ul.called_at DESC, ul.id DESC
    """
    row_args = list(where_args)
    if paged:
        offset = (filters["page"] - 1) * PAGE_SIZE
        rows_sql += " LIMIT %s OFFSET %s"
        row_args.extend([PAGE_SIZE, offset])
    rows = query(rows_sql, tuple(row_args))

    total_calls = int(summary.get("total_calls") or 0)
    total_pages = max(1, math.ceil(total_calls / PAGE_SIZE)) if paged else 1

    return {
        "summary": summary,
        "groups": groups,
        "rows": rows,
        "filters": filters,
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


def _parse_status(raw: str | None) -> bool | None:
    value = (raw or "").strip().lower()
    if value in {"success", "1", "true"}:
        return True
    if value in {"failed", "0", "false"}:
        return False
    return None


def _build_where_clause(*, filters: dict, admin: bool) -> tuple[str, list]:
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

    if not clauses:
        return "", args
    return "WHERE " + " AND ".join(clauses), args


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
