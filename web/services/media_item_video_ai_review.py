"""Service helpers for media item video AI review routes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from flask import jsonify


_SOURCE_TYPE = "media_item"
_BUSY_ERROR = "AI 视频分析正在运行中"
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MediaItemVideoAiReviewOutcome:
    payload: dict[str, Any]
    status_code: int


def media_item_video_ai_review_flask_response(outcome: MediaItemVideoAiReviewOutcome):
    return jsonify(outcome.payload), outcome.status_code


def _review_module():
    from appcore import video_ai_review

    return video_ai_review


def start_media_item_video_ai_review(
    item_id: int,
    *,
    user_id: int | None,
    review_module=None,
    logger: logging.Logger | None = None,
) -> MediaItemVideoAiReviewOutcome:
    review_module = review_module or _review_module()
    try:
        run_id = review_module.trigger_review(
            source_type=_SOURCE_TYPE,
            source_id=str(item_id),
            user_id=user_id,
            triggered_by="manual",
        )
    except review_module.ReviewInProgressError as exc:
        return MediaItemVideoAiReviewOutcome(
            {"error": _BUSY_ERROR, "in_flight_run_id": exc.run_id},
            409,
        )
    except Exception as exc:
        (logger or log).exception("[video-ai-review] media_item trigger failed item=%s", item_id)
        return MediaItemVideoAiReviewOutcome({"error": str(exc)}, 500)

    return MediaItemVideoAiReviewOutcome(
        {
            "status": "started",
            "run_id": run_id,
            "channel": review_module.CHANNEL,
            "model": review_module.MODEL,
        },
        200,
    )


def get_media_item_video_ai_review(
    item_id: int,
    *,
    review_module=None,
) -> MediaItemVideoAiReviewOutcome:
    review_module = review_module or _review_module()
    payload = review_module.latest_review(_SOURCE_TYPE, str(item_id))
    return MediaItemVideoAiReviewOutcome({"review": payload}, 200)
