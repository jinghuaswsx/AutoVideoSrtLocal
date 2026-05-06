"""Small helpers for persisted LLM prompt/request debug payloads."""

from __future__ import annotations

from typing import Any


def build_chat_request_payload(
    *,
    use_case_code: str | None,
    provider: str | None,
    model: str | None,
    messages: list[dict] | None,
    response_format: dict | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra: dict | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "type": "chat",
        "use_case_code": use_case_code,
        "provider": provider,
        "model": model,
        "messages": messages or [],
    }
    if response_format is not None:
        payload["response_format"] = response_format
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if extra:
        payload.update(extra)
    return payload


def build_generate_request_payload(
    *,
    use_case_code: str | None,
    provider: str | None,
    model: str | None,
    prompt: str | None,
    system: str | None = None,
    media: list[str] | None = None,
    response_schema: dict | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    extra: dict | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "type": "generate",
        "use_case_code": use_case_code,
        "provider": provider,
        "model": model,
        "prompt": prompt or "",
    }
    if system is not None:
        payload["system"] = system
    if media:
        payload["media"] = media
    if response_schema is not None:
        payload["response_schema"] = response_schema
    if temperature is not None:
        payload["temperature"] = temperature
    if max_output_tokens is not None:
        payload["max_output_tokens"] = max_output_tokens
    if extra:
        payload.update(extra)
    return payload


def prompt_file_payload(
    *,
    phase: str,
    label: str,
    use_case_code: str,
    provider: str | None,
    model: str | None,
    messages: list[dict] | None,
    request_payload: dict | None = None,
    input_snapshot: list[dict] | None = None,
    meta: dict | None = None,
) -> dict:
    payload = {
        "phase": phase,
        "label": label,
        "use_case_code": use_case_code,
        "provider": provider,
        "model": model,
        "messages": messages or [],
        "request_payload": request_payload or build_chat_request_payload(
            use_case_code=use_case_code,
            provider=provider,
            model=model,
            messages=messages or [],
        ),
    }
    if input_snapshot:
        payload["input_snapshot"] = input_snapshot
    if meta:
        payload.update(meta)
    return payload
