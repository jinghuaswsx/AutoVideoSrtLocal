"""OpenRouter / 豆包 ARK 适配器（OpenAI-compatible 协议）。

凭据完全来自 llm_provider_configs（DB），不再读 .env / api_keys 兼容层。
text/image 凭据通过 media_kind 在 llm_provider_configs.credential_provider_for_adapter
处分流。
"""
from __future__ import annotations

import base64
import json
import mimetypes
from decimal import Decimal
from pathlib import Path

from openai import OpenAI

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

DEFAULT_OPENROUTER_TIMEOUT_SECONDS = 120.0
DEFAULT_OPENROUTER_MAX_RETRIES = 1


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
        resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
        usage = getattr(resp, "usage", None)
        cost_usd = getattr(usage, "cost", None) if usage else None
        cost_cny = None
        if cost_usd is not None:
            cost_cny = (
                Decimal(str(cost_usd)) * Decimal(str(USD_TO_CNY))
            ).quantize(Decimal("0.000001"))
        return {
            "text": resp.choices[0].message.content or "",
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
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        user_content = _media_parts(prompt, media_list) if media_list else prompt
        messages.append({"role": "user", "content": user_content})
        response_format = None
        if response_schema is not None:
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

    def resolve_credentials(self, user_id, *, media_kind: str | None = None):
        provider_code = credential_provider_for_adapter("doubao", media_kind=media_kind)
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
        creds = self.resolve_credentials(user_id)
        client = OpenAI(api_key=creds["api_key"], base_url=creds["base_url"])
        # 豆包不支持 response_format / OpenRouter plugins；一律忽略
        kwargs: dict = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
        usage = getattr(resp, "usage", None)
        return {
            "text": resp.choices[0].message.content or "",
            "raw": resp,
            "usage": {
                "input_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                "output_tokens": getattr(usage, "completion_tokens", None) if usage else None,
            },
        }


__all__ = ["OpenRouterAdapter", "DoubaoAdapter", "ProviderConfigError"]
