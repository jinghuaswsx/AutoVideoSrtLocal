"""订单利润核算增量同步脚本。

策略：每次跑都重算"最近 lookback_days 天"窗口（默认 2 天），覆盖：
  - 今天的新订单 / 状态变更
  - 昨天的当日 SKU spend/units 变化（Meta 广告数据有延迟）
  - 完备性补录后的产品的近期订单（采购价 / 包装成本被填后）

设计为幂等：upsert by dxm_order_line_id，重复跑只更新数字、不重复插入。

用法：
  python tools/order_profit_incremental.py             # 默认最近 2 天
  python tools/order_profit_incremental.py --days 7    # 最近 7 天

注册到 scheduled_tasks：建议每 10 分钟跑一次（与 ROI 同步频率一致）。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.order_profit_backfill import backfill

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Order profit incremental sync")
    parser.add_argument("--days", type=int, default=2,
                        help="重算最近 N 天的订单（默认 2）")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    today = date.today()
    date_from = today - timedelta(days=args.days)
    log.info("incremental sync window: %s ~ %s", date_from, today)

    result = backfill(date_from, today, dry_run=False)
    log.info("incremental sync done: run_id=%s totals=%s",
             result["run_id"], result["totals"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
