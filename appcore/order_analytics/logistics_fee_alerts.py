"""Logistics fee alert queries for order analytics.

Docs-anchor:
docs/superpowers/specs/2026-06-12-logistics-fee-alert-dashboard-design.md
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable

from appcore.db import query


DEFAULT_THRESHOLD_PCT = Decimal("20")
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


def _parse_date(value: Any, name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD") from exc


def _normalize_threshold(value: Any = None) -> Decimal:
    if value is None or str(value).strip() == "":
        return DEFAULT_THRESHOLD_PCT
    try:
        threshold = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("threshold_pct must be numeric") from exc
    if threshold <= 0:
        raise ValueError("threshold_pct must be positive")
    return threshold


def _normalize_positive_int(value: Any, default: int, *, max_value: int | None = None) -> int:
    if value is None or str(value).strip() == "":
        result = default
    else:
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("page and page_size must be positive integers") from exc
    if result <= 0:
        raise ValueError("page and page_size must be positive integers")
    if max_value is not None:
        result = min(result, max_value)
    return result


def _q2(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _q4(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _decode_json(value: Any, fallback):
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _shipping_source(row: dict[str, Any]) -> str:
    cost_basis = _decode_json(row.get("cost_basis"), {})
    missing_fields = _decode_json(row.get("missing_fields"), [])
    estimated_fields = cost_basis.get("estimated_fields") or []
    estimated_names = set(str(item) for item in estimated_fields)
    missing_names = set(str(item) for item in missing_fields)
    if estimated_names.intersection({"shipping_cost", "packet_cost"}) or missing_names.intersection(
        {"shipping_cost", "packet_cost"}
    ):
        return "estimated"
    return str(cost_basis.get("shipping_cost_source") or "unknown")


def _format_alert_row(row: dict[str, Any]) -> dict[str, Any] | None:
    revenue = _to_decimal(row.get("revenue_usd"))
    shipping = _to_decimal(row.get("shipping_cost_usd"))
    if revenue <= 0 or shipping <= 0:
        return None
    ratio = shipping / revenue * Decimal("100")
    return {
        "business_date": row.get("business_date"),
        "paid_at": row.get("paid_at"),
        "site_code": row.get("site_code") or "",
        "dxm_order_line_id": int(row.get("dxm_order_line_id") or 0),
        "dxm_package_id": row.get("dxm_package_id") or "",
        "dxm_order_id": row.get("dxm_order_id") or "",
        "package_number": row.get("package_number") or "",
        "product_id": int(row.get("product_id") or 0),
        "product_code": row.get("product_code") or "",
        "product_name": row.get("product_name") or "",
        "sku": row.get("sku") or "",
        "quantity": int(row.get("quantity") or 0),
        "revenue_usd": _q2(revenue),
        "shipping_cost_usd": _q4(shipping),
        "shipping_ratio_pct": _q2(ratio),
        "shipping_source": _shipping_source(row),
        "status": row.get("status") or "",
    }


def _fetch_alert_rows(
    *,
    start_date: date,
    end_date: date,
    threshold_pct: Decimal,
    product_id: int | None = None,
    query_fn: Callable[..., list[dict[str, Any]]] = query,
) -> list[dict[str, Any]]:
    where = [
        "p.business_date BETWEEN %s AND %s",
        "p.product_id IS NOT NULL",
        "p.revenue_usd > 0",
        "p.shipping_cost_usd > 0",
        "(p.shipping_cost_usd / p.revenue_usd * 100) >= %s",
    ]
    args: list[Any] = [start_date, end_date, threshold_pct]
    if product_id is not None:
        where.append("p.product_id = %s")
        args.append(int(product_id))
    sql = (
        "SELECT p.business_date, p.paid_at, d.site_code, p.dxm_order_line_id, "
        "       d.dxm_package_id, d.dxm_order_id, d.package_number, "
        "       p.product_id, mp.product_code, mp.name AS product_name, "
        "       COALESCE(d.product_sku, d.product_display_sku) AS sku, d.quantity, "
        "       p.revenue_usd, p.shipping_cost_usd, p.status, "
        "       p.missing_fields, p.cost_basis "
        "FROM order_profit_lines p "
        "LEFT JOIN dianxiaomi_order_lines d ON d.id = p.dxm_order_line_id "
        "LEFT JOIN media_products mp ON mp.id = p.product_id "
        "WHERE " + " AND ".join(where) + " "
        "ORDER BY (p.shipping_cost_usd / p.revenue_usd) DESC, p.business_date DESC, p.dxm_order_line_id DESC"
    )
    rows = []
    for row in query_fn(sql, tuple(args)) or []:
        formatted = _format_alert_row(row)
        if formatted is None:
            continue
        if product_id is not None and formatted["product_id"] != int(product_id):
            continue
        if formatted["shipping_ratio_pct"] >= float(threshold_pct):
            rows.append(formatted)
    return rows


def _page(rows: list[dict[str, Any]], page: int, page_size: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    total = len(rows)
    pages = (total + page_size - 1) // page_size if total else 0
    start = (page - 1) * page_size
    return rows[start:start + page_size], {
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
    }


def list_product_alerts(
    *,
    start_date: Any,
    end_date: Any,
    threshold_pct: Any = DEFAULT_THRESHOLD_PCT,
    page: Any = 1,
    page_size: Any = DEFAULT_PAGE_SIZE,
    query_fn: Callable[..., list[dict[str, Any]]] = query,
) -> dict[str, Any]:
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    if start > end:
        raise ValueError("start_date must be <= end_date")
    threshold = _normalize_threshold(threshold_pct)
    page_num = _normalize_positive_int(page, 1)
    page_len = _normalize_positive_int(page_size, DEFAULT_PAGE_SIZE, max_value=MAX_PAGE_SIZE)
    rows = _fetch_alert_rows(
        start_date=start,
        end_date=end,
        threshold_pct=threshold,
        query_fn=query_fn,
    )
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        product_id = int(row["product_id"])
        item = grouped.setdefault(
            product_id,
            {
                "product_id": product_id,
                "product_code": row["product_code"],
                "product_name": row["product_name"],
                "alert_line_count": 0,
                "package_ids": set(),
                "revenue_usd_decimal": Decimal("0"),
                "shipping_cost_usd_decimal": Decimal("0"),
                "max_shipping_ratio_pct": Decimal("0"),
                "shipping_source_counts": {},
            },
        )
        revenue = _to_decimal(row["revenue_usd"])
        shipping = _to_decimal(row["shipping_cost_usd"])
        item["alert_line_count"] += 1
        if row.get("dxm_package_id"):
            item["package_ids"].add(row["dxm_package_id"])
        item["revenue_usd_decimal"] += revenue
        item["shipping_cost_usd_decimal"] += shipping
        item["max_shipping_ratio_pct"] = max(
            item["max_shipping_ratio_pct"],
            _to_decimal(row["shipping_ratio_pct"]),
        )
        source_counts = item["shipping_source_counts"]
        source = row["shipping_source"]
        source_counts[source] = int(source_counts.get(source, 0)) + 1

    products = []
    total_revenue = Decimal("0")
    total_shipping = Decimal("0")
    for item in grouped.values():
        revenue = item.pop("revenue_usd_decimal")
        shipping = item.pop("shipping_cost_usd_decimal")
        total_revenue += revenue
        total_shipping += shipping
        ratio = shipping / revenue * Decimal("100") if revenue > 0 else Decimal("0")
        item["package_count"] = len(item.pop("package_ids"))
        item["revenue_usd"] = _q2(revenue)
        item["shipping_cost_usd"] = _q4(shipping)
        item["shipping_ratio_pct"] = _q2(ratio)
        item["max_shipping_ratio_pct"] = _q2(item["max_shipping_ratio_pct"])
        products.append(item)
    products.sort(
        key=lambda item: (
            item["shipping_ratio_pct"],
            item["shipping_cost_usd"],
            item["alert_line_count"],
        ),
        reverse=True,
    )
    paged_products, page_info = _page(products, page_num, page_len)
    return {
        "period": {"start_date": start.isoformat(), "end_date": end.isoformat()},
        "threshold_pct": float(threshold),
        "summary": {
            "product_count": len(products),
            "alert_line_count": len(rows),
            "revenue_usd": _q2(total_revenue),
            "shipping_cost_usd": _q4(total_shipping),
            "shipping_ratio_pct": _q2(total_shipping / total_revenue * Decimal("100")) if total_revenue > 0 else None,
        },
        "products": paged_products,
        "page": page_info,
    }


def list_product_order_alerts(
    *,
    product_id: int,
    start_date: Any,
    end_date: Any,
    threshold_pct: Any = DEFAULT_THRESHOLD_PCT,
    page: Any = 1,
    page_size: Any = DEFAULT_PAGE_SIZE,
    query_fn: Callable[..., list[dict[str, Any]]] = query,
) -> dict[str, Any]:
    if int(product_id) <= 0:
        raise ValueError("product_id must be positive")
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    if start > end:
        raise ValueError("start_date must be <= end_date")
    threshold = _normalize_threshold(threshold_pct)
    page_num = _normalize_positive_int(page, 1)
    page_len = _normalize_positive_int(page_size, DEFAULT_PAGE_SIZE, max_value=MAX_PAGE_SIZE)
    rows = _fetch_alert_rows(
        start_date=start,
        end_date=end,
        threshold_pct=threshold,
        product_id=int(product_id),
        query_fn=query_fn,
    )
    rows.sort(
        key=lambda row: (
            row["shipping_ratio_pct"],
            row["shipping_cost_usd"],
            row["business_date"],
            row["dxm_order_line_id"],
        ),
        reverse=True,
    )
    paged_rows, page_info = _page(rows, page_num, page_len)
    total_revenue = sum((_to_decimal(row["revenue_usd"]) for row in rows), Decimal("0"))
    total_shipping = sum((_to_decimal(row["shipping_cost_usd"]) for row in rows), Decimal("0"))
    first = rows[0] if rows else {}
    return {
        "period": {"start_date": start.isoformat(), "end_date": end.isoformat()},
        "threshold_pct": float(threshold),
        "product": {
            "product_id": int(product_id),
            "product_code": first.get("product_code") or "",
            "product_name": first.get("product_name") or "",
        },
        "summary": {
            "alert_line_count": len(rows),
            "package_count": len({row["dxm_package_id"] for row in rows if row.get("dxm_package_id")}),
            "revenue_usd": _q2(total_revenue),
            "shipping_cost_usd": _q4(total_shipping),
            "shipping_ratio_pct": _q2(total_shipping / total_revenue * Decimal("100")) if total_revenue > 0 else None,
        },
        "orders": paged_rows,
        "page": page_info,
    }
