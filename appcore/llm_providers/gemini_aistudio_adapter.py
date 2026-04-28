"""Google AI Studio Gemini 适配器。凭据来自 llm_provider_configs。"""
from __future__ import annotations

from pathlib import Path

from appcore import gemini as gemini_api
from appcore.llm_providers.base import LLMAdapter
from appcore.llm_provider_configs import (
    credential_provider_for_adapter,
    require_provider_config,
)


class GeminiAIStudioAdapter(LLMAdapter):
    provider_code = "gemini_aistudio"

    def resolve_credentials(self, user_id, *, media_kind: str | None = None):
        provider_code = credential_provider_for_adapter(
            "gemini_aistudio", media_kind=media_kind,
        )
        cfg = require_provider_config(provider_code)
        api_key = cfg.require_api_key()
        return {
            "api_key": api_key,
            "base_url": None,
            "extra": cfg.extra_config or {},
            "provider_code": provider_code,
        }

    def generate(self, *, model, prompt, user_id=None, system=None,
                 media=None, response_schema=None, temperature=None,
                 max_output_tokens=None, extra_body=None,
                 enable_google_search=False):
        media_list = None
        if media:
            media_list = [media] if isinstance(media, (str, Path)) else list(media)
        result = gemini_api.generate(
            prompt, system=system, model=model, media=media_list,
            response_schema=response_schema, temperature=temperature,
            max_output_tokens=max_output_tokens, user_id=user_id,
            service="gemini", default_model=model, return_payload=True,
            enable_google_search=enable_google_search,
        )
        return result

    def chat(self, *, model, messages, user_id=None, temperature=None,
             max_tokens=None, response_format=None, extra_body=None):
        system, user_parts = None, []
        for m in messages:
            role, content = m.get("role"), m.get("content", "")
            if isinstance(content, list):
                content = "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
            if role == "system":
                system = (system + "\n\n" + content) if system else content
            else:
                user_parts.append(str(content))
        schema = None
        if response_format and response_format.get("type") == "json_schema":
            schema = (response_format.get("json_schema") or {}).get("schema")
        return self.generate(
            model=model, prompt="\n\n".join(user_parts), user_id=user_id,
            system=system, response_schema=schema,
            temperature=temperature, max_output_tokens=max_tokens,
        )
