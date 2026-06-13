from __future__ import annotations

import json
import sys
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Mapping


REGION_US = "us"
REGION_EUROPE = "europe"
REGION_OTHER = "other"

SAMPLE_STATUS_OK_7D = "ok_7d"
SAMPLE_STATUS_OK_30D = "ok_30d"
SAMPLE_STATUS_INSUFFICIENT = "insufficient"

EUROPE_PRESENTMENT_CURRENCIES = {
    "EUR",
    "GBP",
    "CHF",
    "SEK",
    "NOK",
    "DKK",
    "PLN",
    "CZK",
    "HUF",
    "RON",
    "BGN",
}

MIN_7D_ORDERS = 100
MIN_30D_ORDERS = 300
FIXED_FEE_PER_ORDER = Decimal("0.30")


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def get_conn(*args, **kwargs):
    return _facade().get_conn(*args, **kwargs)


def _round_float(value: Decimal, places: str = "0.00000001") -> float:
    return float(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def region_for_presentment_currency(currency: str | None) -> str:
    normalized = (currency or "").strip().upper()
    if normalized == "USD":
        return REGION_US
    if normalized in EUROPE_PRESENTMENT_CURRENCIES:
        return REGION_EUROPE
    return REGION_OTHER


def infer_store_code_from_source_csv(source_csv: str | None) -> str:
    name = Path(source_csv or "").name.lower()
    if name.startswith("newjoyloo__"):
        return "newjoy"
    if name.startswith("omurio__"):
        return "omurio"
    return "all"


def select_snapshot_window(
    *,
    seven_day: Mapping[str, Any],
    thirty_day: Mapping[str, Any],
) -> dict[str, Any]:
    seven_orders = int(seven_day.get("orders_count") or 0)
    if seven_orders >= MIN_7D_ORDERS:
        selected = dict(seven_day)
        selected["window_days"] = 7
        selected["sample_status"] = SAMPLE_STATUS_OK_7D
        return selected

    thirty_orders = int(thirty_day.get("orders_count") or 0)
    selected = dict(thirty_day)
    selected["window_days"] = 30
    selected["sample_status"] = (
        SAMPLE_STATUS_OK_30D
        if thirty_orders >= MIN_30D_ORDERS
        else SAMPLE_STATUS_INSUFFICIENT
    )
    return selected


def build_snapshot_row(
    *,
    store_code: str,
    region: str,
    window_start_date: date,
    window_end_date: date,
    window_days: int,
    orders_count: int,
    amount_usd: Any,
    fee_usd: Any,
    source_csvs: Iterable[str],
    sample_status: str,
) -> dict[str, Any]:
    amount = _to_decimal(amount_usd)
    fee = _to_decimal(fee_usd)
    orders = int(orders_count or 0)
    effective_rate = Decimal("0") if amount <= 0 else fee / amount
    variable_fee = fee - (Decimal(orders) * FIXED_FEE_PER_ORDER)
    if variable_fee < 0:
        variable_fee = Decimal("0")
    variable_rate = Decimal("0") if amount <= 0 else variable_fee / amount

    return {
        "store_code": store_code,
        "region": region,
        "window_start_date": window_start_date,
        "window_end_date": window_end_date,
        "window_days": int(window_days),
        "orders_count": orders,
        "amount_usd": float(amount),
        "fee_usd": float(fee),
        "effective_rate": _round_float(effective_rate),
        "fixed_fee_per_order": float(FIXED_FEE_PER_ORDER),
        "variable_rate": _round_float(variable_rate),
        "source_csvs_json": list(source_csvs),
        "sample_status": sample_status,
    }


def _snapshot_insert_params(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row["store_code"],
        row["region"],
        row["window_start_date"],
        row["window_end_date"],
        row["window_days"],
        row["orders_count"],
        row["amount_usd"],
        row["fee_usd"],
        row["effective_rate"],
        row["fixed_fee_per_order"],
        row["variable_rate"],
        json.dumps(row.get("source_csvs_json") or [], ensure_ascii=False),
        row["sample_status"],
    )


def save_fee_rate_snapshots(rows: Iterable[Mapping[str, Any]]) -> int:
    params_list = [_snapshot_insert_params(row) for row in rows]
    if not params_list:
        return 0

    sql = """
        INSERT INTO shopify_fee_rate_snapshots (
            store_code,
            region,
            window_start_date,
            window_end_date,
            window_days,
            orders_count,
            amount_usd,
            fee_usd,
            effective_rate,
            fixed_fee_per_order,
            variable_rate,
            source_csvs_json,
            sample_status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    conn = get_conn()
    cur = None
    try:
        autocommit = getattr(conn, "autocommit", None)
        if callable(autocommit):
            autocommit(False)
        cur = conn.cursor()
        cur.executemany(sql, params_list)
        conn.commit()
        return len(params_list)
    except Exception:
        conn.rollback()
        raise
    finally:
        if cur is not None:
            close_cursor = getattr(cur, "close", None)
            if callable(close_cursor):
                close_cursor()
        autocommit = getattr(conn, "autocommit", None)
        if callable(autocommit):
            autocommit(True)
        conn.close()


def _load_snapshot_for_store_region(store_code: str, region: str) -> dict[str, Any] | None:
    rows = query(
        """
        SELECT
            id,
            store_code,
            region,
            window_start_date,
            window_end_date,
            window_days,
            orders_count,
            amount_usd,
            fee_usd,
            effective_rate,
            fixed_fee_per_order,
            variable_rate,
            source_csvs_json,
            sample_status,
            computed_at
        FROM shopify_fee_rate_snapshots
        WHERE store_code = %s
          AND region = %s
          AND sample_status IN ('ok_7d', 'ok_30d')
        ORDER BY window_end_date DESC, computed_at DESC, id DESC
        LIMIT 1
        """,
        (store_code, region),
    )
    return dict(rows[0]) if rows else None


def load_best_fee_rate_snapshot(store_code: str | None, region: str) -> dict[str, Any] | None:
    normalized_store = (store_code or "").strip().lower() or "all"
    snapshot = _load_snapshot_for_store_region(normalized_store, region)
    if snapshot is not None:
        return snapshot
    if normalized_store != "all":
        return _load_snapshot_for_store_region("all", region)
    return None
