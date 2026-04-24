"""Fire-and-forget usage logging. Never raises."""
from __future__ import annotations
from decimal import Decimal
import logging

log = logging.getLogger(__name__)


def record(
    user_id: int | None,
    project_id: str | None,
    service: str,
    *,
    use_case_code: str | None = None,
    module: str | None = None,
    provider: str | None = None,
    model_name: str | None = None,
    success: bool = True,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    audio_duration_seconds: float | None = None,
    request_units: int | None = None,
    units_type: str | None = None,
    cost_cny: Decimal | None = None,
    cost_source: str = "unknown",
    extra_data: dict | None = None,
) -> int | None:
    """Returns the new row's id, or None on failure."""
    if user_id is None:
        return None
    try:
        import json
        from appcore.db import execute
        return execute(
            """INSERT INTO usage_logs
               (user_id, project_id, service, use_case_code, module, provider,
                model_name, success, input_tokens, output_tokens,
                audio_duration_seconds, request_units, units_type, cost_cny,
                cost_source, extra_data)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, project_id, service, use_case_code, module, provider,
             model_name, int(success), input_tokens, output_tokens,
             audio_duration_seconds, request_units, units_type, cost_cny,
             cost_source, json.dumps(extra_data) if extra_data else None),
        ) or None
    except Exception as e:
        log.debug("usage_log.record failed: %s", e)
        return None
