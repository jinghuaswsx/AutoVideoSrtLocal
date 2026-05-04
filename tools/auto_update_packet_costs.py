"""自动更新产品级小包成本（packet_cost_actual / packet_cost_estimated）。

每天跑一次，从 dianxiaomi_order_lines.logistic_fee 聚合：
  - packet_cost_actual   = 均值 (mean)
  - packet_cost_estimated = 中位数 (median)

只更新样本 ≥ MIN_SAMPLE_SIZE 的产品；样本不足的产品保留现有值。

用法：
  python tools/auto_update_packet_costs.py          # 默认 lookback 30 天
  python tools/auto_update_packet_costs.py --days 60
"""
from __future__ import annotations

import argparse
import logging
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore.db import execute, query

log = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 30
SETTLEMENT_DELAY_DAYS = 2
MIN_SAMPLE_SIZE = 5


def _fetch_fees_by_product(
    start_time: datetime, end_time: datetime,
) -> dict[int, list[float]]:
    """拉取时间窗内所有产品的有效 logistic_fee，按 product_id 分组。"""
    rows = query(
        "SELECT product_id, logistic_fee "
        "FROM dianxiaomi_order_lines "
        "WHERE product_id IS NOT NULL "
        "  AND logistic_fee IS NOT NULL AND logistic_fee > 0 "
        "  AND paid_at >= %s AND paid_at <= %s",
        (start_time, end_time),
    )
    by_pid: dict[int, list[float]] = {}
    for row in rows:
        pid = int(row["product_id"])
        fee = float(row["logistic_fee"])
        by_pid.setdefault(pid, []).append(fee)
    return by_pid


def _compute_stats(fees: list[float]) -> tuple[float, float, int]:
    """返回 (mean, median, sample_size)。"""
    fees_sorted = sorted(fees)
    mean = round(sum(fees) / len(fees), 2)
    median = round(statistics.median(fees_sorted), 2)
    return mean, median, len(fees)


def update_packet_costs(*, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    now = datetime.now()
    end_time = now - timedelta(days=SETTLEMENT_DELAY_DAYS)
    start_time = end_time - timedelta(days=lookback_days)

    log.info("packet_cost update window: %s ~ %s, min_sample=%d",
             start_time.strftime("%Y-%m-%d"), end_time.strftime("%Y-%m-%d"),
             MIN_SAMPLE_SIZE)

    by_pid = _fetch_fees_by_product(start_time, end_time)

    updated = 0
    skipped = 0
    for pid, fees in by_pid.items():
        if len(fees) < MIN_SAMPLE_SIZE:
            skipped += 1
            continue
        mean, median, n = _compute_stats(fees)
        execute(
            "UPDATE media_products "
            "SET packet_cost_actual = %s, packet_cost_estimated = %s "
            "WHERE id = %s",
            (mean, median, pid),
        )
        updated += 1
        log.debug("product %d: mean=%s median=%s n=%d", pid, mean, median, n)

    result = {
        "window_start": start_time.strftime("%Y-%m-%d"),
        "window_end": end_time.strftime("%Y-%m-%d"),
        "min_sample_size": MIN_SAMPLE_SIZE,
        "products_total": len(by_pid),
        "products_updated": updated,
        "products_skipped": skipped,
    }
    log.info("packet_cost update done: %s", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Auto-update product packet costs")
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                        help=f"Lookback window in days (default {DEFAULT_LOOKBACK_DAYS})")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    result = update_packet_costs(lookback_days=args.days)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
