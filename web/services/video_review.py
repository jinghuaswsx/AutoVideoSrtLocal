"""Service responses for video review routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class VideoReviewResponse:
    payload: dict[str, Any]
    status_code: int


def video_review_flask_response(result: VideoReviewResponse):
    return jsonify(result.payload), result.status_code


def build_video_review_missing_upload_response() -> VideoReviewResponse:
    return VideoReviewResponse({"error": "请上传视频"}, 400)


def build_video_review_unsupported_upload_response() -> VideoReviewResponse:
    return VideoReviewResponse({"error": "不支持的视频格式"}, 400)


def build_video_review_upload_success_response(task_id: str) -> VideoReviewResponse:
    return VideoReviewResponse({"id": task_id}, 201)


def build_video_review_not_found_response() -> VideoReviewResponse:
    return VideoReviewResponse({"error": "not found"}, 404)


def build_video_review_file_missing_response() -> VideoReviewResponse:
    return VideoReviewResponse({"error": "视频文件不存在"}, 400)


def build_video_review_file_too_large_response(size_mb: float) -> VideoReviewResponse:
    return VideoReviewResponse(
        {"error": f"视频文件过大（{size_mb:.1f}MB），请压缩到 100MB 以内"},
        400,
    )


def build_video_review_already_running_response() -> VideoReviewResponse:
    return VideoReviewResponse({"status": "already_running"}, 200)


def build_video_review_started_response() -> VideoReviewResponse:
    return VideoReviewResponse({"status": "started"}, 200)


def build_video_review_prompts_response(prompts: dict[str, Any]) -> VideoReviewResponse:
    return VideoReviewResponse(prompts, 200)


def build_video_review_forbidden_prompts_response() -> VideoReviewResponse:
    return VideoReviewResponse({"error": "仅管理员可修改提示词"}, 403)


def build_video_review_empty_prompts_response() -> VideoReviewResponse:
    return VideoReviewResponse({"error": "提示词不能为空"}, 400)


def build_video_review_prompts_saved_response(en: str, zh: str) -> VideoReviewResponse:
    return VideoReviewResponse({"status": "ok", "en": en, "zh": zh}, 200)


def build_video_review_delete_success_response() -> VideoReviewResponse:
    return VideoReviewResponse({"status": "ok"}, 200)
