"""Hourly cleanup: delete expired project files and TOS objects."""
from __future__ import annotations
import os
import shutil
import logging

from appcore.db import query, execute

log = logging.getLogger(__name__)


def run_cleanup() -> None:
    rows = query(
        "SELECT id, task_dir, user_id, state_json FROM projects "
        "WHERE expires_at < NOW() AND deleted_at IS NULL"
    )
    for row in rows:
        task_id = row["id"]
        task_dir = row.get("task_dir") or ""
        try:
            if task_dir and os.path.isdir(task_dir):
                shutil.rmtree(task_dir, ignore_errors=True)
            _delete_tos_objects(row)
            execute(
                "UPDATE projects SET deleted_at = NOW(), status = 'expired' WHERE id = %s",
                (task_id,),
            )
            log.info("Cleaned up expired project %s", task_id)
        except Exception as e:
            log.error("Cleanup failed for %s: %s", task_id, e)


def _delete_tos_objects(row: dict) -> None:
    try:
        import json
        import tos as tos_sdk
        import config
        state = json.loads(row["state_json"]) if row.get("state_json") else {}
        tos_uploads = state.get("tos_uploads", {})
        if not tos_uploads:
            return
        client = tos_sdk.TosClientV2(
            ak=config.TOS_ACCESS_KEY, sk=config.TOS_SECRET_KEY,
            endpoint=config.TOS_ENDPOINT, region=config.TOS_REGION,
        )
        for tos_key in tos_uploads:
            try:
                client.delete_object(config.TOS_BUCKET, tos_key)
            except Exception:
                pass
    except Exception:
        pass
