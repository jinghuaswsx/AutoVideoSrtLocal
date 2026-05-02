"""Task CapCut deployment helpers."""

from __future__ import annotations

from collections.abc import Callable

from pipeline.capcut import deploy_capcut_project
from web import store
from web.services.artifact_download import safe_task_dir_path


def deploy_task_capcut_project(
    task_id: str,
    task: dict,
    *,
    variant: str | None = None,
    resolve_safe_dir: Callable[[dict, str | None], str | None] | None = None,
    deploy_project: Callable[[str], str] | None = None,
    update_task: Callable[..., object] | None = None,
    update_variant: Callable[..., object] | None = None,
) -> dict | None:
    variant_state = task.get("variants", {}).get(variant, {}) if variant else {}
    exports = variant_state.get("exports", {}) if variant else task.get("exports", {})
    project_dir = exports.get("capcut_project")

    resolve_safe_dir = resolve_safe_dir or safe_task_dir_path
    safe_project_dir = resolve_safe_dir(task, project_dir)
    if not safe_project_dir:
        return None

    deploy_project = deploy_project or deploy_capcut_project
    deployed_project_dir = deploy_project(safe_project_dir)

    updated_exports = dict(exports)
    updated_exports["jianying_project_dir"] = deployed_project_dir

    if variant:
        update_variant = update_variant or store.update_variant
        update_variant(task_id, variant, exports=updated_exports)
    else:
        update_task = update_task or store.update
        update_task(task_id, exports=updated_exports)

    return {"deployed_project_dir": deployed_project_dir}
