"""Hourly cleanup: delete expired project files and TOS objects."""
from __future__ import annotations
import json
import os
import shutil
import logging
from datetime import datetime, timezone

from appcore.db import query, execute
from appcore import tos_clients
from config import TOS_BROWSER_UPLOAD_PREFIX, TOS_UPLOAD_CLEANUP_MAX_AGE_SECONDS

log = logging.getLogger(__name__)


def run_cleanup() -> None:
    rows = query(
        "SELECT id, task_dir, user_id, state_json FROM projects "
        "WHERE expires_at < NOW() AND deleted_at IS NULL"
    )
    for row in rows:
        task_id = row["id"]
        try:
            delete_task_storage(row)
            execute(
                "UPDATE projects SET deleted_at = NOW(), status = 'expired' WHERE id = %s",
                (task_id,),
            )
            log.info("Cleaned up expired project %s", task_id)
        except Exception as e:
            log.error("Cleanup failed for %s: %s", task_id, e)
    try:
        delete_stale_upload_objects()
    except Exception as e:
        log.error("Orphan upload cleanup failed: %s", e)


def delete_task_storage(task_or_row: dict) -> None:
    task_dir = (task_or_row.get("task_dir") or "").strip()
    if task_dir and os.path.isdir(task_dir):
        shutil.rmtree(task_dir, ignore_errors=True)
    tos_keys = task_or_row.get("tos_keys") or collect_task_tos_keys(task_or_row)
    for tos_key in tos_keys:
        try:
            tos_clients.delete_object(tos_key)
        except Exception:
            pass


def collect_task_tos_keys(task_or_row: dict | None) -> list[str]:
    state = _load_task_state(task_or_row or {})
    merged = dict(state)
    merged.update(task_or_row or {})
    return tos_clients.collect_task_tos_keys(merged)


def delete_stale_upload_objects() -> None:
    if not tos_clients.is_tos_configured() or TOS_UPLOAD_CLEANUP_MAX_AGE_SECONDS <= 0:
        return

    prefix = TOS_BROWSER_UPLOAD_PREFIX.strip("/")
    if not prefix:
        return

    objects = tos_clients.list_objects(prefix)
    if not objects:
        return

    now = _utcnow()
    cutoff = TOS_UPLOAD_CLEANUP_MAX_AGE_SECONDS
    stale_objects = []
    candidate_task_ids = set()

    for obj in objects:
        key = (getattr(obj, "key", "") or "").strip()
        if not key.startswith(f"{prefix}/"):
            continue
        task_id = _extract_upload_task_id(key, prefix)
        if not task_id:
            continue
        age_seconds = _object_age_seconds(obj, now)
        if age_seconds is None or age_seconds < cutoff:
            continue
        stale_objects.append((key, task_id))
        candidate_task_ids.add(task_id)

    if not stale_objects:
        return

    active_task_ids = _load_active_task_ids(candidate_task_ids)
    for key, task_id in stale_objects:
        if task_id in active_task_ids:
            continue
        try:
            tos_clients.delete_object(key)
        except Exception:
            pass


def _load_task_state(row: dict) -> dict:
    if row.get("steps") or row.get("variants"):
        return row
    try:
        return json.loads(row["state_json"]) if row.get("state_json") else {}
    except Exception:
        return {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _extract_upload_task_id(key: str, prefix: str) -> str:
    parts = key.split("/")
    prefix_parts = [segment for segment in prefix.split("/") if segment]
    if len(parts) < len(prefix_parts) + 3:
        return ""
    if parts[: len(prefix_parts)] != prefix_parts:
        return ""
    return parts[len(prefix_parts) + 1]


def _object_age_seconds(obj, now: datetime) -> float | None:
    last_modified = getattr(obj, "last_modified", None)
    if not isinstance(last_modified, datetime):
        return None
    if last_modified.tzinfo is None:
        last_modified = last_modified.replace(tzinfo=timezone.utc)
    return (now - last_modified).total_seconds()


def _load_active_task_ids(task_ids: set[str]) -> set[str]:
    if not task_ids:
        return set()

    placeholders = ", ".join(["%s"] * len(task_ids))
    rows = query(
        f"SELECT id FROM projects WHERE id IN ({placeholders}) AND deleted_at IS NULL",
        tuple(sorted(task_ids)),
    )
    return {row["id"] for row in rows if row.get("id")}
