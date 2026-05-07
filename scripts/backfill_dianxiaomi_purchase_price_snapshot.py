"""一次性脚本：把所有存量 dianxiaomi_order_lines 的 purchase_price_cny 列填上当前 media_products.purchase_price + NOW()。

调用方式（在 prod /opt/autovideosrt 用 venv 跑）：
  ./venv/bin/python scripts/backfill_dianxiaomi_purchase_price_snapshot.py

仅填充 purchase_price_cny IS NULL 的行（已有快照的不动）。
新订单导入时 upsert_dianxiaomi_order_lines 会自动 chain 这步，存量订单需要手动跑这个脚本一次。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore.order_analytics.dianxiaomi import backfill_purchase_price_snapshot


def main() -> int:
    print("=== dianxiaomi_order_lines purchase_price snapshot backfill ===", flush=True)
    result = backfill_purchase_price_snapshot(batch_id=None)
    print(f"  affected rows = {result.get('affected', 0)}", flush=True)
    print("  done.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
