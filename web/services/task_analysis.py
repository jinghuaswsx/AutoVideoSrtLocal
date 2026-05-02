"""Task AI analysis launch helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from appcore.db import query_one as db_query_one
from web.services import pipeline_runner
from web.services.task_access import load_task


_BUSY_ERROR = "AI 分析正在运行中"


@dataclass(frozen=True)
class TaskAnalysisOutcome:
    payload: dict[str, Any]
    status_code: int
    not_found: bool = False


def start_task_analysis(
    task_id: str,
    *,
    user_id: int,
    query_one=db_query_one,
    load_task: Callable[[str], dict | None] = load_task,
    run_analysis: Callable[..., bool] | None = None,
) -> TaskAnalysisOutcome:
    row = query_one(
        "SELECT id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, user_id),
    )
    if not row:
        return TaskAnalysisOutcome({}, 404, not_found=True)

    task = load_task(task_id)
    if not task:
        return TaskAnalysisOutcome({}, 404, not_found=True)

    if (task.get("steps") or {}).get("analysis") == "running":
        return TaskAnalysisOutcome({"error": _BUSY_ERROR}, 409)

    run_analysis = run_analysis or pipeline_runner.run_analysis
    if not run_analysis(task_id, user_id=user_id):
        return TaskAnalysisOutcome({"error": _BUSY_ERROR}, 409)

    return TaskAnalysisOutcome({"status": "started"}, 200)
