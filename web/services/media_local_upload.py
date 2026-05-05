"""Service helpers for local media upload completion."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LocalMediaUploadOutcome:
    status_code: int
    not_found: bool = False


def complete_local_media_upload(
    upload_id: str,
    *,
    user_id: int,
    stream,
    reservations: Mapping[str, dict],
    reservation_guard,
    write_stream_fn: Callable[[str, Any], Any],
) -> LocalMediaUploadOutcome:
    with reservation_guard:
        reservation = reservations.get(upload_id)
    if not reservation or int(reservation.get("user_id") or 0) != int(user_id):
        return LocalMediaUploadOutcome(404, not_found=True)

    write_stream_fn(reservation["object_key"], stream)
    return LocalMediaUploadOutcome(204)
