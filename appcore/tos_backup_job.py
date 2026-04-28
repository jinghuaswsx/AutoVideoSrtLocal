from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
import gzip
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import config
from appcore import tos_backup_references, tos_backup_storage
from appcore.db import execute


TASK_CODE = "tos_backup"
TASK_NAME = "TOS 文件与数据库备份"


def _now() -> datetime:
    return datetime.now()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "db").strip("._") or "db"


def previous_backup_date(run_time: datetime | None = None) -> date:
    return (run_time or _now()).date() - timedelta(days=1)


def sync_protected_files() -> dict[str, Any]:
    refs = tos_backup_references.collect_protected_file_refs()
    actions: Counter[str] = Counter()
    errors: list[dict[str, str]] = []

    for ref in refs:
        try:
            result = tos_backup_storage.reconcile_local_file(ref.local_path)
        except Exception as exc:
            actions["failed"] += 1
            errors.append({"local_path": ref.local_path, "error": str(exc)})
            continue
        actions[result.action] += 1
        if result.action == "failed":
            errors.append({"local_path": ref.local_path, "object_key": result.object_key, "error": result.error})

    return {
        "files_checked": len(refs),
        "actions": dict(actions),
        "failed": int(actions.get("failed", 0)),
        "errors": errors[:20],
    }


def build_db_dump_key(*, backup_date: date, run_time: datetime | None = None) -> str:
    current = run_time or _now()
    db_name = _safe_name(config.DB_NAME)
    day = backup_date.isoformat()
    stamp = current.strftime("%H%M%S")
    return f"{tos_backup_storage.db_backup_prefix()}/{day}/{db_name}_{day}_{stamp}.sql.gz"


def _mysqldump_args() -> list[str]:
    return [
        config.MYSQLDUMP_BIN,
        "--single-transaction",
        "--quick",
        "--routines",
        "--triggers",
        "--events",
        "--default-character-set=utf8mb4",
        "--host",
        config.DB_HOST,
        "--port",
        str(config.DB_PORT),
        "--user",
        config.DB_USER,
        config.DB_NAME,
    ]


def dump_mysql_to_file(
    backup_date: date,
    *,
    run_time: datetime | None = None,
    output_dir: str | os.PathLike[str] | None = None,
) -> Path:
    current = run_time or _now()
    base_dir = Path(output_dir or tempfile.gettempdir()) / "autovideosrt-tos-backup"
    base_dir.mkdir(parents=True, exist_ok=True)
    day = backup_date.isoformat()
    dump_path = base_dir / f"{_safe_name(config.DB_NAME)}_{day}_{current.strftime('%H%M%S')}.sql.gz"

    env = os.environ.copy()
    env["MYSQL_PWD"] = config.DB_PASSWORD or ""
    with gzip.open(dump_path, "wb") as handle:
        completed = subprocess.run(
            _mysqldump_args(),
            stdout=handle,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
    if completed.returncode != 0:
        try:
            dump_path.unlink()
        except FileNotFoundError:
            pass
        stderr = completed.stderr.decode("utf-8", errors="replace") if completed.stderr else ""
        raise RuntimeError(f"mysqldump failed: {stderr.strip()}")
    return dump_path


def upload_mysql_dump(
    *,
    run_time: datetime | None = None,
    output_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    current = run_time or _now()
    backup_day = previous_backup_date(current)
    dump_path = dump_mysql_to_file(backup_day, run_time=current, output_dir=output_dir)
    object_key = build_db_dump_key(backup_date=backup_day, run_time=current)
    tos_backup_storage.upload_local_file(dump_path, object_key)
    return {
        "backup_date": backup_day.isoformat(),
        "object_key": object_key,
        "local_file": str(dump_path),
        "bytes": dump_path.stat().st_size if dump_path.exists() else 0,
    }


def _dump_date_from_key(key: str, prefix: str) -> date | None:
    if not key.startswith(prefix.rstrip("/") + "/"):
        return None
    rest = key[len(prefix.rstrip("/") + "/"):]
    day_text = rest.split("/", 1)[0]
    try:
        return date.fromisoformat(day_text)
    except ValueError:
        return None


def cleanup_expired_db_dumps(
    *,
    run_time: datetime | None = None,
    retention_days: int | None = None,
) -> dict[str, int]:
    current = run_time or _now()
    days = int(retention_days if retention_days is not None else config.TOS_BACKUP_DB_RETENTION_DAYS)
    cutoff = current.date() - timedelta(days=days)
    prefix = tos_backup_storage.db_backup_prefix()
    keys = tos_backup_storage.list_object_keys(prefix.rstrip("/") + "/")
    deleted = 0
    for key in keys:
        dump_day = _dump_date_from_key(key, prefix)
        if dump_day is not None and dump_day < cutoff:
            tos_backup_storage.delete_object(key)
            deleted += 1
    return {"db_dumps_scanned": len(keys), "db_dumps_deleted": deleted}


def run_backup(*, run_time: datetime | None = None, output_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    current = run_time or _now()
    if not tos_backup_storage.is_enabled():
        return {"skipped": True, "reason": "TOS backup disabled"}
    file_summary = sync_protected_files()
    db_summary = upload_mysql_dump(run_time=current, output_dir=output_dir)
    cleanup_summary = cleanup_expired_db_dumps(run_time=current)
    return {
        "skipped": False,
        "run_time": current.isoformat(timespec="seconds"),
        "files": file_summary,
        "db_dump": db_summary,
        "cleanup": cleanup_summary,
    }


def _start_scheduled_run(scheduled_for: datetime | None = None) -> int:
    return int(execute(
        "INSERT INTO scheduled_task_runs "
        "(task_code, task_name, status, scheduled_for, started_at) "
        "VALUES (%s, %s, 'running', %s, NOW())",
        (TASK_CODE, TASK_NAME, scheduled_for),
    ))


def _finish_scheduled_run(
    run_id: int,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    error_message: str | None = None,
    output_file: str | None = None,
) -> None:
    execute(
        "UPDATE scheduled_task_runs SET "
        "status=%s, finished_at=NOW(), "
        "duration_seconds=TIMESTAMPDIFF(SECOND, started_at, NOW()), "
        "summary_json=%s, error_message=%s, output_file=%s "
        "WHERE id=%s",
        (
            status,
            json.dumps(summary, ensure_ascii=False) if summary is not None else None,
            error_message,
            output_file,
            int(run_id),
        ),
    )


def run_scheduled_backup(*, scheduled_for: datetime | None = None) -> dict[str, Any]:
    run_id = _start_scheduled_run(scheduled_for)
    try:
        summary = run_backup(run_time=scheduled_for)
    except Exception as exc:
        _finish_scheduled_run(run_id, status="failed", error_message=str(exc))
        raise
    output_file = ((summary.get("db_dump") or {}).get("object_key") if isinstance(summary, dict) else None)
    _finish_scheduled_run(run_id, status="success", summary=summary, output_file=output_file)
    return summary


def register(scheduler) -> None:
    scheduler.add_job(
        run_scheduled_backup,
        "cron",
        hour=1,
        minute=0,
        id=TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
