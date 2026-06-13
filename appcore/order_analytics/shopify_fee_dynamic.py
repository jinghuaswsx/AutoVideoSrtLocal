from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
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


def _normalize_source_csvs(source_csvs: Iterable[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in source_csvs or []:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return normalized


def _coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def _source_csv_filter(source_csvs: list[str]) -> tuple[str, list[Any]]:
    if not source_csvs:
        return "", []
    placeholders = ", ".join(["%s"] * len(source_csvs))
    return f"AND source_csv IN ({placeholders})", list(source_csvs)


def _load_max_transaction_date(source_csvs: Iterable[str] | None = None) -> date | None:
    source_list = _normalize_source_csvs(source_csvs)
    source_filter, source_params = _source_csv_filter(source_list)
    rows = query(
        f"""
        SELECT MAX(DATE(transaction_date)) AS max_date
        FROM shopify_payments_transactions
        WHERE type = 'charge'
          {source_filter}
        """,
        tuple(source_params),
    )
    if not rows:
        return None
    return _coerce_date(rows[0].get("max_date"))


def _load_window_aggregates(
    *,
    window_end_date: date,
    window_days: int,
    source_csvs: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    source_list = _normalize_source_csvs(source_csvs)
    source_filter, source_params = _source_csv_filter(source_list)
    europe_currency_list = ", ".join(
        f"'{currency}'" for currency in sorted(EUROPE_PRESENTMENT_CURRENCIES)
    )
    params: list[Any] = [
        window_end_date,
        int(window_days) - 1,
        window_end_date,
        *source_params,
    ]
    rows = query(
        f"""
        SELECT
            CASE
                WHEN LOWER(source_csv) LIKE 'newjoyloo__%%' THEN 'newjoy'
                WHEN LOWER(source_csv) LIKE 'omurio__%%' THEN 'omurio'
                ELSE 'all'
            END AS store_code,
            CASE
                WHEN UPPER(presentment_currency) = 'USD' THEN 'us'
                WHEN UPPER(presentment_currency) IN ({europe_currency_list}) THEN 'europe'
                ELSE 'other'
            END AS region,
            COUNT(DISTINCT COALESCE(NULLIF(TRIM(order_name), ''), transaction_id)) AS orders_count,
            SUM(ABS(amount_usd)) AS amount_usd,
            SUM(ABS(fee_usd)) AS fee_usd
        FROM shopify_payments_transactions
        WHERE type = 'charge'
          AND DATE(transaction_date) BETWEEN DATE_SUB(%s, INTERVAL %s DAY) AND %s
          {source_filter}
        GROUP BY store_code, region
        """,
        tuple(params),
    )
    return [dict(row) for row in rows]


def _add_all_store_aggregates(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized_rows = [dict(row) for row in rows]
    aggregates: dict[str, dict[str, Any]] = {}
    for row in normalized_rows:
        region = str(row.get("region") or "").strip()
        if not region:
            continue
        aggregate = aggregates.setdefault(
            region,
            {
                "store_code": "all",
                "region": region,
                "orders_count": 0,
                "amount_usd": Decimal("0"),
                "fee_usd": Decimal("0"),
            },
        )
        aggregate["orders_count"] += int(row.get("orders_count") or 0)
        aggregate["amount_usd"] += _to_decimal(row.get("amount_usd"))
        aggregate["fee_usd"] += _to_decimal(row.get("fee_usd"))

    store_rows = [
        row for row in normalized_rows
        if str(row.get("store_code") or "").strip().lower() != "all"
    ]
    all_rows = [
        {
            "store_code": "all",
            "region": row["region"],
            "orders_count": row["orders_count"],
            "amount_usd": float(row["amount_usd"]),
            "fee_usd": float(row["fee_usd"]),
        }
        for _region, row in sorted(aggregates.items())
    ]
    return store_rows + all_rows


def refresh_fee_rate_snapshots(source_csvs: Iterable[str] | None = None) -> dict[str, Any]:
    source_list = _normalize_source_csvs(source_csvs)
    window_end_date = _load_max_transaction_date(source_list)
    if window_end_date is None:
        return {"saved": 0, "reason": "no_charge_transactions"}

    seven_day_rows = {
        (row["store_code"], row["region"]): row
        for row in _add_all_store_aggregates(
            _load_window_aggregates(
                window_end_date=window_end_date,
                window_days=7,
                source_csvs=source_list,
            )
        )
    }
    thirty_day_rows = {
        (row["store_code"], row["region"]): row
        for row in _add_all_store_aggregates(
            _load_window_aggregates(
                window_end_date=window_end_date,
                window_days=30,
                source_csvs=source_list,
            )
        )
    }

    snapshot_rows: list[dict[str, Any]] = []
    for store_code, region in sorted(set(seven_day_rows) | set(thirty_day_rows)):
        selected = select_snapshot_window(
            seven_day=seven_day_rows.get((store_code, region), {}),
            thirty_day=thirty_day_rows.get((store_code, region), {}),
        )
        window_days = int(selected["window_days"])
        snapshot_rows.append(
            build_snapshot_row(
                store_code=store_code,
                region=region,
                window_start_date=window_end_date - timedelta(days=window_days - 1),
                window_end_date=window_end_date,
                window_days=window_days,
                orders_count=selected.get("orders_count") or 0,
                amount_usd=selected.get("amount_usd") or 0,
                fee_usd=selected.get("fee_usd") or 0,
                source_csvs=source_list,
                sample_status=selected["sample_status"],
            )
        )

    saved = save_fee_rate_snapshots(snapshot_rows)
    return {
        "saved": saved,
        "window_end_date": window_end_date,
        "source_csvs": source_list,
    }


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
