from __future__ import annotations

import argparse
import json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Deprecated. Media objects are localized now; use "
            "scripts/migrate_local_storage_media_assets.py instead."
        )
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--cleanup-remote", action="store_true")
    parser.add_argument("--cleanup-local", action="store_true")
    parser.add_argument("--configure-cors", action="store_true")
    parser.add_argument("--old-bucket")
    parser.add_argument("--new-bucket")
    parser.add_argument("--report-path")
    parser.add_argument("--temp-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--origin", action="append", dest="origins")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    print(json.dumps({
        "ok": False,
        "error": "media TOS bucket migration is disabled",
        "replacement": "python scripts/migrate_local_storage_media_assets.py",
    }, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
