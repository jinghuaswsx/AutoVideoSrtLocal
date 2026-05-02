"""Google AI Studio Gemini 适配器。凭据来自 llm_provider_configs。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from google import genai

from appcore.llm_providers.base import LLMAdapter
from appcore.llm_providers._helpers.gemini_calls import (
    GeminiError,
    _build_config,
    _build_contents,
    _extract_gemini_tokens,
    _is_retryable,
)
from appcore.llm_provider_configs import (
    credential_provider_for_adapter,
    require_provider_config,
)


_clients: dict[str, genai.Client] = {}


def _get_client(api_key: str) -> genai.Client:
    client = _clients.get(api_key)
    if client is None:
        client = genai.Client(api_key=api_key)
        _clients[api_key] = client
    return client


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
                 max_output_tokens=None, google_search=None):
        media_list = None
        if media:
            media_list = [media] if isinstance(media, (str, Path)) else list(media)
        creds = self.resolve_credentials(
            user_id, media_kind="image" if media_list else "text",
        )
        client = _get_client(creds["api_key"])
        contents = _build_contents(client, prompt, media_list)
        cfg = _build_config(
            system=system,
            temperature=temperature,
            response_schema=response_schema,
            max_output_tokens=max_output_tokens,
            google_search=bool(google_search),
        )

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model=model, contents=contents, config=cfg,
                )
                input_tokens, output_tokens = _extract_gemini_tokens(resp)
                usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
                if response_schema is not None:
                    parsed = getattr(resp, "parsed", None)
                    if parsed is None:
                        parsed = json.loads(resp.text or "{}")
                    return {"text": None, "json": parsed, "raw": resp, "usage": usage}
                return {"text": resp.text or "", "json": None, "raw": resp, "usage": usage}
            except Exception as exc:
                last_err = exc
                if attempt < 2 and _is_retryable(exc):
                    time.sleep(2 ** attempt)
                    continue
                break
        raise GeminiError(f"Gemini 调用失败：{last_err}") from last_err

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
