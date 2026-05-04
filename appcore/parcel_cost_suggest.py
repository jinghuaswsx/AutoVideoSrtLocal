from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta
from typing import Any

from appcore.db import query


log = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 30
SETTLEMENT_DELAY_DAYS = 2


class ParcelCostSuggestError(RuntimeError):
    pass


def pick_primary_sku_and_shop(product_id: int) -> tuple[str, str]:
    rows = query(
        """
        SELECT product_sku, dxm_shop_id, COUNT(*) AS cnt
        FROM dianxiaomi_order_lines
        WHERE product_id = %s
          AND product_sku IS NOT NULL
          AND dxm_shop_id IS NOT NULL
        GROUP BY product_sku, dxm_shop_id
        ORDER BY cnt DESC
        LIMIT 1
        """,
        (int(product_id),),
    )
    if not rows:
        raise ParcelCostSuggestError("no_orders")
    row = rows[0]
    return str(row["product_sku"]), str(row["dxm_shop_id"])


def compute_suggestion(fees: list[float]) -> dict[str, Any]:
    if not fees:
        return {"sample_size": 0, "median": None, "mean": None, "min": None, "max": None}
    fees_sorted = sorted(fees)
    return {
        "sample_size": len(fees),
        "median": round(statistics.median(fees_sorted), 2),
        "mean": round(sum(fees) / len(fees), 2),
        "min": round(fees_sorted[0], 2),
        "max": round(fees_sorted[-1], 2),
    }


def suggest_parcel_cost(
    product_id: int,
    *,
    days: int = DEFAULT_LOOKBACK_DAYS,
    now_func: Any = None,
) -> dict[str, Any]:
    """从本地 dianxiaomi_order_lines 聚合该产品主 SKU 的历史 logistic_fee。

    不再依赖店小秘 CDP/Playwright —— 本地 SQL 直查，毫秒级返回。
    """
    sku, shop_id = pick_primary_sku_and_shop(int(product_id))
    now = (now_func or datetime.now)()
    end_time = now - timedelta(days=SETTLEMENT_DELAY_DAYS)
    start_time = end_time - timedelta(days=int(days))

    rows = query(
        "SELECT logistic_fee FROM dianxiaomi_order_lines "
        "WHERE product_id = %s AND product_sku = %s "
        "  AND logistic_fee IS NOT NULL AND logistic_fee > 0 "
        "  AND paid_at >= %s AND paid_at <= %s",
        (int(product_id), sku, start_time, end_time),
    )
    fees = [float(r["logistic_fee"]) for r in rows]
    suggestion = compute_suggestion(fees)
    return {
        "product_id": int(product_id),
        "sku": sku,
        "dxm_shop_id": shop_id,
        "lookback_days": int(days),
        "settlement_delay_days": SETTLEMENT_DELAY_DAYS,
        "window_start": start_time.strftime("%Y-%m-%d"),
        "window_end": end_time.strftime("%Y-%m-%d"),
        "orders_pulled": len(rows),
        **suggestion,
    }
