from __future__ import annotations

from collections import Counter
from datetime import date
import gzip
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import config
from appcore import tos_backup_references, tos_backup_storage


def _dump_sort_key(object_key: str) -> tuple[date, str] | None:
    prefix = tos_backup_storage.db_backup_prefix().rstrip("/") + "/"
    if not object_key.startswith(prefix) or not object_key.endswith(".sql.gz"):
        return None
    rest = object_key[len(prefix):]
    day_text = rest.split("/", 1)[0]
    try:
        dump_day = date.fromisoformat(day_text)
    except ValueError:
        return None
    return dump_day, object_key


def latest_db_dump_key() -> str | None:
    prefix = tos_backup_storage.db_backup_prefix().rstrip("/") + "/"
    candidates: list[tuple[tuple[date, str], str]] = []
    for key in tos_backup_storage.list_object_keys(prefix):
        sort_key = _dump_sort_key(key)
        if sort_key is not None:
            candidates.append((sort_key, key))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def download_latest_db_dump(*, output_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    object_key = latest_db_dump_key()
    if not object_key:
        raise RuntimeError("no TOS DB dump found")
    base_dir = Path(output_dir) if output_dir is not None else Path(tempfile.gettempdir()) / "autovideosrt-tos-restore"
    base_dir.mkdir(parents=True, exist_ok=True)
    local_path = base_dir / Path(object_key).name
    tos_backup_storage.download_to_file(object_key, local_path)
    return {
        "object_key": object_key,
        "local_file": str(local_path),
        "bytes": local_path.stat().st_size if local_path.exists() else 0,
    }


def _mysql_args() -> list[str]:
    return [
        config.MYSQL_BIN,
        "--default-character-set=utf8mb4",
        "--host",
        config.DB_HOST,
        "--port",
        str(config.DB_PORT),
        "--user",
        config.DB_USER,
        config.DB_NAME,
    ]


def _decode_stderr(handle) -> str:
    try:
        handle.seek(0)
        data = handle.read() or b""
    except Exception:
        return ""
    if isinstance(data, str):
        return data.strip()
    return data.decode("utf-8", errors="replace").strip()


def restore_mysql_dump(dump_path: str | os.PathLike[str]) -> dict[str, Any]:
    path = Path(dump_path)
    if not path.is_file():
        raise RuntimeError(f"dump file not found: {path}")

    env = os.environ.copy()
    env["MYSQL_PWD"] = config.DB_PASSWORD or ""
    with tempfile.TemporaryFile() as stderr_handle:
        process = subprocess.Popen(
            _mysql_args(),
            stdin=subprocess.PIPE,
            stderr=stderr_handle,
            env=env,
        )
        if process.stdin is None:
            raise RuntimeError("mysql stdin pipe unavailable")
        with gzip.open(path, "rb") as source:
            shutil.copyfileobj(source, process.stdin)
        process.stdin.close()
        returncode = process.wait()
        if returncode != 0:
            stderr = _decode_stderr(stderr_handle)
            raise RuntimeError(f"mysql restore failed: {stderr}")

    return {
        "local_file": str(path),
        "bytes": path.stat().st_size,
    }


def restore_referenced_files() -> dict[str, Any]:
    refs = tos_backup_references.collect_protected_file_refs()
    actions: Counter[str] = Counter()
    errors: list[dict[str, str]] = []

    for ref in refs:
        try:
            result = tos_backup_storage.ensure_local_copy_for_local_path(ref.local_path)
        except Exception as exc:
            actions["failed"] += 1
            errors.append({"local_path": ref.local_path, "error": str(exc)})
            continue
        if result is None:
            actions["skipped"] += 1
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


def run_restore(
    *,
    output_dir: str | os.PathLike[str] | None = None,
    restore_db: bool = True,
    restore_files: bool = True,
    download_only: bool = False,
) -> dict[str, Any]:
    summary: dict[str, Any] = {"download_only": bool(download_only)}

    if restore_db:
        db_summary = download_latest_db_dump(output_dir=output_dir)
        summary["db_dump"] = db_summary
        if not download_only:
            summary["db_restore"] = restore_mysql_dump(db_summary["local_file"])

    if restore_files and not download_only:
        summary["files"] = restore_referenced_files()

    failed_files = int(((summary.get("files") or {}).get("failed") or 0) if isinstance(summary, dict) else 0)
    if failed_files:
        summary["status"] = "failed"
        summary["error_message"] = f"file restore failed for {failed_files} protected files"
    else:
        summary["status"] = "success"

    return summary
