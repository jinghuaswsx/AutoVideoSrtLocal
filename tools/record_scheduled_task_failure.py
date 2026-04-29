from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import scheduled_tasks


def _parse_summary(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--summary-json must decode to an object")
    return parsed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record a failed scheduled task run.")
    parser.add_argument("--task-code", required=True)
    parser.add_argument("--error-message", required=True)
    parser.add_argument("--summary-json", default="")
    parser.add_argument("--output-file", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run_id = scheduled_tasks.record_failure(
        args.task_code,
        error_message=args.error_message,
        summary=_parse_summary(args.summary_json),
        output_file=args.output_file or None,
    )
    print(json.dumps({"ok": True, "run_id": run_id, "task_code": args.task_code}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
