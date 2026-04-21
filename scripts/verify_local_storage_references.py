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
    parser = argparse.ArgumentParser(description="Verify local storage references for projects and media assets.")
    parser.add_argument("--only-active", action="store_true")
    parser.add_argument("--project-limit", type=int, default=0)
    parser.add_argument("--media-limit", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = migration.verify_all_references(
        only_active=bool(args.only_active),
        project_limit=max(int(args.project_limit or 0), 0),
        media_limit=max(int(args.media_limit or 0), 0),
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
