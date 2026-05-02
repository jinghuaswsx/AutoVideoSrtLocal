"""Helpers for task start and restart request inputs."""

from __future__ import annotations


def parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "manual"}
    return bool(value)
