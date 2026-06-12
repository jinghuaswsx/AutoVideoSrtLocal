"""Batch backfill standalone_price / shipping_fee / packet_cost_estimated /
packet_cost_actual on media_products.

Two sources:
- shopify_orders -> mode of (lineitem_price, shipping) for quantity=1 orders
  -> standalone_price + standalone_shipping_fee
- dianxiaomi 30-day orders via DXM-01 CDP -> median logisticFee per product_sku
  -> packet_cost_estimated + packet_cost_actual

Default behaviour: only fill NULL fields. Use --force to overwrite.
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

from appcore import roas_backfill
from appcore.browser_automation_lock import browser_automation_lock


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kind",
        choices=("all", "shopify", "dianxiaomi"),
        default="all",
        help="Which source to backfill (default all).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing non-NULL fields.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without touching DB.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Lookback window for dianxiaomi orders (default 30).",
    )
    parser.add_argument(
        "--cdp-url",
        default=None,
        help="Override DXM-01 CDP URL (default appcore.parcel_cost_suggest.DEFAULT_DXM_CDP_URL).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = build_arg_parser().parse_args(argv)
    summary: dict = {}
    if args.kind in ("all", "shopify"):
        summary["shopify"] = roas_backfill.backfill_shopify_fields(
            force=args.force, dry_run=args.dry_run,
        )
    if args.kind in ("all", "dianxiaomi"):
        with browser_automation_lock(
            task_code="roas_fields_backfill",
            timeout_seconds=600,
            command="backfill_parcel_costs_via_dxm",
        ):
            summary["dianxiaomi"] = roas_backfill.backfill_parcel_costs_via_dxm(
                force=args.force,
                dry_run=args.dry_run,
                days=args.days,
                cdp_url=args.cdp_url,
            )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
