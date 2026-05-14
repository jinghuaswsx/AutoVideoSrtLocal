from __future__ import annotations

from dataclasses import dataclass

from flask import jsonify


@dataclass(frozen=True)
class VideoCoverResponse:
    payload: dict
    status_code: int = 200


def video_cover_flask_response(result: VideoCoverResponse):
    return jsonify(result.payload), result.status_code

