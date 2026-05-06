"""Task rename validation and conflict resolution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from appcore.project_state import (
    get_project_for_user,
    resolve_project_display_name_conflict,
    update_project_display_name,
)
from web import store
from web.services.task_access import load_task as load_task_from_store


@dataclass(frozen=True)
class TaskRenameOutcome:
    display_name: str | None = None
    error: str | None = None
    status_code: int = 200
    not_found: bool = False

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


def rename_task_display_name(
    task_id: str,
    body: Mapping[str, object],
    *,
    user_id: int,
    load_project_for_user: Callable[[str, int], dict | None] = get_project_for_user,
    update_display_name: Callable[[str, str], object] = update_project_display_name,
    load_task: Callable[..., dict | None] = load_task_from_store,
    update_task: Callable[..., object] = store.update,
    resolve_name_conflict: Callable[..., str] | None = None,
    query_one: Callable[..., dict | None] | None = None,
    execute: Callable[..., object] | None = None,
) -> TaskRenameOutcome:
    if load_project_for_user is get_project_for_user and query_one is not None:
        row = load_project_for_user(task_id, user_id, query_one_func=query_one)
    else:
        row = load_project_for_user(task_id, user_id)
    if not row:
        return TaskRenameOutcome(status_code=404, not_found=True)

    if resolve_name_conflict is None:

        def resolve_name_conflict(user_id, desired_name, *, exclude_task_id=None):
            if query_one is not None:
                return resolve_project_display_name_conflict(
                    user_id,
                    desired_name,
                    query_one_func=query_one,
                    exclude_task_id=exclude_task_id,
                )
            return resolve_project_display_name_conflict(
                user_id,
                desired_name,
                exclude_task_id=exclude_task_id,
            )

    outcome = prepare_task_rename(
        body,
        user_id=user_id,
        task_id=task_id,
        resolve_name_conflict=resolve_name_conflict,
    )
    if outcome.error:
        return outcome

    resolved = outcome.display_name
    if update_display_name is update_project_display_name and execute is not None:
        update_display_name(task_id, resolved, execute_func=execute)
    else:
        update_display_name(task_id, resolved)
    load_task(task_id)
    update_task(task_id, display_name=resolved)
    return outcome
