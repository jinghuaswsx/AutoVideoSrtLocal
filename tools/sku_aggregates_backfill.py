"""Aggregate per-SKU pricing/shipping/parcel-fee onto xmyc_storage_skus.

Three sources, all keyed on xmyc_storage_skus.sku:
- shopify_orders.lineitem_sku  -> standalone_price_sku + standalone_shipping_fee_sku
- dianxiaomi_order_lines       -> sku_orders_count
- DXM-01 dianxiaomi orders     -> packet_cost_actual_sku (median logisticFee)

Default: only fill NULLs (--force overwrites). --dry-run prints without writing.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import sku_aggregates
from appcore.browser_automation_lock import browser_automation_lock


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kind", choices=("all", "shopify", "counts", "dianxiaomi"), default="all")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--cdp-url", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = build_arg_parser().parse_args(argv)
    summary: dict = {}
    if args.kind in ("all", "shopify"):
        summary["shopify"] = sku_aggregates.update_xmyc_sku_shopify_aggregates(
            force=args.force, dry_run=args.dry_run,
        )
    if args.kind in ("all", "counts"):
        if args.dry_run:
            summary["order_counts"] = {"skipped": "dry-run"}
        else:
            summary["order_counts"] = {"updated": sku_aggregates.update_xmyc_sku_order_counts()}
    if args.kind in ("all", "dianxiaomi"):
        with browser_automation_lock(
            task_code="sku_aggregates_backfill",
            timeout_seconds=600,
            command="update_xmyc_sku_parcel_costs",
        ):
            summary["dianxiaomi"] = sku_aggregates.update_xmyc_sku_parcel_costs(
                force=args.force,
                dry_run=args.dry_run,
                days=args.days,
                cdp_url=args.cdp_url,
            )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
