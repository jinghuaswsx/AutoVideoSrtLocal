from __future__ import annotations

from decimal import Decimal
import logging
from typing import Any

from appcore import usage_log
from appcore.llm_use_cases import get_use_case
from appcore.pricing import compute_cost_cny


log = logging.getLogger(__name__)


def log_request(
    *,
    use_case_code: str,
    user_id: int | None,
    project_id: str | None,
    provider: str,
    model: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    request_units: int | None = None,
    units_type: str = "tokens",
    audio_duration_seconds: float | None = None,
    response_cost_cny: Decimal | None = None,
    success: bool = True,
    extra: dict | None = None,
    request_payload: Any = None,
    response_payload: Any = None,
) -> int | None:
    """Returns the new usage_logs row id, or None on failure."""
    if user_id is None:
        return None

    try:
        uc = get_use_case(use_case_code)
        module = uc["module"]
        service = uc["usage_log_service"]

        if units_type == "tokens" and request_units is None:
            request_units = (input_tokens or 0) + (output_tokens or 0)

        if response_cost_cny is not None:
            cost_cny, cost_source = response_cost_cny, "response"
        else:
            cost_cny, cost_source = compute_cost_cny(
                provider=provider,
                model=model,
                units_type=units_type,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                request_units=request_units,
            )

        log_id = usage_log.record(
            user_id,
            project_id,
            service,
            use_case_code=use_case_code,
            module=module,
            provider=provider,
            model_name=model,
            success=success,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            audio_duration_seconds=audio_duration_seconds,
            request_units=request_units,
            units_type=units_type,
            cost_cny=cost_cny,
            cost_source=cost_source,
            extra_data=extra,
        )
        if log_id and (request_payload is not None or response_payload is not None):
            usage_log.record_payload(log_id, request_payload, response_payload)
        return log_id
    except Exception:
        log.debug("ai_billing.log_request failed", exc_info=True)
        return None
