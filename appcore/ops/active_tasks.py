from __future__ import annotations

import argparse
import json
import sys
from typing import Iterable

from appcore import active_tasks
from appcore.active_tasks import ActiveTask

_RUNTIME_TABLE_NAMES = ("runtime_active_tasks", "runtime_active_task_snapshots")


def _task_key(task: ActiveTask) -> str:
    return f"{task.project_type}:{task.task_id}"


def _dedupe(tasks: Iterable[ActiveTask]) -> list[ActiveTask]:
    by_key: dict[str, ActiveTask] = {}
    for task in tasks:
        by_key[_task_key(task)] = task
    return sorted(by_key.values(), key=lambda item: (item.project_type, item.task_id))


def _print_tasks(tasks: list[ActiveTask], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([task.to_dict() for task in tasks], ensure_ascii=False, indent=2))
        return
    if not tasks:
        print("no active tasks")
        return
    for task in tasks:
        print(
            f"{_task_key(task)} policy={task.interrupt_policy} "
            f"stage={task.stage or '-'} runner={task.runner or '-'} "
            f"last_heartbeat_at={task.last_heartbeat_at.isoformat()}"
        )


def _load_tasks(max_age_seconds: int) -> list[ActiveTask]:
    return _dedupe([
        *active_tasks.list_active_tasks(),
        *active_tasks.load_persisted_active_tasks(max_age_seconds),
    ])


def _is_missing_runtime_table_error(exc: Exception) -> bool:
    parts = getattr(exc, "args", None) or (str(exc),)
    text = " ".join(str(part) for part in parts).lower()
    return "1146" in text and any(table_name in text for table_name in _RUNTIME_TABLE_NAMES)


def _print_load_error(exc: Exception, *, force: bool) -> None:
    if not _is_missing_runtime_table_error(exc):
        print(f"blocked: cannot verify active tasks before restart: {exc}")
        return

    print(
        "blocked: runtime active task tables are missing; this is expected only during "
        "the first deploy before migrations have run."
    )
    print(
        "Use --force only after manually confirming no long-running tasks, then restart "
        "once to apply migrations. After that, run pre-restart without --force."
    )
    if force:
        print("force: restart allowed by operator override despite missing runtime active task tables.")


def _list(args: argparse.Namespace) -> int:
    try:
        tasks = _load_tasks(args.max_age_seconds)
    except Exception as exc:
        _print_load_error(exc, force=False)
        return 2
    _print_tasks(tasks, as_json=args.json)
    return 0


def _pre_restart(args: argparse.Namespace) -> int:
    try:
        tasks = _load_tasks(args.max_age_seconds)
    except Exception as exc:
        _print_load_error(exc, force=args.force)
        return 0 if args.force else 2

    active_tasks.snapshot_active_tasks(
        "pre_restart_force" if args.force else "pre_restart_check",
        tasks=tasks,
    )
    if not tasks:
        print("no active tasks")
        return 0

    blocking = [task for task in tasks if task.interrupt_policy == "block_restart"]
    if blocking and not args.force:
        print("blocked: active non-interruptible tasks found")
        _print_tasks(blocking, as_json=False)
        return 2

    if blocking and args.force:
        print("force: active non-interruptible tasks found, restart allowed by operator override")
        _print_tasks(blocking, as_json=False)
        return 0

    print("warning: active interruptible tasks found")
    _print_tasks(tasks, as_json=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect AutoVideoSrt active background tasks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List active tasks.")
    list_parser.add_argument("--json", action="store_true", help="Print JSON.")
    list_parser.add_argument("--max-age-seconds", type=int, default=30)
    list_parser.set_defaults(func=_list)

    pre_restart = subparsers.add_parser("pre-restart", help="Fail when restart would interrupt risky tasks.")
    pre_restart.add_argument("--force", action="store_true", help="Snapshot and allow restart despite blockers.")
    pre_restart.add_argument("--max-age-seconds", type=int, default=30)
    pre_restart.set_defaults(func=_pre_restart)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
