"""OpenRouter / 豆包 ARK 适配器（OpenAI-compatible 协议）。

凭据完全来自 llm_provider_configs（DB），不再读 .env / api_keys 兼容层。
text/image 凭据通过 media_kind 在 llm_provider_configs.credential_provider_for_adapter
处分流。
"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import ssl
import time
from decimal import Decimal
from pathlib import Path

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from appcore.llm_providers.base import LLMAdapter
from appcore.llm_provider_configs import (
    ProviderConfigError,
    credential_provider_for_adapter,
    require_provider_config,
)
from config import (
    DOUBAO_LLM_BASE_URL_DEFAULT,
    OPENROUTER_BASE_URL_DEFAULT,
    USD_TO_CNY,
)

logger = logging.getLogger(__name__)

DEFAULT_OPENROUTER_TIMEOUT_SECONDS = 600.0
DEFAULT_OPENROUTER_MAX_RETRIES = 1
DEFAULT_OPENROUTER_NETWORK_RETRY_ATTEMPTS = 3
_OPENROUTER_RETRYABLE_ERROR_CODES = {429, 500, 502, 503, 504}

_NETWORK_RETRY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteError,
    ssl.SSLError,
)


def _call_with_network_retry(
    fn,
    *,
    attempts: int = DEFAULT_OPENROUTER_NETWORK_RETRY_ATTEMPTS,
    base_delay: float = 2.0,
    label: str = "openrouter",
):
    """Wrap a single OpenAI-compatible SDK call so that transient network /
    SSL / connection errors get exponential-backoff retries instead of
    exploding the entire pipeline. Only retries on _NETWORK_RETRY_EXCEPTIONS;
    business-level failures (auth, bad request) propagate immediately."""
    total = max(1, attempts)
    for attempt in range(total):
        try:
            return fn()
        except _NETWORK_RETRY_EXCEPTIONS as exc:
            if attempt >= total - 1:
                logger.exception(
                    "%s network retry exhausted (%d/%d): %s",
                    label, attempt + 1, total, exc,
                )
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning(
                "%s network error (%d/%d), retrying in %.1fs: %s",
                label, attempt + 1, total, delay, exc,
            )
            time.sleep(delay)


def _extra_float(extra: dict, key: str, default: float) -> float:
    try:
        value = extra.get(key)
        if value is None or value == "":
            return default
        parsed = float(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def _extra_int(extra: dict, key: str, default: int) -> int:
    try:
        value = extra.get(key)
        if value is None or value == "":
            return default
        parsed = int(value)
        return parsed if parsed >= 0 else default
    except (TypeError, ValueError):
        return default


def _openrouter_client(creds: dict) -> OpenAI:
    extra = creds.get("extra") or {}
    return OpenAI(
        api_key=creds["api_key"],
        base_url=creds["base_url"],
        timeout=_extra_float(extra, "timeout", DEFAULT_OPENROUTER_TIMEOUT_SECONDS),
        max_retries=_extra_int(extra, "max_retries", DEFAULT_OPENROUTER_MAX_RETRIES),
    )


def _normalize_media(media):
    if not media:
        return None
    if isinstance(media, (str, Path)):
        return [media]
    return list(media)


def _coerce_openrouter_model(model: str) -> str:
    if model and "/" not in model and model.startswith("gemini-"):
        return f"google/{model}"
    return model


def _guess_mime(path: Path) -> str:
    mt, _ = mimetypes.guess_type(str(path))
    if mt:
        return mt
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")


def _media_parts(prompt: str, media) -> list[dict]:
    parts = [{"type": "text", "text": prompt}]
    for item in media or []:
        path = Path(item)
        if not path.is_file():
            raise RuntimeError(f"media file does not exist: {path}")
        mime = _guess_mime(path)
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        data_url = f"data:{mime};base64,{data}"
        if mime.startswith("image/"):
            parts.append({
                "type": "image_url",
                "image_url": {"url": data_url},
            })
        elif mime.startswith("video/"):
            parts.append({
                "type": "video_url",
                "video_url": {"url": data_url},
            })
        else:
            raise RuntimeError(f"unsupported media mime for OpenRouter: {mime}")
    return parts


def _parse_json_content(raw: str):
    content = (raw or "").strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    return json.loads(content.strip())


def _upload_media_for_provider(path: str | Path) -> str:
    """Stage local media for provider URL-pull APIs."""
    from pipeline.storage import upload_file

    local_path = Path(path)
    if not local_path.is_file():
        raise RuntimeError(f"media file does not exist: {local_path}")
    object_key = f"llm_media/{int(time.time())}_{local_path.name}"
    return upload_file(str(local_path), object_key, expires=3600)


def _doubao_media_content_item(path: str | Path) -> dict:
    local_path = Path(path)
    mime = _guess_mime(local_path)
    public_url = _upload_media_for_provider(local_path)
    if mime.startswith("image/"):
        return {"type": "input_image", "image_url": public_url}
    if mime.startswith("video/"):
        return {"type": "input_video", "video_url": public_url}
    raise RuntimeError(f"unsupported media mime for Doubao: {mime}")


def _create_ark_client(*, api_key: str, base_url: str):
    try:
        from volcenginesdkarkruntime import Ark
    except ImportError as exc:
        raise ImportError(
            "volcenginesdkarkruntime 未安装，请运行: pip install volcenginesdkarkruntime"
        ) from exc
    return Ark(api_key=api_key, base_url=base_url)


def _extract_ark_text(response) -> str:
    output = getattr(response, "output", None)
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        for item in output:
            content = getattr(item, "content", None)
            if content is None and isinstance(item, dict):
                content = item.get("content")
            for part in content or []:
                text = getattr(part, "text", None)
                if text is None and isinstance(part, dict):
                    text = part.get("text")
                if text:
                    return str(text)
        for item in output:
            text = getattr(item, "text", None)
            if text:
                return str(text)
    if output is not None:
        content = getattr(output, "content", None)
        for part in content or []:
            text = getattr(part, "text", None)
            if text:
                return str(text)
    raise RuntimeError(f"无法从 Ark 响应中提取文本: {repr(output)[:500]}")


def _extract_ark_usage(response) -> dict:
    usage = getattr(response, "usage", None)
    if usage is None:
        meta = getattr(response, "ResponseMeta", None) or getattr(response, "response_meta", None)
        usage = getattr(meta, "Usage", None) or getattr(meta, "usage", None) if meta else None
    if usage is None:
        return {"input_tokens": None, "output_tokens": None}
    return {
        "input_tokens": (
            getattr(usage, "prompt_tokens", None)
            or getattr(usage, "PromptTokens", None)
            or getattr(usage, "input_tokens", None)
        ),
        "output_tokens": (
            getattr(usage, "completion_tokens", None)
            or getattr(usage, "CompletionTokens", None)
            or getattr(usage, "output_tokens", None)
        ),
    }


def _append_schema_instruction(prompt: str, response_schema: dict | None) -> str:
    if not response_schema:
        return prompt
    schema_text = json.dumps(response_schema, ensure_ascii=False)
    return (
        f"{prompt}\n\n"
        "Return only one valid JSON value. Do not use Markdown or explanatory text. "
        "The JSON must match this JSON Schema:\n"
        f"{schema_text}"
    )


def _has_media(messages):
    for msg in messages or []:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in {"image_url", "video_url"}:
                    return True
    return False


class OpenRouterAdapter(LLMAdapter):
    provider_code = "openrouter"

    def resolve_credentials(self, user_id, *, media_kind: str | None = None):
        provider_code = credential_provider_for_adapter("openrouter", media_kind=media_kind)
        cfg = require_provider_config(provider_code)
        api_key = cfg.require_api_key()
        base_url = cfg.require_base_url(default=OPENROUTER_BASE_URL_DEFAULT)
        return {
            "api_key": api_key,
            "base_url": base_url,
            "extra": cfg.extra_config or {},
            "provider_code": provider_code,
        }

    def chat(self, *, model, messages, user_id=None, temperature=None,
             max_tokens=None, response_format=None, extra_body=None):
        media_kind = "image" if _has_media(messages) else "text"
        creds = self.resolve_credentials(user_id, media_kind=media_kind)
        client = _openrouter_client(creds)
        body: dict = dict(extra_body or {})
        if response_format is not None:
            body["response_format"] = response_format
        usage_body = dict(body.get("usage") or {})
        usage_body["include"] = True
        body["usage"] = usage_body
        # 非显式传 plugins 时默认启用 response-healing，让 JSON 响应更稳
        if "plugins" not in body:
            body["plugins"] = [{"id": "response-healing"}]
        kwargs: dict = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if body:
            kwargs["extra_body"] = body
        attempts = _extra_int(creds.get("extra") or {}, "logical_max_retries",
                              DEFAULT_OPENROUTER_MAX_RETRIES) + 1
        resp = None
        choices = None
        retry_label = f"openrouter:{creds.get('provider_code', 'unknown')}"
        for attempt in range(max(1, attempts)):
            resp = _call_with_network_retry(
                lambda: client.chat.completions.create(model=model, messages=messages, **kwargs),
                label=retry_label,
            )
            choices = getattr(resp, "choices", None)
            if choices:
                break
            error = getattr(resp, "error", None)
            code = error.get("code") if isinstance(error, dict) else None
            if attempt < attempts - 1 and code in _OPENROUTER_RETRYABLE_ERROR_CODES:
                time.sleep(2 ** attempt)
                continue
            if isinstance(error, dict):
                message = error.get("message") or "unknown error"
                suffix = f" (code {code})" if code is not None else ""
                raise RuntimeError(f"OpenRouter response missing choices: {message}{suffix}")
            raise RuntimeError("OpenRouter response missing choices")
        usage = getattr(resp, "usage", None)
        cost_usd = getattr(usage, "cost", None) if usage else None
        cost_cny = None
        if cost_usd is not None:
            cost_cny = (
                Decimal(str(cost_usd)) * Decimal(str(USD_TO_CNY))
            ).quantize(Decimal("0.000001"))
        return {
            "text": choices[0].message.content or "",
            "raw": resp,
            "usage": {
                "input_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                "output_tokens": getattr(usage, "completion_tokens", None) if usage else None,
                "cost_cny": cost_cny,
            },
        }

    def generate(self, *, model, prompt, user_id=None, system=None,
                 media=None, response_schema=None, temperature=None,
                 max_output_tokens=None, google_search=None):
        media_list = _normalize_media(media)
        schema_via_prompt = bool(response_schema is not None and google_search)
        prompt_for_model = _append_schema_instruction(prompt, response_schema) if schema_via_prompt else prompt
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        user_content = _media_parts(prompt_for_model, media_list) if media_list else prompt_for_model
        messages.append({"role": "user", "content": user_content})
        response_format = None
        if response_schema is not None and not schema_via_prompt:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "openrouter_generate", "schema": response_schema},
            }
        extra_body = None
        if google_search:
            extra_body = {"tools": [{"type": "openrouter:web_search"}]}
        result = self.chat(
            model=_coerce_openrouter_model(model),
            messages=messages,
            user_id=user_id,
            temperature=temperature,
            max_tokens=max_output_tokens,
            response_format=response_format,
            extra_body=extra_body,
        )
        if response_schema is not None:
            result["json"] = _parse_json_content(result.get("text") or "")
            result["text"] = None
        else:
            result["json"] = None
        return result


class DoubaoAdapter(LLMAdapter):
    provider_code = "doubao"

    def resolve_credentials(self, user_id, *, media_kind: str | None = None,
                            model_id: str | None = None):
        provider_code = credential_provider_for_adapter(
            "doubao",
            media_kind=media_kind,
            model_id=model_id,
        )
        cfg = require_provider_config(provider_code)
        api_key = cfg.require_api_key()
        base_url = cfg.require_base_url(default=DOUBAO_LLM_BASE_URL_DEFAULT)
        return {
            "api_key": api_key,
            "base_url": base_url,
            "extra": cfg.extra_config or {},
            "provider_code": provider_code,
        }

    def chat(self, *, model, messages, user_id=None, temperature=None,
             max_tokens=None, response_format=None, extra_body=None):
        creds = self.resolve_credentials(user_id, model_id=model)
        client = OpenAI(api_key=creds["api_key"], base_url=creds["base_url"])
        # 豆包不支持 response_format / OpenRouter plugins；一律忽略
        kwargs: dict = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        resp = _call_with_network_retry(
            lambda: client.chat.completions.create(model=model, messages=messages, **kwargs),
            label=f"doubao:{creds.get('provider_code', 'unknown')}",
        )
        usage = getattr(resp, "usage", None)
        return {
            "text": resp.choices[0].message.content or "",
            "raw": resp,
            "usage": {
                "input_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                "output_tokens": getattr(usage, "completion_tokens", None) if usage else None,
            },
        }

    def generate(self, *, model, prompt, user_id=None, system=None,
                 media=None, response_schema=None, temperature=None,
                 max_output_tokens=None, google_search=None):
        creds = self.resolve_credentials(
            user_id,
            media_kind="video" if media else "text",
            model_id=model,
        )
        client = _create_ark_client(api_key=creds["api_key"], base_url=creds["base_url"])
        media_list = _normalize_media(media)
        prompt_for_model = _append_schema_instruction(prompt, response_schema)

        user_content: list[dict] = [{"type": "input_text", "text": prompt_for_model}]
        for item in media_list or []:
            user_content.append(_doubao_media_content_item(item))

        input_messages: list[dict] = []
        if system:
            input_messages.append({
                "role": "system",
                "content": [{"type": "input_text", "text": system}],
            })
        input_messages.append({"role": "user", "content": user_content})

        kwargs: dict = {"model": model, "input": input_messages}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_output_tokens is not None:
            kwargs["max_output_tokens"] = max_output_tokens
        response = _call_with_network_retry(
            lambda: client.responses.create(**kwargs),
            label="doubao-responses",
        )
        text = _extract_ark_text(response)
        result = {
            "text": text,
            "json": None,
            "raw": response,
            "usage": _extract_ark_usage(response),
        }
        if response_schema is not None:
            try:
                result["json"] = _parse_json_content(text)
                result["text"] = None
            except json.JSONDecodeError as exc:
                result["json_parse_error"] = str(exc)
        return result


__all__ = ["OpenRouterAdapter", "DoubaoAdapter", "ProviderConfigError"]
