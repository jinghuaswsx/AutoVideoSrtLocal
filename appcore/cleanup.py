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
    # ── 清理已过期的项目 ──
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

    # ── 僵尸项目兜底清理 ──
    zombie_rows = query(
        "SELECT id, task_dir, user_id, state_json FROM projects "
        "WHERE expires_at IS NULL "
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
        except Exception as e:
            log.error("Zombie cleanup failed for %s: %s", task_id, e)

    # ── 清理 uploads 目录中的孤儿文件 ──
    try:
        _cleanup_orphan_uploads()
    except Exception as e:
        log.error("Orphan upload file cleanup failed: %s", e)

    try:
        _trim_local_uploads_with_tos_backup()
    except Exception as e:
        log.error("TOS-backed local upload trim failed: %s", e)

    try:
        delete_stale_upload_objects()
    except Exception as e:
        log.error("Orphan upload cleanup failed: %s", e)


def delete_task_storage(task_or_row: dict) -> None:
    task_dir = (task_or_row.get("task_dir") or "").strip()
    if task_dir and os.path.isdir(task_dir):
        shutil.rmtree(task_dir, ignore_errors=True)

    # 清理 uploads 目录中的原始视频文件
    state = _load_task_state(task_or_row)
    video_path = state.get("video_path") or task_or_row.get("video_path") or ""
    if video_path and os.path.isfile(video_path):
        try:
            os.remove(video_path)
            log.info("Deleted upload file: %s", video_path)
        except Exception:
            pass

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


def _cleanup_orphan_uploads() -> None:
    """Remove upload files whose project has been deleted or doesn't exist."""
    from config import UPLOAD_DIR
    if not UPLOAD_DIR or not os.path.isdir(UPLOAD_DIR):
        return

    alive_rows = query("SELECT id FROM projects WHERE deleted_at IS NULL")
    alive_ids = {r["id"] for r in alive_rows}

    for filename in os.listdir(UPLOAD_DIR):
        task_id = os.path.splitext(filename)[0]
        if task_id in alive_ids:
            continue
        fpath = os.path.join(UPLOAD_DIR, filename)
        if not os.path.isfile(fpath):
            continue
        try:
            os.remove(fpath)
            log.info("Deleted orphan upload: %s", filename)
        except Exception:
            pass


def _trim_local_uploads_with_tos_backup() -> None:
    """Delete local upload files for finished tasks that have a TOS backup.

    Policy: if a task is not actively running (status done/error/composing_done)
    and has a non-empty source_tos_key, the local mp4 in UPLOAD_DIR is
    redundant — it can be re-fetched from TOS on demand via
    appcore.source_video.ensure_local_source_video().
    Keeps disk usage low without losing recoverability.
    """
    from config import UPLOAD_DIR
    if not UPLOAD_DIR or not os.path.isdir(UPLOAD_DIR):
        return

    rows = query(
        "SELECT id, state_json FROM projects "
        "WHERE deleted_at IS NULL AND status IN ('done', 'error', 'composing_done')"
    )
    trimmed = 0
    for row in rows:
        try:
            state = json.loads(row["state_json"]) if row.get("state_json") else {}
        except Exception:
            continue
        source_tos_key = (state.get("source_tos_key") or "").strip()
        video_path = (state.get("video_path") or "").strip()
        if not source_tos_key or not video_path:
            continue
        if not video_path.startswith(os.path.abspath(UPLOAD_DIR) + os.sep) \
                and not video_path.startswith(UPLOAD_DIR):
            continue
        if not os.path.isfile(video_path):
            continue
        try:
            os.remove(video_path)
            trimmed += 1
            log.info("Trimmed TOS-backed local upload: %s (task %s)", video_path, row["id"])
        except Exception:
            pass
    if trimmed:
        log.info("Trimmed %d local upload files backed by TOS", trimmed)
