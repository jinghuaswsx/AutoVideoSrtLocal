from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appcore.pushes import normalize_new_product_push_flags


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _json_default(value: Any) -> str:
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalize historical media_push_logs.is_new_product_push flags."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Omit this flag to run a dry-run preview.",
    )
    parser.add_argument(
        "--show-changes",
        type=int,
        default=50,
        help="Maximum number of planned/applied changes to print.",
    )
    args = parser.parse_args(argv)

    result = normalize_new_product_push_flags(dry_run=not args.apply)
    preview = dict(result)
    changes = list(preview.get("changes") or [])
    preview["changes"] = changes[: max(0, int(args.show_changes))]
    preview["changes_truncated"] = len(changes) > len(preview["changes"])

    logging.info(
        "new product push flag normalize %s: products=%s logs=%s updates=%s set_true=%s clear=%s",
        "apply" if args.apply else "dry-run",
        result["scanned_products"],
        result["scanned_logs"],
        result["update_count"],
        result["set_true_count"],
        result["clear_count"],
    )
    print(json.dumps(preview, ensure_ascii=False, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
