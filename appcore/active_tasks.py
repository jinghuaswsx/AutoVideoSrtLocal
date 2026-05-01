from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any

from appcore.db import execute as db_execute, query as db_query

log = logging.getLogger(__name__)

SAFE_INTERRUPT_PROJECT_TYPES = {"link_check"}
CAUTIOUS_INTERRUPT_PROJECT_TYPES = {"subtitle_removal", "image_translate"}
BLOCK_RESTART_PROJECT_TYPES = {
    "translation",
    "de_translate",
    "fr_translate",
    "ja_translate",
    "copywriting",
    "multi_translate",
    "omni_translate",
    "translate_lab",
    "av_translate",
    "video_creation",
    "video_review",
}

_active_tasks: dict[tuple[str, str], "ActiveTask"] = {}
_active_lock = threading.RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def default_interrupt_policy(project_type: str) -> str:
    code = _normalize_text(project_type)
    if code in SAFE_INTERRUPT_PROJECT_TYPES:
        return "safe"
    if code in CAUTIOUS_INTERRUPT_PROJECT_TYPES:
        return "cautious"
    if code in BLOCK_RESTART_PROJECT_TYPES:
        return "block_restart"
    return "block_restart"


@dataclass(slots=True)
class ActiveTask:
    project_type: str
    task_id: str
    user_id: int | None = None
    runner: str = ""
    entrypoint: str = ""
    stage: str = ""
    thread_name: str = ""
    process_id: int = field(default_factory=os.getpid)
    interrupt_policy: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=_now)
    last_heartbeat_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.project_type = _normalize_text(self.project_type)
        self.task_id = _normalize_text(self.task_id)
        self.runner = _normalize_text(self.runner)
        self.entrypoint = _normalize_text(self.entrypoint)
        self.stage = _normalize_text(self.stage)
        self.thread_name = _normalize_text(self.thread_name)
        self.interrupt_policy = (
            _normalize_text(self.interrupt_policy)
            or default_interrupt_policy(self.project_type)
        )
        self.started_at = _parse_datetime(self.started_at) or _now()
        self.last_heartbeat_at = _parse_datetime(self.last_heartbeat_at) or self.started_at

    @property
    def key(self) -> tuple[str, str]:
        return (self.project_type, self.task_id)

    def touch(self, *, stage: str | None = None, details: dict[str, Any] | None = None) -> None:
        if stage is not None:
            self.stage = _normalize_text(stage)
        if details:
            self.details.update(details)
        self.last_heartbeat_at = _now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_type": self.project_type,
            "task_id": self.task_id,
            "user_id": self.user_id,
            "runner": self.runner,
            "entrypoint": self.entrypoint,
            "stage": self.stage,
            "thread_name": self.thread_name,
            "process_id": self.process_id,
            "interrupt_policy": self.interrupt_policy,
            "details": dict(self.details or {}),
            "started_at": self.started_at.isoformat(),
            "last_heartbeat_at": self.last_heartbeat_at.isoformat(),
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ActiveTask":
        details = row.get("details_json") or row.get("details") or {}
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except json.JSONDecodeError:
                details = {"raw": details}
        return cls(
            project_type=row.get("project_type") or "",
            task_id=row.get("task_id") or "",
            user_id=row.get("user_id"),
            runner=row.get("runner") or "",
            entrypoint=row.get("entrypoint") or "",
            stage=row.get("stage") or "",
            thread_name=row.get("thread_name") or "",
            process_id=int(row.get("process_id") or 0),
            interrupt_policy=row.get("interrupt_policy") or "",
            details=details if isinstance(details, dict) else {},
            started_at=row.get("started_at"),
            last_heartbeat_at=row.get("last_heartbeat_at"),
        )


def _truthy(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"", "0", "false", "no", "off", "disabled"}


def _database_enabled() -> bool:
    explicit = os.getenv("AUTOVIDEOSRT_ACTIVE_TASK_DB_ENABLED")
    if explicit is not None:
        return _truthy(explicit, default=True)
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    try:
        from config import DB_HOST
    except Exception:
        DB_HOST = ""
    host = _normalize_text(DB_HOST).lower()
    if os.name == "nt" and host in {"127.0.0.1", "localhost", "::1"}:
        return False
    return True


def _persist_live_task(task: ActiveTask) -> None:
    if not _database_enabled():
        return
    try:
        db_execute(
            """
            INSERT INTO runtime_active_tasks
            (task_key, project_type, task_id, user_id, runner, entrypoint, stage,
             thread_name, process_id, interrupt_policy, started_at, last_heartbeat_at,
             details_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              user_id=VALUES(user_id),
              runner=VALUES(runner),
              entrypoint=VALUES(entrypoint),
              stage=VALUES(stage),
              thread_name=VALUES(thread_name),
              process_id=VALUES(process_id),
              interrupt_policy=VALUES(interrupt_policy),
              started_at=VALUES(started_at),
              last_heartbeat_at=VALUES(last_heartbeat_at),
              details_json=VALUES(details_json)
            """,
            (
                f"{task.project_type}:{task.task_id}",
                task.project_type,
                task.task_id,
                task.user_id,
                task.runner,
                task.entrypoint,
                task.stage,
                task.thread_name,
                task.process_id,
                task.interrupt_policy,
                task.started_at.replace(tzinfo=None),
                task.last_heartbeat_at.replace(tzinfo=None),
                json.dumps(task.details or {}, ensure_ascii=False, default=_json_default),
            ),
        )
    except Exception:
        log.warning(
            "failed to persist active task project_type=%s task_id=%s",
            task.project_type,
            task.task_id,
            exc_info=True,
        )


def _delete_live_task(project_type: str, task_id: str) -> None:
    if not _database_enabled():
        return
    try:
        db_execute(
            "DELETE FROM runtime_active_tasks WHERE task_key = %s",
            (f"{_normalize_text(project_type)}:{_normalize_text(task_id)}",),
        )
    except Exception:
        log.warning(
            "failed to delete active task project_type=%s task_id=%s",
            project_type,
            task_id,
            exc_info=True,
        )


def register(
    project_type: str,
    task_id: str,
    *,
    user_id: int | None = None,
    runner: str = "",
    entrypoint: str = "",
    stage: str = "",
    thread_name: str = "",
    details: dict[str, Any] | None = None,
    interrupt_policy: str | None = None,
) -> ActiveTask:
    task = ActiveTask(
        project_type=project_type,
        task_id=task_id,
        user_id=user_id,
        runner=runner,
        entrypoint=entrypoint,
        stage=stage,
        thread_name=thread_name,
        details=dict(details or {}),
        interrupt_policy=interrupt_policy or "",
    )
    with _active_lock:
        _active_tasks[task.key] = task
    _persist_live_task(task)
    return task


def try_register(
    project_type: str,
    task_id: str,
    *,
    user_id: int | None = None,
    runner: str = "",
    entrypoint: str = "",
    stage: str = "",
    thread_name: str = "",
    details: dict[str, Any] | None = None,
    interrupt_policy: str | None = None,
) -> bool:
    key = (_normalize_text(project_type), _normalize_text(task_id))
    with _active_lock:
        if key in _active_tasks:
            return False
        task = ActiveTask(
            project_type=project_type,
            task_id=task_id,
            user_id=user_id,
            runner=runner,
            entrypoint=entrypoint,
            stage=stage,
            thread_name=thread_name,
            details=dict(details or {}),
            interrupt_policy=interrupt_policy or "",
        )
        _active_tasks[key] = task
    _persist_live_task(task)
    return True


def unregister(project_type: str, task_id: str) -> None:
    key = (_normalize_text(project_type), _normalize_text(task_id))
    with _active_lock:
        _active_tasks.pop(key, None)
    _delete_live_task(project_type, task_id)


def is_active(project_type: str, task_id: str) -> bool:
    key = (_normalize_text(project_type), _normalize_text(task_id))
    with _active_lock:
        return key in _active_tasks


def heartbeat_active_task(
    project_type: str,
    task_id: str,
    *,
    stage: str | None = None,
    details: dict[str, Any] | None = None,
) -> bool:
    key = (_normalize_text(project_type), _normalize_text(task_id))
    with _active_lock:
        task = _active_tasks.get(key)
        if task is None:
            return False
        task.touch(stage=stage, details=details)
    _persist_live_task(task)
    return True


def list_active_tasks() -> list[ActiveTask]:
    with _active_lock:
        return sorted(
            list(_active_tasks.values()),
            key=lambda task: (task.started_at, task.project_type, task.task_id),
        )


def clear_active_tasks_for_tests() -> None:
    with _active_lock:
        _active_tasks.clear()


def _snapshot_path() -> Path:
    explicit = os.getenv("AUTOVIDEOSRT_ACTIVE_TASK_SNAPSHOT_PATH")
    if explicit:
        return Path(explicit)
    log_dir = os.getenv("AUTOVIDEOSRT_LOG_DIR")
    if log_dir:
        return Path(log_dir) / "active-task-snapshots.jsonl"
    return Path("logs") / "active-task-snapshots.jsonl"


def _write_snapshot_file(reason: str, tasks: list[ActiveTask]) -> None:
    path = _snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "reason": reason,
        "captured_at": _now().isoformat(),
        "active_tasks": [task.to_dict() for task in tasks],
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=_json_default))
        fh.write("\n")


def _persist_snapshot_rows(reason: str, tasks: list[ActiveTask]) -> None:
    if not tasks:
        return
    for task in tasks:
        db_execute(
            """
            INSERT INTO runtime_active_task_snapshots
            (snapshot_reason, project_type, task_id, user_id, runner, entrypoint,
             stage, thread_name, process_id, interrupt_policy, started_at,
             last_heartbeat_at, details_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                reason,
                task.project_type,
                task.task_id,
                task.user_id,
                task.runner,
                task.entrypoint,
                task.stage,
                task.thread_name,
                task.process_id,
                task.interrupt_policy,
                task.started_at.replace(tzinfo=None),
                task.last_heartbeat_at.replace(tzinfo=None),
                json.dumps(task.details or {}, ensure_ascii=False, default=_json_default),
            ),
        )


def snapshot_active_tasks(reason: str, tasks: list[ActiveTask] | None = None) -> dict[str, Any]:
    selected = list(tasks) if tasks is not None else list_active_tasks()
    snapshot_reason = _normalize_text(reason) or "manual"
    if _database_enabled():
        try:
            _persist_snapshot_rows(snapshot_reason, selected)
            return {"count": len(selected), "target": "database"}
        except Exception:
            log.warning("failed to persist active task snapshot; falling back to jsonl", exc_info=True)
    _write_snapshot_file(snapshot_reason, selected)
    return {"count": len(selected), "target": str(_snapshot_path())}


def load_persisted_active_tasks(max_age_seconds: int = 30) -> list[ActiveTask]:
    if not _database_enabled():
        return []
    # Existing runners do not all emit periodic heartbeat yet. Treat live rows
    # as blockers until normal unregister deletes them; operators can override
    # stale records with the CLI --force path, which also snapshots evidence.
    _ = max_age_seconds
    rows = db_query(
        """
        SELECT project_type, task_id, user_id, runner, entrypoint, stage,
               thread_name, process_id, interrupt_policy, started_at,
               last_heartbeat_at, details_json
        FROM runtime_active_tasks
        ORDER BY last_heartbeat_at DESC
        """,
        (),
    )
    return [ActiveTask.from_row(row) for row in rows]
