"""Sync dianxiaomi.com yuncang warehouse SKUs into the local dianxiaomi_yuncang_skus table.

Connects to the shared Chromium via CDP (default 127.0.0.1:9225),
fetches yuncangWarehouseSku/index.htm pages, parses SKUs, upserts them, and refreshes
media_products purchase_price.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import xmyc_storage


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cdp-url",
        default="http://127.0.0.1:9225",
        help="Chromium CDP URL for Dianxiaomi (default 127.0.0.1:9225)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    summary = xmyc_storage.sync_dianxiaomi_yuncang_skus(cdp_url=args.cdp_url)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
