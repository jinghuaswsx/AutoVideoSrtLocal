"""Hourly cleanup for expired local project files."""

from __future__ import annotations

import json
import logging
import os

from appcore import object_keys
from appcore.db import execute, query
from appcore.safe_paths import PathSafetyError, remove_file_under_roots, remove_tree_under_roots
from config import OUTPUT_DIR, UPLOAD_DIR

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
        except Exception as exc:
            log.error("Cleanup failed for %s: %s", task_id, exc)

    zombie_rows = query(
        "SELECT id, task_dir, user_id, state_json FROM projects "
        "WHERE expires_at IS NULL "
        "AND type NOT IN ('image_translate', 'link_check') "
        "AND status NOT IN ('uploaded', 'running') "
        "AND created_at < NOW() - INTERVAL 30 DAY "
        "AND deleted_at IS NULL"
    )
    for row in zombie_rows:
        task_id = row["id"]
        try:
            delete_task_storage(row)
            execute(
                "UPDATE projects SET deleted_at = NOW(), status = 'expired' WHERE id = %s",
                (task_id,),
            )
            log.info("Cleaned up zombie project %s", task_id)
        except Exception as exc:
            log.error("Zombie cleanup failed for %s: %s", task_id, exc)

    try:
        _cleanup_orphan_uploads()
    except Exception as exc:
        log.error("Orphan upload file cleanup failed: %s", exc)


def delete_task_storage(task_or_row: dict) -> None:
    task_dir = (task_or_row.get("task_dir") or "").strip()
    if task_dir and os.path.isdir(task_dir):
        try:
            remove_tree_under_roots(task_dir, [OUTPUT_DIR], ignore_errors=True)
        except PathSafetyError:
            log.warning("Skip deleting task_dir outside OUTPUT_DIR: %s", task_dir)

    state = _load_task_state(task_or_row)
    video_path = state.get("video_path") or task_or_row.get("video_path") or ""
    if video_path and os.path.isfile(video_path):
        try:
            remove_file_under_roots(video_path, [UPLOAD_DIR, OUTPUT_DIR])
            log.info("Deleted upload file: %s", video_path)
        except PathSafetyError:
            log.warning("Skip deleting upload file outside storage roots: %s", video_path)
        except Exception:
            pass


def collect_task_tos_keys(task_or_row: dict | None) -> list[str]:
    """Compatibility helper for legacy callers; cleanup no longer deletes TOS."""
    state = _load_task_state(task_or_row or {})
    merged = dict(state)
    merged.update(task_or_row or {})
    return object_keys.collect_legacy_object_keys(merged)


def delete_stale_upload_objects() -> None:
    """Deprecated TOS cleanup hook kept as a no-op for scheduler compatibility."""
    return None


def _load_task_state(row: dict) -> dict:
    if row.get("steps") or row.get("variants"):
        return row
    try:
        return json.loads(row["state_json"]) if row.get("state_json") else {}
    except Exception:
        return {}


def _cleanup_orphan_uploads() -> None:
    """Remove upload files whose project has been deleted or does not exist."""
    if not UPLOAD_DIR or not os.path.isdir(UPLOAD_DIR):
        return

    alive_rows = query("SELECT id FROM projects WHERE deleted_at IS NULL")
    alive_ids = {row["id"] for row in alive_rows}

    for filename in os.listdir(UPLOAD_DIR):
        task_id = os.path.splitext(filename)[0]
        if task_id in alive_ids:
            continue
        file_path = os.path.join(UPLOAD_DIR, filename)
        if not os.path.isfile(file_path):
            continue
        try:
            os.remove(file_path)
            log.info("Deleted orphan upload: %s", filename)
        except Exception:
            pass
