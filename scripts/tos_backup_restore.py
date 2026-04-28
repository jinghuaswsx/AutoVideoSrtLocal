from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from appcore import tos_backup_restore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Restore the latest TOS DB dump and protected files onto this server.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--db-only", action="store_true", help="Only download and restore the latest MySQL dump.")
    group.add_argument("--files-only", action="store_true", help="Only restore protected files referenced by the current DB.")
    group.add_argument("--download-only", action="store_true", help="Only download the latest MySQL dump without importing it.")
    parser.add_argument("--output-dir", default="", help="Directory for downloaded .sql.gz dumps.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = args.output_dir or None

    if args.files_only:
        result = tos_backup_restore.run_restore(output_dir=output_dir, restore_db=False, restore_files=True)
    elif args.db_only:
        result = tos_backup_restore.run_restore(output_dir=output_dir, restore_db=True, restore_files=False)
    elif args.download_only:
        result = tos_backup_restore.run_restore(
            output_dir=output_dir,
            restore_db=True,
            restore_files=False,
            download_only=True,
        )
    else:
        result = tos_backup_restore.run_restore(output_dir=output_dir)

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
