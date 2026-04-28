"""统一 LLM 调用入口。"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterable

from appcore import ai_billing, llm_bindings
from appcore.llm_use_cases import get_use_case
from appcore.llm_providers import get_adapter

log = logging.getLogger(__name__)

_PROXY_REQUIRED_PROVIDERS = {
    "anthropic",
    "gemini_aistudio",
    "gemini_vertex",
    "openai",
    "openrouter",
}


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """把 messages 里的 base64 图片内容替换为占位符，避免存储巨量数据。"""
    result = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if (isinstance(part, dict)
                        and part.get("type") == "image_url"):
                    url = (part.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"[base64-image, ~{len(url)} bytes]"},
                        })
                    else:
                        parts.append(part)
                else:
                    parts.append(part)
            result.append({**msg, "content": parts})
        elif isinstance(content, str) and "base64," in content:
            sanitized = re.sub(
                r"data:[^;]+;base64,[A-Za-z0-9+/=]+",
                lambda m: f"[base64-image, ~{len(m.group())} bytes]",
                content,
            )
            result.append({**msg, "content": sanitized})
        else:
            result.append(msg)
    return result


def _network_route_intent(provider: str) -> str:
    provider = (provider or "").strip().lower()
    if provider in _PROXY_REQUIRED_PROVIDERS:
        return "proxy_required"
    if provider.startswith("doubao"):
        return "direct_preferred"
    return "unknown"


def _search_tools_payload(provider: str, enabled: bool | None) -> list[dict] | None:
    if not enabled:
        return None
    if (provider or "").strip().lower() == "openrouter":
        return [{"type": "openrouter:web_search"}]
    return [{"google_search": {}}]


def _media_network_estimate(media_paths: list[str]) -> dict:
    items: list[dict[str, Any]] = []
    total_bytes = 0
    estimated_payload_bytes = 0
    for raw_path in media_paths:
        path = Path(raw_path)
        size = None
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        if size is not None:
            total_bytes += int(size)
            estimated_payload_bytes += ((int(size) + 2) // 3) * 4
        items.append({
            "path": str(path),
            "bytes": size,
            "estimated_base64_payload_bytes": (
                ((int(size) + 2) // 3) * 4 if size is not None else None
            ),
        })
    return {
        "media": items,
        "total_media_bytes": total_bytes,
        "estimated_base64_payload_bytes": estimated_payload_bytes,
    }


def _save_payload(log_id: int, request_data: Any, response_data: Any) -> None:
    """写入 usage_log_payloads，失败静默忽略。"""
    try:
        from appcore import usage_log
        usage_log.record_payload(log_id, request_data, response_data)
    except Exception:
        log.debug("_save_payload failed for log_id=%s", log_id, exc_info=True)


def _log_usage(*, use_case_code: str, user_id: int | None,
               project_id: str | None, provider: str, model: str,
               success: bool, usage: dict | None,
               error: Exception | None = None,
               billing_extra: dict | None = None,
               request_payload: dict | None = None,
               response_payload: dict | None = None) -> None:
    if user_id is None:
        return

    usage_data = usage or {}
    extra: dict[str, Any] = {"use_case": use_case_code}
    if billing_extra:
        extra.update(billing_extra)
    if error is not None:
        extra["error"] = str(error)[:500]

    units_type = "tokens"
    request_units = usage_data.get("request_units")
    try:
        units_type = get_use_case(use_case_code).get("units_type") or "tokens"
    except Exception:
        units_type = "tokens"
    if units_type != "tokens" and request_units is None:
        request_units = 1

    try:
        log_id = ai_billing.log_request(
            use_case_code=use_case_code,
            user_id=user_id,
            project_id=project_id,
            provider=provider,
            model=model,
            input_tokens=usage_data.get("input_tokens"),
            output_tokens=usage_data.get("output_tokens"),
            request_units=request_units,
            units_type=units_type,
            response_cost_cny=usage_data.get("cost_cny"),
            success=success,
            extra=extra,
        )
    except Exception:
        log.debug("ai_billing.log_request failed for use_case=%s",
                  use_case_code, exc_info=True)
        log_id = None

    if log_id and (request_payload is not None or response_payload is not None):
        _save_payload(log_id, request_payload, response_payload)


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
    billing_extra: dict | None = None,
) -> dict:
    binding = llm_bindings.resolve(use_case_code)
    provider = provider_override or binding["provider"]
    model = model_override or binding["model"]
    adapter = get_adapter(provider)

    req_payload: dict = {
        "type": "chat",
        "model": model,
        "messages": _sanitize_messages(messages),
        "network_route_intent": _network_route_intent(provider),
    }
    if temperature is not None:
        req_payload["temperature"] = temperature
    if max_tokens is not None:
        req_payload["max_tokens"] = max_tokens
    if response_format:
        req_payload["response_format"] = response_format

    try:
        result = adapter.chat(
            model=model, messages=messages, user_id=user_id,
            temperature=temperature, max_tokens=max_tokens,
            response_format=response_format, extra_body=extra_body,
        )
    except Exception as e:
        _log_usage(use_case_code=use_case_code, user_id=user_id,
                   project_id=project_id, provider=provider, model=model,
                   success=False, usage=None, error=e,
                   billing_extra=billing_extra,
                   request_payload=req_payload,
                   response_payload={"error": str(e)[:500]})
        raise

    resp_payload: dict = {}
    if result.get("text") is not None:
        resp_payload["text"] = result["text"]
    if result.get("json") is not None:
        resp_payload["json"] = result["json"]
    if result.get("usage"):
        resp_payload["usage"] = {
            k: str(v) for k, v in result["usage"].items()
        }

    _log_usage(use_case_code=use_case_code, user_id=user_id,
               project_id=project_id, provider=provider, model=model,
               success=True, usage=result.get("usage"),
               billing_extra=billing_extra,
               request_payload=req_payload,
               response_payload=resp_payload)
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
    google_search: bool | None = None,
    billing_extra: dict | None = None,
) -> dict:
    binding = llm_bindings.resolve(use_case_code)
    provider = provider_override or binding["provider"]
    model = model_override or binding["model"]
    adapter = get_adapter(provider)

    # 规范化 media：只存路径字符串，不传输文件内容
    media_paths: list[str] = []
    if media is not None:
        if isinstance(media, (str, Path)):
            media_paths = [str(media)]
        else:
            media_paths = [str(p) for p in media]

    req_payload: dict = {
        "type": "generate",
        "model": model,
        "prompt": prompt,
        "network_route_intent": _network_route_intent(provider),
    }
    if system:
        req_payload["system"] = system
    if media_paths:
        req_payload["media"] = media_paths
        req_payload["network_estimate"] = _media_network_estimate(media_paths)
    if temperature is not None:
        req_payload["temperature"] = temperature
    if max_output_tokens is not None:
        req_payload["max_output_tokens"] = max_output_tokens
    if response_schema:
        req_payload["response_schema"] = response_schema
    if google_search is not None:
        req_payload["google_search"] = bool(google_search)
        tools_payload = _search_tools_payload(provider, google_search)
        if tools_payload:
            req_payload["tools"] = tools_payload

    try:
        result = adapter.generate(
            model=model, prompt=prompt, user_id=user_id,
            system=system, media=media,
            response_schema=response_schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            google_search=google_search,
        )
    except Exception as e:
        _log_usage(use_case_code=use_case_code, user_id=user_id,
                   project_id=project_id, provider=provider, model=model,
                   success=False, usage=None, error=e,
                   billing_extra=billing_extra,
                   request_payload=req_payload,
                   response_payload={"error": str(e)[:500]})
        raise

    resp_payload: dict = {}
    if result.get("text") is not None:
        resp_payload["text"] = result["text"]
    if result.get("json") is not None:
        resp_payload["json"] = result["json"]
    if result.get("usage"):
        resp_payload["usage"] = {
            k: str(v) for k, v in result["usage"].items()
        }

    _log_usage(use_case_code=use_case_code, user_id=user_id,
               project_id=project_id, provider=provider, model=model,
               success=True, usage=result.get("usage"),
               billing_extra=billing_extra,
               request_payload=req_payload,
               response_payload=resp_payload)
    return result
