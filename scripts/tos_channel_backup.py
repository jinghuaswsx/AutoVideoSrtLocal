from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from appcore import tos_channel_migration


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy protected backup files and the latest MySQL dump to a configured TOS channel."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--files-only", action="store_true", help="Only copy protected files.")
    group.add_argument("--db-only", action="store_true", help="Only copy the latest MySQL dump.")
    parser.add_argument("--target-code", default="tos_wj", help="infra_credentials TOS channel code.")
    parser.add_argument("--mysql-prefix", default="mysqldump", help="Target prefix for copied MySQL dumps.")
    parser.add_argument(
        "--mysql-retention-count",
        type=int,
        default=7,
        help="Keep only this many MySQL dump objects in the target prefix.",
    )
    parser.add_argument("--output-dir", default="", help="Temporary directory for downloaded .sql.gz dumps.")
    parser.add_argument("--dry-run", action="store_true", help="Report intended copies without uploading.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite objects that already exist in the target bucket.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    copy_files = not args.db_only
    copy_db = not args.files_only
    result = tos_channel_migration.run_channel_backup(
        target_code=args.target_code,
        files=copy_files,
        db_dump=copy_db,
        mysql_prefix=args.mysql_prefix,
        mysql_retention_count=args.mysql_retention_count,
        output_dir=args.output_dir or None,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
