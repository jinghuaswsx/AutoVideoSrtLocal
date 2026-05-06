"""Shared Flask JSON response helpers for media detail-image services."""

from __future__ import annotations

from flask import jsonify


def detail_image_json_flask_response(result):
    payload = getattr(result, "payload", None)
    if payload is None:
        error = getattr(result, "error", None)
        payload = {"error": error} if error is not None else {}
    return jsonify(payload), getattr(result, "status_code", 200)
