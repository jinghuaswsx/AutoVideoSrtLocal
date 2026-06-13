"""Fill product standalone price, TK sale price, and user shipping fee.

Default mode is dry-run. Pass --apply to write media_products.
Docs-anchor: docs/superpowers/specs/2026-06-12-product-price-shipping-fill.md
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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write database changes. Default is dry-run.")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run even if --apply is present.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing non-NULL values.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum products to scan.")
    parser.add_argument("--offset", type=int, default=0, help="Offset for batched scans.")
    parser.add_argument("--product-id", type=int, default=None, help="Scan one product id.")
    parser.add_argument("--workers", type=int, default=2, help="Low parallelism for public Shopify fetches.")
    parser.add_argument("--timeout", type=int, default=8, help="Per public JSON request timeout seconds.")
    parser.add_argument("--retries", type=int, default=1, help="Retries after the initial attempt.")
    parser.add_argument("--sample-limit", type=int, default=20, help="Number of sample rows in JSON output.")
    parser.add_argument(
        "--env-file",
        default="",
        help="Optional .env path to load before appcore imports, e.g. /opt/autovideosrt/.env.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_arg_parser().parse_args(argv)
    if args.apply and args.dry_run:
        raise SystemExit("--apply and --dry-run cannot be used together")
    if args.env_file:
        from dotenv import load_dotenv

        load_dotenv(args.env_file)

    from appcore import product_price_shipping_fill as service

    summary = service.fill_product_price_shipping(
        force=args.force,
        dry_run=not args.apply,
        limit=args.limit,
        offset=args.offset,
        product_id=args.product_id,
        max_workers=args.workers,
        timeout_seconds=args.timeout,
        retries=args.retries,
        sample_limit=args.sample_limit,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
