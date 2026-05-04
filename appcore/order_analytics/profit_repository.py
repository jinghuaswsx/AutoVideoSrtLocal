"""order_profit_lines / order_profit_runs 持久化层。

upsert by dxm_order_line_id；status='incomplete' 也写入（profit_usd=NULL）。
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from typing import Any


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def query_one(*args, **kwargs):
    return _facade().query_one(*args, **kwargs)


def execute(*args, **kwargs):
    return _facade().execute(*args, **kwargs)


def get_conn(*args, **kwargs):
    return _facade().get_conn(*args, **kwargs)


_PROFIT_LINE_COLUMNS = (
    "dxm_order_line_id", "product_id", "business_date", "paid_at",
    "buyer_country", "presentment_currency", "shopify_tier",
    "line_amount_usd", "shipping_allocated_usd", "revenue_usd",
    "shopify_fee_usd", "ad_cost_usd", "purchase_usd",
    "shipping_cost_usd", "return_reserve_usd",
    "profit_usd", "status", "missing_fields", "cost_basis",
    "source_run_id",
)


def upsert_profit_line(
    line_result: dict[str, Any],
    *,
    business_date: date,
    paid_at: datetime | None,
    source_run_id: int | None = None,
) -> None:
    """upsert 利润核算结果到 order_profit_lines。

    incomplete 行也写入：profit_usd=NULL，missing_fields 列出缺什么。
    """
    status = line_result.get("status", "error")
    is_complete = status == "ok"

    values = (
        line_result.get("dxm_order_line_id"),
        line_result.get("product_id"),
        business_date,
        paid_at,
        line_result.get("buyer_country"),
        line_result.get("presentment_currency"),
        line_result.get("shopify_tier"),
        line_result.get("line_amount_usd") if is_complete else None,
        line_result.get("shipping_allocated_usd") if is_complete else None,
        line_result.get("revenue_usd") if is_complete else None,
        line_result.get("shopify_fee_usd") if is_complete else None,
        line_result.get("ad_cost_usd") if is_complete else None,
        line_result.get("purchase_usd") if is_complete else None,
        line_result.get("shipping_cost_usd") if is_complete else None,
        line_result.get("return_reserve_usd") if is_complete else None,
        line_result.get("profit_usd"),
        status,
        json.dumps(line_result.get("missing_fields") or [], ensure_ascii=False),
        json.dumps(line_result.get("cost_basis") or {}, ensure_ascii=False, default=str),
        source_run_id,
    )

    placeholders = ", ".join(["%s"] * len(_PROFIT_LINE_COLUMNS))
    columns_sql = ", ".join(_PROFIT_LINE_COLUMNS)
    update_cols = [c for c in _PROFIT_LINE_COLUMNS if c != "dxm_order_line_id"]
    update_sql = ", ".join(f"{c}=VALUES({c})" for c in update_cols)

    sql = (
        f"INSERT INTO order_profit_lines ({columns_sql}) "
        f"VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {update_sql}, computed_at=NOW()"
    )
    execute(sql, values)


def start_profit_run(
    *,
    task_code: str,
    window_start_at: datetime | None = None,
    window_end_at: datetime | None = None,
    rmb_per_usd: float | None = None,
    return_reserve_rate: float | None = None,
) -> int:
    """开始一次利润核算任务，返回 run_id。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO order_profit_runs "
                "(task_code, window_start_at, window_end_at, rmb_per_usd, "
                " return_reserve_rate) "
                "VALUES (%s, %s, %s, %s, %s)",
                (task_code, window_start_at, window_end_at,
                 rmb_per_usd, return_reserve_rate),
            )
            run_id = int(cur.lastrowid)
        conn.commit()
        return run_id
    finally:
        conn.close()


def finish_profit_run(
    *,
    run_id: int,
    status: str,
    lines_total: int = 0,
    lines_ok: int = 0,
    lines_incomplete: int = 0,
    lines_error: int = 0,
    unallocated_ad_spend_usd: float = 0,
    error_message: str | None = None,
    summary: dict[str, Any] | None = None,
) -> None:
    execute(
        "UPDATE order_profit_runs SET "
        "status=%s, finished_at=NOW(), "
        "lines_total=%s, lines_ok=%s, lines_incomplete=%s, lines_error=%s, "
        "unallocated_ad_spend_usd=%s, error_message=%s, summary_json=%s "
        "WHERE id=%s",
        (
            status,
            lines_total, lines_ok, lines_incomplete, lines_error,
            unallocated_ad_spend_usd,
            error_message,
            json.dumps(summary or {}, ensure_ascii=False, default=str),
            run_id,
        ),
    )
