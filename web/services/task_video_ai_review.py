"""AV task video AI review helpers for route handlers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from web.services.task_access import is_admin_user, load_task as default_load_task


_SOURCE_TYPE = "av_sync_task"
_BUSY_ERROR = "AI 视频分析正在运行中"
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskVideoAiReviewOutcome:
    payload: dict[str, Any]
    status_code: int
    not_found: bool = False


def _review_module():
    from appcore import video_ai_review

    return video_ai_review


def _task_state_module():
    from appcore import task_state

    return task_state


def can_view_task_video_ai_review(
    task_id: str,
    *,
    user,
    load_task: Callable[[str], dict | None] = default_load_task,
) -> bool:
    task = load_task(task_id)
    if not task:
        return False
    if is_admin_user(user):
        return True
    return task.get("_user_id") == getattr(user, "id", None)


def start_task_video_ai_review(
    task_id: str,
    *,
    user,
    load_task: Callable[[str], dict | None] = default_load_task,
    review_module=None,
    logger: logging.Logger | None = None,
) -> TaskVideoAiReviewOutcome:
    if not can_view_task_video_ai_review(task_id, user=user, load_task=load_task):
        return TaskVideoAiReviewOutcome({}, 404, not_found=True)

    review_module = review_module or _review_module()
    try:
        run_id = review_module.trigger_review(
            source_type=_SOURCE_TYPE,
            source_id=task_id,
            user_id=getattr(user, "id", None),
            triggered_by="manual",
        )
    except review_module.ReviewInProgressError as exc:
        return TaskVideoAiReviewOutcome(
            {"error": _BUSY_ERROR, "in_flight_run_id": exc.run_id},
            409,
        )
    except Exception as exc:
        (logger or log).exception("[video-ai-review] av_sync trigger failed task=%s", task_id)
        return TaskVideoAiReviewOutcome({"error": str(exc)}, 500)

    return TaskVideoAiReviewOutcome(
        {
            "status": "started",
            "run_id": run_id,
            "channel": review_module.CHANNEL,
            "model": review_module.MODEL,
        },
        200,
    )


def get_task_video_ai_review(
    task_id: str,
    *,
    user,
    load_task: Callable[[str], dict | None] = default_load_task,
    review_module=None,
    task_state_module=None,
) -> TaskVideoAiReviewOutcome:
    if not can_view_task_video_ai_review(task_id, user=user, load_task=load_task):
        return TaskVideoAiReviewOutcome({}, 404, not_found=True)

    review_module = review_module or _review_module()
    task_state_module = task_state_module or _task_state_module()
    payload = review_module.latest_review(_SOURCE_TYPE, task_id)
    ts_state = task_state_module.get(task_id) or {}
    return TaskVideoAiReviewOutcome(
        {
            "review": payload,
            "task_evals_invalidated_at": ts_state.get("evals_invalidated_at"),
        },
        200,
    )
