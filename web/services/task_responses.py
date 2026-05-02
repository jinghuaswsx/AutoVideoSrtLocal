"""Shared task route response helpers."""

from __future__ import annotations

from flask import jsonify


def task_not_found_response():
    return jsonify({"error": "Task not found"}), 404
