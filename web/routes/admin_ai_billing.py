from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, Response, render_template, request, stream_with_context
from flask_login import current_user, login_required

from appcore import usage_log
from web.auth import admin_required
from web.services.admin_ai_billing import (
    admin_ai_billing_flask_response,
    build_ai_usage_payload_response,
)


PAGE_SIZE = 50

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
    row = usage_log.get_usage_payload(log_id)
    return admin_ai_billing_flask_response(build_ai_usage_payload_response(row))


@user_ai_billing_bp.route("/my-ai-usage/payload/<int:log_id>")
@login_required
def get_my_ai_usage_payload(log_id: int):
    row = usage_log.get_user_usage_payload(log_id, user_id=current_user.id)
    return admin_ai_billing_flask_response(build_ai_usage_payload_response(row))


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
    return usage_log.get_ai_usage_report(
        filters=filters,
        detail_filters=detail_filters,
        admin=admin,
        paged=paged,
        page_size=PAGE_SIZE,
    )


def _parse_filters(*, admin: bool) -> dict:
    today = date.today().isoformat()
    group_by = usage_log.normalize_ai_usage_group_by(
        request.args.get("group_by", "module"),
        admin=admin,
    )

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
