"""Helpers for task start and restart request inputs."""

from __future__ import annotations


def parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "manual"}
    return bool(value)


def request_payload_from(request_obj) -> dict:
    if request_obj.is_json:
        return request_obj.get_json(silent=True) or {}
    return request_obj.form.to_dict(flat=True)


def json_payload_from(request_obj) -> dict:
    return request_obj.get_json(silent=True) or {}
