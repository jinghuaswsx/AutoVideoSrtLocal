from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from appcore.product_roas import get_configured_rmb_per_usd


ESTIMATED_FEE_RATE = Decimal("0.07")


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def execute(*args, **kwargs):
    return _facade().execute(*args, **kwargs)


def calculate_window(
    run_date: date,
    *,
    window_days: int = 30,
    settlement_delay_days: int = 2,
) -> tuple[date, date]:
    window_end = run_date - timedelta(days=int(settlement_delay_days))
    window_start = window_end - timedelta(days=int(window_days) - 1)
    return window_start, window_end


def _decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def _positive_decimal(*values: Any) -> Decimal:
    for value in values:
        candidate = _decimal(value)
        if candidate > 0:
            return candidate
    return Decimal("0")


def _q4(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _fee_source(real_count: int, estimated_count: int) -> str:
    if real_count and estimated_count:
        return "mixed"
    if real_count:
        return "real"
    return "estimated_7pct"


def _allocate_shipping_to_line(
    *,
    line_amount: Decimal,
    order_total_line_amount: Decimal,
    order_shipping_usd: Decimal,
) -> Decimal:
    if order_shipping_usd <= 0 or line_amount <= 0 or order_total_line_amount <= 0:
        return Decimal("0")
    return order_shipping_usd * (line_amount / order_total_line_amount)


def aggregate_sku_rows(
    rows: list[dict[str, Any]],
    real_fees_by_order: dict[str, Any],
    *,
    rmb_per_usd: Any | None = None,
) -> dict[str, dict[str, Any]]:
    rate = _decimal(rmb_per_usd if rmb_per_usd is not None else get_configured_rmb_per_usd())
    order_line_totals: dict[str, Decimal] = defaultdict(Decimal)
    order_shipping: dict[str, Decimal] = {}
    order_revenue: dict[str, Decimal] = defaultdict(Decimal)

    for row in rows:
        package_id = str(row.get("dxm_package_id") or "")
        line_amount = _decimal(row.get("line_amount"))
        order_line_totals[package_id] += line_amount
        order_shipping.setdefault(package_id, _decimal(row.get("ship_amount")))

    for package_id, line_total in order_line_totals.items():
        order_revenue[package_id] = line_total + order_shipping.get(package_id, Decimal("0"))

    buckets: dict[str, dict[str, Any]] = {}
    order_sets: dict[str, set[str]] = defaultdict(set)
    fee_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"real": 0, "estimated": 0})

    for row in rows:
        sku = str(row.get("product_display_sku") or "").strip()
        if not sku:
            continue
        package_id = str(row.get("dxm_package_id") or "")
        order_name = str(row.get("extended_order_id") or "").strip()
        line_amount = _decimal(row.get("line_amount"))
        quantity = int(row.get("quantity") or 0)
        shipping_alloc = _allocate_shipping_to_line(
            line_amount=line_amount,
            order_total_line_amount=order_line_totals.get(package_id, Decimal("0")),
            order_shipping_usd=order_shipping.get(package_id, Decimal("0")),
        )
        revenue = line_amount + shipping_alloc
        purchase_cny = _positive_decimal(
            row.get("purchase_price_cny"),
            row.get("xmyc_unit_price"),
            row.get("product_purchase_price"),
        )
        purchase_usd = (purchase_cny * Decimal(quantity)) / rate if rate > 0 else Decimal("0")
        logistic_fee = _decimal(row.get("logistic_fee"))
        shipping_cost_cny = Decimal("0")
        order_total = order_line_totals.get(package_id, Decimal("0"))
        if logistic_fee > 0 and order_total > 0:
            shipping_cost_cny = logistic_fee * (line_amount / order_total)
        shipping_usd = shipping_cost_cny / rate if rate > 0 else Decimal("0")

        if order_name in real_fees_by_order and order_revenue.get(package_id, Decimal("0")) > 0:
            fee = _decimal(real_fees_by_order[order_name]) * (revenue / order_revenue[package_id])
            fee_counts[sku]["real"] += 1
        else:
            fee = revenue * ESTIMATED_FEE_RATE
            fee_counts[sku]["estimated"] += 1

        bucket = buckets.setdefault(sku, {
            "sku": sku,
            "units": 0,
            "revenue_usd": Decimal("0"),
            "purchase_cost_usd": Decimal("0"),
            "shipping_cost_usd": Decimal("0"),
            "shopify_fee_usd": Decimal("0"),
        })
        order_sets[sku].add(package_id)
        bucket["units"] += quantity
        bucket["revenue_usd"] += revenue
        bucket["purchase_cost_usd"] += purchase_usd
        bucket["shipping_cost_usd"] += shipping_usd
        bucket["shopify_fee_usd"] += fee

    out: dict[str, dict[str, Any]] = {}
    for sku, bucket in buckets.items():
        revenue = bucket["revenue_usd"]
        costs = bucket["purchase_cost_usd"] + bucket["shipping_cost_usd"] + bucket["shopify_fee_usd"]
        available = revenue - costs
        roas = revenue / available if available > 0 else None
        counts = fee_counts[sku]
        out[sku] = {
            "sku": sku,
            "orders_count": len(order_sets[sku]),
            "units": int(bucket["units"]),
            "revenue_usd": _q4(revenue),
            "purchase_cost_usd": _q4(bucket["purchase_cost_usd"]),
            "shipping_cost_usd": _q4(bucket["shipping_cost_usd"]),
            "shopify_fee_usd": _q4(bucket["shopify_fee_usd"]),
            "fee_source": _fee_source(counts["real"], counts["estimated"]),
            "actual_breakeven_roas": _q4(roas) if roas is not None else None,
            "summary": {
                "real_fee_lines": counts["real"],
                "estimated_fee_lines": counts["estimated"],
            },
        }
    return out


def _load_order_rows(window_start: date, window_end: date) -> list[dict[str, Any]]:
    return query(
        """
        SELECT d.dxm_package_id, d.extended_order_id, d.product_display_sku,
               d.quantity, d.line_amount, d.ship_amount, d.logistic_fee,
               d.purchase_price_cny,
               xs.unit_price AS xmyc_unit_price,
               m.purchase_price AS product_purchase_price
        FROM dianxiaomi_order_lines d
        LEFT JOIN xmyc_storage_skus xs ON xs.sku = d.product_display_sku
        LEFT JOIN media_products m ON m.id = d.product_id
        WHERE d.meta_business_date BETWEEN %s AND %s
          AND d.product_display_sku IS NOT NULL
          AND d.product_display_sku <> ''
        """,
        (window_start, window_end),
    )


def _load_real_fees_by_order(order_names: list[str]) -> dict[str, float]:
    names = [name for name in dict.fromkeys(order_names) if name]
    if not names:
        return {}
    placeholders = ",".join(["%s"] * len(names))
    rows = query(
        f"""
        SELECT order_name, COALESCE(SUM(fee_usd), 0) AS fee
        FROM shopify_payments_transactions
        WHERE type='charge' AND order_name IN ({placeholders})
        GROUP BY order_name
        """,
        tuple(names),
    )
    return {str(row["order_name"]): float(row.get("fee") or 0) for row in rows}


def _upsert_snapshot(
    snapshot: dict[str, Any],
    *,
    window_start: date,
    window_end: date,
    source_run_id: int | None,
) -> None:
    execute(
        """
        INSERT INTO sku_actual_breakeven_roas_snapshots (
          sku, window_start, window_end, orders_count, units,
          revenue_usd, purchase_cost_usd, shipping_cost_usd, shopify_fee_usd,
          fee_source, actual_breakeven_roas, summary_json, source_run_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          orders_count=VALUES(orders_count),
          units=VALUES(units),
          revenue_usd=VALUES(revenue_usd),
          purchase_cost_usd=VALUES(purchase_cost_usd),
          shipping_cost_usd=VALUES(shipping_cost_usd),
          shopify_fee_usd=VALUES(shopify_fee_usd),
          fee_source=VALUES(fee_source),
          actual_breakeven_roas=VALUES(actual_breakeven_roas),
          summary_json=VALUES(summary_json),
          source_run_id=VALUES(source_run_id),
          computed_at=NOW()
        """,
        (
            snapshot["sku"],
            window_start,
            window_end,
            snapshot["orders_count"],
            snapshot["units"],
            snapshot["revenue_usd"],
            snapshot["purchase_cost_usd"],
            snapshot["shipping_cost_usd"],
            snapshot["shopify_fee_usd"],
            snapshot["fee_source"],
            snapshot["actual_breakeven_roas"],
            json.dumps(snapshot.get("summary") or {}, ensure_ascii=False, default=str),
            source_run_id,
        ),
    )


def compute_sku_actual_breakeven_roas(
    window_start: date,
    window_end: date,
    *,
    rmb_per_usd: Any | None = None,
    source_run_id: int | None = None,
) -> dict[str, Any]:
    rows = _load_order_rows(window_start, window_end)
    order_names = [str(row.get("extended_order_id") or "").strip() for row in rows]
    real_fees = _load_real_fees_by_order(order_names)
    snapshots = aggregate_sku_rows(rows, real_fees, rmb_per_usd=rmb_per_usd)

    written = 0
    for snapshot in snapshots.values():
        _upsert_snapshot(
            snapshot,
            window_start=window_start,
            window_end=window_end,
            source_run_id=source_run_id,
        )
        written += 1

    return {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "order_rows": len(rows),
        "skus": len(snapshots),
        "snapshots_written": written,
        "source_run_id": source_run_id,
    }


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _datetime_text(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def get_latest_sku_actual_roas(skus: list[str]) -> dict[str, dict[str, Any]]:
    unique_skus = [sku for sku in dict.fromkeys(str(s).strip() for s in skus) if sku]
    if not unique_skus:
        return {}
    placeholders = ",".join(["%s"] * len(unique_skus))
    rows = query(
        f"""
        SELECT s.sku, s.window_start, s.window_end, s.orders_count, s.units,
               s.actual_breakeven_roas, s.fee_source, s.computed_at
        FROM sku_actual_breakeven_roas_snapshots s
        JOIN (
          SELECT sku, MAX(computed_at) AS max_computed_at
          FROM sku_actual_breakeven_roas_snapshots
          WHERE sku IN ({placeholders})
          GROUP BY sku
        ) latest ON latest.sku = s.sku AND latest.max_computed_at = s.computed_at
        """,
        tuple(unique_skus),
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get("actual_breakeven_roas")
        out[str(row["sku"])] = {
            "value": float(value) if value is not None else None,
            "fee_source": row.get("fee_source"),
            "window_start": _date_text(row.get("window_start")),
            "window_end": _date_text(row.get("window_end")),
            "orders_count": int(row.get("orders_count") or 0),
            "units": int(row.get("units") or 0),
            "computed_at": _datetime_text(row.get("computed_at")),
        }
    return out
