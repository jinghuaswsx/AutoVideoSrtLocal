"""Shopify Payments unsettled payout project archive.

Docs-anchor: docs/superpowers/specs/2026-06-10-shopify-unsettled-payout-ledger-design.md
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


REQUIRED_COLUMNS = ("Payout Status", "Amount", "Fee", "Net")
SUPPORTED_EXTENSIONS = (".csv", ".xls", ".xlsx")

STATUS_META = {
    "pending": {
        "label": "未结算订单",
        "net_label": "预计打款总额",
    },
    "paid": {
        "label": "已结算订单",
        "net_label": "已打款总额",
    },
    "scheduled": {
        "label": "已排期订单",
        "net_label": "已排期打款总额",
    },
}


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def query_one(*args, **kwargs):
    return _facade().query_one(*args, **kwargs)


def execute(*args, **kwargs):
    return _facade().execute(*args, **kwargs)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value).strip()


def _decode_csv_bytes(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return content.decode("gbk")
        except UnicodeDecodeError as exc:
            raise ValueError("文件编码必须是 UTF-8 或 GBK") from exc


def _parse_csv(content: bytes) -> list[dict[str, str]]:
    text = _decode_csv_bytes(content)
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append({
            str(key or "").strip(): _cell_text(value)
            for key, value in row.items()
            if key is not None and str(key).strip()
        })
    return rows


def _parse_excel(content: bytes) -> list[dict[str, str]]:
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("服务器未安装 openpyxl，无法解析 Excel 文件") from exc

    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        headers = next(rows_iter, None)
        if not headers:
            return []
        header_names = [_cell_text(value) for value in headers]
        out: list[dict[str, str]] = []
        for values in rows_iter:
            item: dict[str, str] = {}
            for idx, value in enumerate(values):
                if idx >= len(header_names):
                    continue
                header = header_names[idx]
                if not header:
                    continue
                item[header] = _cell_text(value)
            out.append(item)
        return out
    finally:
        wb.close()


def _decimal_to_float(value: Decimal | int | float | None) -> float:
    if value is None:
        return 0.0
    return float(Decimal(str(value)).quantize(Decimal("0.0001")))


def _decimal_to_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal_to_float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_decimal_to_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _decimal_to_json(item) for key, item in value.items()}
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_decimal_to_json(value), ensure_ascii=False, sort_keys=True, default=str)


def _loads_json(value: Any, default):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _money_decimal(value: Any, *, row_number: int, column: str) -> Decimal:
    if value is None:
        return Decimal("0")
    text = str(value).strip()
    if not text or text in {"-", "—"}:
        return Decimal("0")

    negative_parentheses = text.startswith("(") and text.endswith(")")
    if negative_parentheses:
        text = text[1:-1]
    text = text.replace(",", "")
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if cleaned in {"", "-", ".", "-."}:
        return Decimal("0")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"第 {row_number} 行 {column} 金额格式无效：{value}") from exc
    if negative_parentheses and amount > 0:
        amount = -amount
    return amount


def _empty_bucket(status: str) -> dict[str, Any]:
    meta = STATUS_META[status]
    return {
        "status": status,
        "label": meta["label"],
        "net_label": meta["net_label"],
        "order_count": 0,
        "amount_total": Decimal("0"),
        "fee_total": Decimal("0"),
        "net_total": Decimal("0"),
    }


def _validate_required_columns(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        raise ValueError("文件为空或格式不正确")
    columns = set(rows[0].keys())
    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    if missing:
        raise ValueError("缺少必需列：" + ", ".join(missing))
    return list(rows[0].keys())


def parse_payout_file(content: bytes, filename: str) -> dict[str, Any]:
    """Parse a Shopify Payments export and return rows plus status summary."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".csv":
        raw_rows = _parse_csv(content)
    elif ext in {".xls", ".xlsx"}:
        raw_rows = _parse_excel(content)
    else:
        raise ValueError("仅支持 CSV / Excel (.xlsx) 文件")

    columns = _validate_required_columns(raw_rows)
    buckets = {status: _empty_bucket(status) for status in STATUS_META}
    normalized_rows: list[dict[str, Any]] = []
    currency_breakdown: dict[str, dict[str, Any]] = {}
    included_row_count = 0
    ignored_row_count = 0

    for idx, raw in enumerate(raw_rows, start=2):
        norm = {str(key or "").strip(): _cell_text(value) for key, value in raw.items()}
        status_raw = norm.get("Payout Status", "")
        status = status_raw.strip().lower()
        amount = _money_decimal(norm.get("Amount"), row_number=idx, column="Amount")
        fee = _money_decimal(norm.get("Fee"), row_number=idx, column="Fee")
        net = _money_decimal(norm.get("Net"), row_number=idx, column="Net")
        currency = (norm.get("Currency") or "USD").strip().upper() or "USD"

        currency_info = currency_breakdown.setdefault(
            currency,
            {"currency": currency, "row_count": 0, "amount_total": Decimal("0"), "net_total": Decimal("0")},
        )
        currency_info["row_count"] += 1
        currency_info["amount_total"] += amount
        currency_info["net_total"] += net

        if status in buckets:
            bucket = buckets[status]
            bucket["order_count"] += 1
            bucket["amount_total"] += amount
            bucket["fee_total"] += fee
            bucket["net_total"] += net
            included_row_count += 1
        else:
            ignored_row_count += 1

        normalized_rows.append({
            "row_number": idx,
            "payout_status": status,
            "payout_status_raw": status_raw,
            "transaction_date": norm.get("Transaction Date", ""),
            "transaction_type": (norm.get("Type") or "").strip().lower(),
            "order_name": norm.get("Order", ""),
            "payout_date": norm.get("Payout Date", ""),
            "payout_id": norm.get("Payout ID", ""),
            "available_on": norm.get("Available On", ""),
            "currency": currency,
            "amount": amount,
            "fee": fee,
            "net": net,
            "raw_row": norm,
        })

    currencies = sorted(currency_breakdown)
    display_currency = currencies[0] if len(currencies) == 1 else ("MIXED" if currencies else "USD")
    return {
        "columns": columns,
        "rows": normalized_rows,
        "summary": {
            "total_rows": len(raw_rows),
            "included_row_count": included_row_count,
            "ignored_row_count": ignored_row_count,
            "currency": display_currency,
            "currency_breakdown": [
                currency_breakdown[key] for key in sorted(currency_breakdown)
            ],
            "buckets": buckets,
        },
    }


def _bucket_db_values(summary: dict[str, Any], status: str) -> tuple[int, Decimal, Decimal, Decimal]:
    bucket = (summary.get("buckets") or {}).get(status) or _empty_bucket(status)
    return (
        int(bucket.get("order_count") or 0),
        Decimal(str(bucket.get("amount_total") or 0)),
        Decimal(str(bucket.get("fee_total") or 0)),
        Decimal(str(bucket.get("net_total") or 0)),
    )


def _project_summary_from_row(row: dict[str, Any]) -> dict[str, Any]:
    buckets: dict[str, Any] = {}
    for status in STATUS_META:
        buckets[status] = {
            "status": status,
            "label": STATUS_META[status]["label"],
            "net_label": STATUS_META[status]["net_label"],
            "order_count": int(row.get(f"{status}_order_count") or 0),
            "amount_total": _decimal_to_float(row.get(f"{status}_amount_total") or 0),
            "fee_total": _decimal_to_float(row.get(f"{status}_fee_total") or 0),
            "net_total": _decimal_to_float(row.get(f"{status}_net_total") or 0),
        }
    return {
        "total_rows": int(row.get("imported_row_count") or 0),
        "included_row_count": int(row.get("included_row_count") or 0),
        "ignored_row_count": int(row.get("ignored_row_count") or 0),
        "currency": row.get("currency") or "USD",
        "currency_breakdown": _loads_json(row.get("currency_breakdown_json"), []),
        "buckets": buckets,
    }


def project_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "store_code": row.get("store_code") or "",
        "project_name": row.get("project_name") or "",
        "source_filename": row.get("source_filename") or "",
        "currency": row.get("currency") or "USD",
        "imported_row_count": int(row.get("imported_row_count") or 0),
        "included_row_count": int(row.get("included_row_count") or 0),
        "ignored_row_count": int(row.get("ignored_row_count") or 0),
        "imported_by": row.get("imported_by"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "summary": _project_summary_from_row(row),
    }


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "project_id": int(row["project_id"]),
        "row_number": int(row.get("source_row_number") or row.get("row_number") or 0),
        "payout_status": row.get("payout_status") or "",
        "payout_status_raw": row.get("payout_status_raw") or "",
        "transaction_date": row.get("transaction_date") or "",
        "transaction_type": row.get("transaction_type") or "",
        "order_name": row.get("order_name") or "",
        "payout_date": row.get("payout_date") or "",
        "payout_id": row.get("payout_id") or "",
        "available_on": row.get("available_on") or "",
        "currency": row.get("currency") or "USD",
        "amount": _decimal_to_float(row.get("amount") or 0),
        "fee": _decimal_to_float(row.get("fee") or 0),
        "net": _decimal_to_float(row.get("net") or 0),
        "raw_row": _loads_json(row.get("raw_row_json"), {}),
    }


def create_project_from_file(
    *,
    store_code: str,
    project_name: str,
    filename: str,
    content: bytes,
    imported_by: int | None = None,
) -> dict[str, Any]:
    parsed = parse_payout_file(content, filename)
    summary = parsed["summary"]
    project_name = (project_name or "").strip() or os.path.splitext(filename)[0] or "Shopify Payments 导入"
    pending = _bucket_db_values(summary, "pending")
    paid = _bucket_db_values(summary, "paid")
    scheduled = _bucket_db_values(summary, "scheduled")

    project_id = execute(
        "INSERT INTO shopify_unsettled_payout_projects ("
        "  store_code, project_name, source_filename, source_file_ext, currency, "
        "  currency_breakdown_json, imported_row_count, included_row_count, ignored_row_count, "
        "  pending_order_count, pending_amount_total, pending_fee_total, pending_net_total, "
        "  paid_order_count, paid_amount_total, paid_fee_total, paid_net_total, "
        "  scheduled_order_count, scheduled_amount_total, scheduled_fee_total, scheduled_net_total, "
        "  imported_by, summary_json, columns_json"
        ") VALUES ("
        "  %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "  %s, %s, %s"
        ")",
        (
            store_code,
            project_name[:180],
            filename[:255],
            os.path.splitext(filename)[1].lower()[:16],
            summary["currency"],
            _json_dumps(summary["currency_breakdown"]),
            int(summary["total_rows"]),
            int(summary["included_row_count"]),
            int(summary["ignored_row_count"]),
            *pending,
            *paid,
            *scheduled,
            imported_by,
            _json_dumps(summary),
            _json_dumps(parsed["columns"]),
        ),
    )

    insert_row_sql = (
        "INSERT INTO shopify_unsettled_payout_rows ("
        "  project_id, source_row_number, payout_status, payout_status_raw, transaction_date, "
        "  transaction_type, order_name, payout_date, payout_id, available_on, "
        "  currency, amount, fee, net, raw_row_json"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    for item in parsed["rows"]:
        execute(
            insert_row_sql,
            (
                project_id,
                item["row_number"],
                item["payout_status"][:32],
                item["payout_status_raw"][:64],
                item["transaction_date"][:64],
                item["transaction_type"][:32],
                item["order_name"][:64],
                item["payout_date"][:64],
                item["payout_id"][:64],
                item["available_on"][:64],
                item["currency"][:16],
                item["amount"],
                item["fee"],
                item["net"],
                _json_dumps(item["raw_row"]),
            ),
        )

    project = get_project(project_id)
    if not project:
        raise RuntimeError("project insert failed")
    return project


def list_projects(*, limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(int(limit or 100), 200))
    rows = query(
        "SELECT * FROM shopify_unsettled_payout_projects "
        "ORDER BY created_at DESC, id DESC LIMIT %s",
        (limit,),
    )
    projects = [project_to_dict(row) for row in rows]
    aggregate = {status: _empty_bucket(status) for status in STATUS_META}
    for project in projects:
        buckets = (project.get("summary") or {}).get("buckets") or {}
        for status in STATUS_META:
            bucket = buckets.get(status) or {}
            aggregate[status]["order_count"] += int(bucket.get("order_count") or 0)
            aggregate[status]["amount_total"] += Decimal(str(bucket.get("amount_total") or 0))
            aggregate[status]["fee_total"] += Decimal(str(bucket.get("fee_total") or 0))
            aggregate[status]["net_total"] += Decimal(str(bucket.get("net_total") or 0))
    return {
        "projects": projects,
        "summary": {
            "project_count": len(projects),
            "buckets": _decimal_to_json(aggregate),
        },
    }


def get_project(project_id: int) -> dict[str, Any] | None:
    row = query_one(
        "SELECT * FROM shopify_unsettled_payout_projects WHERE id = %s",
        (int(project_id),),
    )
    return project_to_dict(row) if row else None


def get_project_detail(
    project_id: int,
    *,
    status: str | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any] | None:
    project = get_project(project_id)
    if not project:
        return None

    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 100), 500))
    offset = (page - 1) * page_size
    normalized_status = (status or "").strip().lower()
    where = "project_id = %s"
    params: list[Any] = [int(project_id)]
    if normalized_status and normalized_status != "all":
        where += " AND payout_status = %s"
        params.append(normalized_status)

    total_row = query_one(
        f"SELECT COUNT(*) AS total FROM shopify_unsettled_payout_rows WHERE {where}",
        tuple(params),
    ) or {"total": 0}
    total = int(total_row.get("total") or 0)
    rows = query(
        f"SELECT * FROM shopify_unsettled_payout_rows WHERE {where} "
        "ORDER BY source_row_number ASC, id ASC LIMIT %s OFFSET %s",
        tuple([*params, page_size, offset]),
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return {
        "project": project,
        "rows": [_row_to_dict(row) for row in rows],
        "page": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
        },
    }
