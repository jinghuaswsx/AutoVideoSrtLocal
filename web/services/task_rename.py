"""Task rename validation and conflict resolution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class TaskRenameOutcome:
    display_name: str | None = None
    error: str | None = None
    status_code: int = 200

    @property
    def payload(self) -> dict | None:
        if self.display_name is None:
            return None
        return {"status": "ok", "display_name": self.display_name}


def prepare_task_rename(
    body: Mapping[str, object],
    *,
    user_id: int,
    task_id: str,
    resolve_name_conflict: Callable[..., str],
) -> TaskRenameOutcome:
    new_name = str(body.get("display_name") or "").strip()
    if not new_name:
        return TaskRenameOutcome(error="display_name required", status_code=400)
    if len(new_name) > 50:
        return TaskRenameOutcome(error="名称不超过50个字符", status_code=400)

    resolved = resolve_name_conflict(user_id, new_name, exclude_task_id=task_id)
    return TaskRenameOutcome(display_name=resolved)
