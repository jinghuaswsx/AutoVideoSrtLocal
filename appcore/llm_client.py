"""统一 LLM 调用入口。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable

from appcore import ai_billing, llm_bindings
from appcore.llm_providers import get_adapter

log = logging.getLogger(__name__)


def _log_usage(*, use_case_code: str, user_id: int | None,
               project_id: str | None, provider: str, model: str,
               success: bool, usage: dict | None,
               error: Exception | None = None) -> None:
    if user_id is None:
        return

    usage_data = usage or {}
    extra: dict[str, Any] = {"use_case": use_case_code}
    if error is not None:
        extra["error"] = str(error)[:500]

    try:
        ai_billing.log_request(
            use_case_code=use_case_code,
            user_id=user_id,
            project_id=project_id,
            provider=provider,
            model=model,
            input_tokens=usage_data.get("input_tokens"),
            output_tokens=usage_data.get("output_tokens"),
            response_cost_cny=usage_data.get("cost_cny"),
            success=success,
            extra=extra,
        )
    except Exception:
        log.debug("ai_billing.log_request failed for use_case=%s",
                  use_case_code, exc_info=True)


def invoke_chat(
    use_case_code: str,
    *,
    messages: list[dict],
    user_id: int | None = None,
    project_id: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format: dict | None = None,
    extra_body: dict | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> dict:
    binding = llm_bindings.resolve(use_case_code)
    provider = provider_override or binding["provider"]
    model = model_override or binding["model"]
    adapter = get_adapter(provider)
    try:
        result = adapter.chat(
            model=model, messages=messages, user_id=user_id,
            temperature=temperature, max_tokens=max_tokens,
            response_format=response_format, extra_body=extra_body,
        )
    except Exception as e:
        _log_usage(use_case_code=use_case_code, user_id=user_id,
                   project_id=project_id, provider=provider, model=model,
                   success=False, usage=None, error=e)
        raise
    _log_usage(use_case_code=use_case_code, user_id=user_id,
               project_id=project_id, provider=provider, model=model,
               success=True, usage=result.get("usage"))
    return result


def invoke_generate(
    use_case_code: str,
    *,
    prompt: str,
    user_id: int | None = None,
    project_id: str | None = None,
    system: str | None = None,
    media: Iterable[str | Path] | str | Path | None = None,
    response_schema: dict | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> dict:
    binding = llm_bindings.resolve(use_case_code)
    provider = provider_override or binding["provider"]
    model = model_override or binding["model"]
    adapter = get_adapter(provider)
    try:
        result = adapter.generate(
            model=model, prompt=prompt, user_id=user_id,
            system=system, media=media,
            response_schema=response_schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
    except Exception as e:
        _log_usage(use_case_code=use_case_code, user_id=user_id,
                   project_id=project_id, provider=provider, model=model,
                   success=False, usage=None, error=e)
        raise
    _log_usage(use_case_code=use_case_code, user_id=user_id,
               project_id=project_id, provider=provider, model=model,
               success=True, usage=result.get("usage"))
    return result
