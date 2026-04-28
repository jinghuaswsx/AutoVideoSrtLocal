from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from appcore import tos_backup_job


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync protected local files and MySQL dumps to the dedicated TOS backup bucket.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--files-only", action="store_true", help="Only reconcile protected local files with TOS.")
    group.add_argument("--db-only", action="store_true", help="Only create/upload a MySQL dump and clean expired DB dumps.")
    group.add_argument("--cleanup-only", action="store_true", help="Only clean expired DB dumps from TOS.")
    group.add_argument("--scheduled", action="store_true", help="Run through scheduled_task_runs tracking.")
    parser.add_argument("--output-dir", default="", help="Temporary directory for local .sql.gz dumps.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = args.output_dir or None

    if args.files_only:
        result = tos_backup_job.sync_protected_files()
    elif args.db_only:
        result = {
            "db_dump": tos_backup_job.upload_mysql_dump(output_dir=output_dir),
            "cleanup": tos_backup_job.cleanup_expired_db_dumps(),
        }
    elif args.cleanup_only:
        result = tos_backup_job.cleanup_expired_db_dumps()
    elif args.scheduled:
        result = tos_backup_job.run_scheduled_backup()
    else:
        result = tos_backup_job.run_backup(output_dir=output_dir)

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
