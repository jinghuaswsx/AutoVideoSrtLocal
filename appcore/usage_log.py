"""Fire-and-forget usage logging. Never raises."""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)


def record(
    user_id: int | None,
    project_id: str | None,
    service: str,
    *,
    model_name: str | None = None,
    success: bool = True,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    audio_duration_seconds: float | None = None,
    extra_data: dict | None = None,
) -> None:
    if user_id is None:
        return
    try:
        import json
        from appcore.db import execute
        execute(
            """INSERT INTO usage_logs
               (user_id, project_id, service, model_name, success,
                input_tokens, output_tokens, audio_duration_seconds, extra_data)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, project_id, service, model_name, int(success),
             input_tokens, output_tokens, audio_duration_seconds,
             json.dumps(extra_data) if extra_data else None),
        )
    except Exception as e:
        log.debug("usage_log.record failed: %s", e)
