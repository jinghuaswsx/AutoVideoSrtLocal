from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from appcore import local_storage_migration as migration


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enumerate project references for local storage migration.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only-active", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows = migration.load_project_rows(
        only_active=bool(args.only_active),
        limit=max(int(args.limit or 0), 0),
    )
    for row in rows:
        print(json.dumps(migration.build_project_report(row), ensure_ascii=False))
    print(json.dumps({
        "checked": len(rows),
        "dry_run": bool(args.dry_run),
        "ok": True,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
