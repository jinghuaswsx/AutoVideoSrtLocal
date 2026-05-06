"""Service responses for deprecated TOS upload endpoints."""

from __future__ import annotations

from dataclasses import dataclass

from flask import jsonify


@dataclass(frozen=True)
class TosUploadResponse:
    payload: dict
    status_code: int


def tos_upload_flask_response(result: TosUploadResponse):
    return jsonify(result.payload), result.status_code


def build_tos_upload_bootstrap_disabled_response() -> TosUploadResponse:
    return TosUploadResponse(
        {"error": "新建任务已切换为本地上传，通用 TOS 直传入口已停用"},
        410,
    )


def build_tos_upload_complete_disabled_response() -> TosUploadResponse:
    return TosUploadResponse(
        {"error": "新建任务已切换为本地上传，TOS complete 创建任务入口已停用"},
        410,
    )
