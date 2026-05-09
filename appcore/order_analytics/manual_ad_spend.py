"""Meta 广告费人工录入兜底 DAO。

详细设计：docs/superpowers/specs/2026-05-09-manual-daily-ad-spend-design.md
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable, Mapping

from appcore.db import get_conn

TABLE = "meta_ad_manual_daily_spend"


def upsert_entries(
    *,
    business_date: date,
    entries: Iterable[Mapping[str, object]],
    updated_by: int | None,
) -> int:
    """批量 upsert 同一天多个账户的人工录入。返回受影响行数（含 update）。

    每个 entry: {"account_code": str, "ad_account_id": str, "spend_usd": Decimal|str|float}
    """
    payload = []
    for entry in entries:
        account_code = str(entry["account_code"]).strip()
        ad_account_id = str(entry["ad_account_id"]).strip()
        spend = Decimal(str(entry["spend_usd"]))
        payload.append((business_date, account_code, ad_account_id, spend, updated_by))
    if not payload:
        return 0

    sql = f"""
        INSERT INTO {TABLE} (business_date, account_code, ad_account_id, spend_usd, updated_by)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          ad_account_id = VALUES(ad_account_id),
          spend_usd     = VALUES(spend_usd),
          updated_by    = VALUES(updated_by)
    """
    conn = get_conn()
    with conn.cursor() as cur:
        cur.executemany(sql, payload)
        conn.commit()
        return cur.rowcount


def list_range(date_from: date, date_to: date) -> list[dict]:
    """按 business_date DESC, account_code ASC 列出区间内所有人工录入行。"""
    sql = f"""
        SELECT id, business_date, account_code, ad_account_id, spend_usd,
               updated_by, updated_at, created_at
        FROM {TABLE}
        WHERE business_date BETWEEN %s AND %s
        ORDER BY business_date DESC, account_code ASC
    """
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, (date_from, date_to))
        return list(cur.fetchall())
