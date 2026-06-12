"""Batch backfill standalone_price / shipping_fee / ROAS inputs on media_products.

Two sources:
- shopify_orders -> mode of (lineitem_price, shipping) for quantity=1 orders
  -> standalone_price + standalone_shipping_fee
- dianxiaomi local order rows -> median logisticFee per product
  -> packet_cost_estimated + packet_cost_actual
- complete mode -> all product-level ROAS inputs with source labels

Default behaviour: only fill NULL fields. Use --force to overwrite.

Docs-anchor: docs/superpowers/specs/2026-06-12-product-roas-completion-design.md
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


def _csv_ints(value: str | None) -> list[int]:
    if not value:
        return []
    out: list[int] = []
    for part in str(value).split(","):
        text = part.strip()
        if not text:
            continue
        out.append(int(text))
    return out


def _csv_text(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kind",
        choices=("all", "shopify", "dianxiaomi", "complete"),
        default="complete",
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
        help="Lookback window for dianxiaomi logistics rows (default 30).",
    )
    parser.add_argument(
        "--settlement-delay-days",
        type=int,
        default=2,
        help="Skip recent dianxiaomi rows to avoid unsettled logistics data (default 2).",
    )
    parser.add_argument(
        "--rmb-per-usd",
        default=None,
        help="Override configured RMB/USD rate for estimate calculations.",
    )
    parser.add_argument(
        "--shopify-timeout",
        type=int,
        default=12,
        help="Timeout seconds for each public Shopify product JSON fetch.",
    )
    parser.add_argument(
        "--product-id",
        default=None,
        help="Only process one or more product ids, comma-separated.",
    )
    parser.add_argument(
        "--product-code",
        default=None,
        help="Only process one or more product codes, comma-separated.",
    )
    parser.add_argument(
        "--show-products",
        action="store_true",
        help="Include per-product status rows in the final JSON.",
    )
    parser.add_argument(
        "--log-products",
        action="store_true",
        help="Print one progress line per product while processing.",
    )
    parser.add_argument(
        "--cdp-url",
        default=None,
        help="Deprecated compatibility option; local SQL is used.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = build_arg_parser().parse_args(argv)
    summary: dict = {}
    if args.kind == "complete":
        progress_fn = None
        if args.log_products:
            def progress_fn(item: dict) -> None:
                print(
                    "product "
                    f"{item.get('id')} {item.get('product_code')} "
                    f"status={item.get('status')} "
                    f"updated={','.join(item.get('updated_fields') or []) or '-'} "
                    f"roas={item.get('effective_roas') if item.get('effective_roas') is not None else '-'}",
                    flush=True,
                )

        summary["complete"] = roas_backfill.backfill_complete_product_roas(
            force=args.force,
            dry_run=args.dry_run,
            days=args.days,
            settlement_delay_days=args.settlement_delay_days,
            rmb_per_usd=args.rmb_per_usd,
            product_ids=_csv_ints(args.product_id),
            product_codes=_csv_text(args.product_code),
            include_products=args.show_products,
            progress_fn=progress_fn,
            shopify_timeout_s=args.shopify_timeout,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.kind in ("all", "shopify"):
        summary["shopify"] = roas_backfill.backfill_shopify_fields(
            force=args.force, dry_run=args.dry_run,
        )
    if args.kind in ("all", "dianxiaomi"):
        summary["dianxiaomi"] = roas_backfill.backfill_parcel_costs_via_dxm(
            force=args.force,
            dry_run=args.dry_run,
            days=args.days,
            settlement_delay_days=args.settlement_delay_days,
            cdp_url=args.cdp_url,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
