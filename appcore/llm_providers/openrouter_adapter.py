"""OpenRouter / 豆包 ARK 适配器（OpenAI-compatible 协议）。"""
from __future__ import annotations

from openai import OpenAI

from appcore.api_keys import resolve_extra, resolve_key
from appcore.llm_providers.base import LLMAdapter
from config import (
    DOUBAO_LLM_API_KEY, DOUBAO_LLM_BASE_URL,
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL,
)


class OpenRouterAdapter(LLMAdapter):
    provider_code = "openrouter"

    def resolve_credentials(self, user_id):
        key = (resolve_key(user_id, "openrouter", "OPENROUTER_API_KEY")
               if user_id is not None else OPENROUTER_API_KEY)
        extra = resolve_extra(user_id, "openrouter") if user_id else {}
        return {
            "api_key": key or "",
            "base_url": extra.get("base_url") or OPENROUTER_BASE_URL,
            "extra": extra,
        }

    def chat(self, *, model, messages, user_id=None, temperature=None,
             max_tokens=None, response_format=None, extra_body=None):
        creds = self.resolve_credentials(user_id)
        if not creds["api_key"]:
            raise RuntimeError("OpenRouter API Key 未配置")
        client = OpenAI(api_key=creds["api_key"], base_url=creds["base_url"])
        body: dict = dict(extra_body or {})
        if response_format is not None:
            body["response_format"] = response_format
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
        return {
            "text": resp.choices[0].message.content or "",
            "raw": resp,
            "usage": {
                "input_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                "output_tokens": getattr(usage, "completion_tokens", None) if usage else None,
            },
        }


class DoubaoAdapter(LLMAdapter):
    provider_code = "doubao"

    def resolve_credentials(self, user_id):
        key = (resolve_key(user_id, "doubao_llm", "DOUBAO_LLM_API_KEY")
               if user_id is not None else DOUBAO_LLM_API_KEY)
        extra = resolve_extra(user_id, "doubao_llm") if user_id else {}
        return {
            "api_key": key or "",
            "base_url": extra.get("base_url") or DOUBAO_LLM_BASE_URL,
            "extra": extra,
        }

    def chat(self, *, model, messages, user_id=None, temperature=None,
             max_tokens=None, response_format=None, extra_body=None):
        creds = self.resolve_credentials(user_id)
        if not creds["api_key"]:
            raise RuntimeError("豆包 LLM API Key 未配置")
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
