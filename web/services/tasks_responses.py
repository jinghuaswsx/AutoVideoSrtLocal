"""Shared Flask JSON responses for task-center route modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class TasksRouteResponse:
    payload: Any
    status_code: int


def tasks_flask_response(result: TasksRouteResponse):
    return jsonify(result.payload), result.status_code


def build_tasks_payload_response(
    payload: Any,
    status_code: int = 200,
) -> TasksRouteResponse:
    return TasksRouteResponse(payload, status_code)


def build_tasks_error_response(
    error: str,
    status_code: int,
    **extra: Any,
) -> TasksRouteResponse:
    return TasksRouteResponse({"error": error, **extra}, status_code)
