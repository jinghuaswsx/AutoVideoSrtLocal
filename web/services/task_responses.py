"""Shared task route response helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class TaskRouteResponse:
    payload: Any
    status_code: int


def task_flask_response(result: TaskRouteResponse):
    return jsonify(result.payload), result.status_code


def build_task_payload_response(
    payload: Any,
    status_code: int = 200,
) -> TaskRouteResponse:
    return TaskRouteResponse(payload, status_code)


def build_task_error_response(
    error: str,
    status_code: int,
    **extra: Any,
) -> TaskRouteResponse:
    return TaskRouteResponse({"error": error, **extra}, status_code)


def task_not_found_response():
    return task_flask_response(build_task_error_response("Task not found", 404))
