"""Fire-and-forget usage logging. Never raises."""
from __future__ import annotations
from decimal import Decimal
import logging
from typing import Any

log = logging.getLogger(__name__)


def record_payload(log_id: int, request_data: Any, response_data: Any) -> None:
    """Store request/response JSON for an existing usage log row. Never raises."""
    if not log_id:
        return
    try:
        import json
        from appcore.db import execute

        execute(
            """INSERT INTO usage_log_payloads (log_id, request_data, response_data)
               VALUES (%s, %s, %s)""",
            (
                log_id,
                json.dumps(request_data, ensure_ascii=False, default=str)
                if request_data is not None else None,
                json.dumps(response_data, ensure_ascii=False, default=str)
                if response_data is not None else None,
            ),
        )
    except Exception as e:
        log.debug("usage_log.record_payload failed: %s", e)


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
