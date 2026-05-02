"""Google Cloud Vertex AI (Express Mode) adapter.

凭据来自 llm_provider_configs:
  - text 调用走 gemini_cloud_text
  - image / 多模态调用走 gemini_cloud_image
extra_config 里可携带 project / location（Vertex 区分项目）。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from google import genai

from appcore.llm_providers.base import LLMAdapter
from appcore.llm_providers._helpers.gemini_calls import (
    GeminiError,
    _build_config,
    _extract_gemini_tokens,
    _guess_mime,
    _is_retryable,
    genai_types,
)
from appcore.llm_provider_configs import (
    ProviderConfigError,
    credential_provider_for_adapter,
    require_provider_config,
)


_clients: dict[str, genai.Client] = {}


def _normalize_media(media):
    if not media:
        return None
    if isinstance(media, (str, Path)):
        return [media]
    return list(media)


def _build_inline_contents(prompt: str, media) -> list:
    parts = []
    if media:
        for item in media:
            path = Path(item)
            if not path.is_file():
                raise GeminiError(f"文件不存在：{path}")
            mime = _guess_mime(path)
            parts.append(
                genai_types.Part.from_bytes(
                    data=path.read_bytes(),
                    mime_type=mime,
                )
            )
    parts.append(genai_types.Part.from_text(text=prompt))
    return parts


def _client_cache_key(api_key: str, project: str, location: str) -> str:
    if project:
        return f"project:{project}:{location}"
    if api_key:
        key_hash = hashlib.sha1(api_key.encode("utf-8")).hexdigest()[:16]
        return f"api_key:{key_hash}"
    raise RuntimeError(
        "Vertex AI channel is not configured: missing api_key or extra_config.project"
    )


def _get_client(api_key: str, project: str, location: str) -> genai.Client:
    cache_key = _client_cache_key(api_key, project, location)
    client = _clients.get(cache_key)
    if client is None:
        if project:
            client = genai.Client(
                vertexai=True,
                project=project,
                location=location or "global",
            )
        else:
            client = genai.Client(vertexai=True, api_key=api_key)
        _clients[cache_key] = client
    return client


class GeminiVertexAdapter(LLMAdapter):
    provider_code = "gemini_vertex"

    def resolve_credentials(self, user_id, *, media_kind: str | None = None):
        provider_code = credential_provider_for_adapter("gemini_vertex", media_kind=media_kind)
        cfg = require_provider_config(provider_code)
        extra = cfg.extra_config or {}
        project = (extra.get("project") or "").strip()
        location = (extra.get("location") or "global").strip() or "global"
        api_key = (cfg.api_key or "").strip()
        if not api_key and not project:
            raise ProviderConfigError(
                f"缺少供应商配置 {cfg.provider_code}.api_key 或 extra_config.project，"
                f"请在 /settings 的「服务商接入」页填写（{cfg.display_name}）。"
            )
        return {
            "api_key": api_key,
            "base_url": None,
            "extra": dict(extra),
            "provider_code": provider_code,
            "project": project,
            "location": location,
        }

    def _call(self, *, model, messages, response_format, temperature, max_output_tokens):
        # Keep text-only behavior aligned with the legacy translation path.
        from appcore.llm_providers._helpers.vertex_json import _call_vertex_json

        return _call_vertex_json(
            messages, model, response_format,
            temperature=temperature if temperature is not None else 0.2,
            max_output_tokens=max_output_tokens or 4096,
        )

    def chat(self, *, model, messages, user_id=None, temperature=None,
             max_tokens=None, response_format=None, extra_body=None):
        payload, usage, raw = self._call(
            model=model, messages=messages,
            response_format=response_format,
            temperature=temperature, max_output_tokens=max_tokens,
        )
        text_out = raw if isinstance(raw, str) else json.dumps(payload, ensure_ascii=False)
        return {
            "text": text_out,
            "json": payload if not isinstance(payload, str) else None,
            "raw": raw,
            "usage": usage or {"input_tokens": None, "output_tokens": None},
        }

    def generate(self, *, model, prompt, user_id=None, system=None,
                 media=None, response_schema=None, temperature=None,
                 max_output_tokens=None, google_search=None):
        media_list = _normalize_media(media)
        if media_list:
            return self._generate_with_media(
                model=model,
                prompt=prompt,
                user_id=user_id,
                system=system,
                media=media_list,
                response_schema=response_schema,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                google_search=google_search,
            )
        if google_search:
            raise NotImplementedError(
                f"{self.provider_code} text generate() does not support google_search"
            )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response_format = None
        if response_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "vx", "schema": response_schema},
            }
        return self.chat(
            model=model, messages=messages, user_id=user_id,
            temperature=temperature, max_tokens=max_output_tokens,
            response_format=response_format,
        )

    def _generate_with_media(self, *, model, prompt, user_id=None, system=None,
                             media=None, response_schema=None, temperature=None,
                             max_output_tokens=None, google_search=None):
        # 直接调 gemini_calls helper + SDK；不再 from appcore import gemini，
        # 避免业务模块对 adapter 形成反向依赖（B-1/B-2 收尾的关键约束）。
        creds = self.resolve_credentials(user_id, media_kind="image" if media else "text")
        client = _get_client(creds["api_key"], creds["project"], creds["location"])
        contents = _build_inline_contents(prompt, media)
        cfg = _build_config(
            system=system,
            temperature=temperature,
            response_schema=response_schema,
            max_output_tokens=max_output_tokens,
            google_search=google_search,
        )

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=cfg,
                )
                input_tokens, output_tokens = _extract_gemini_tokens(resp)
                usage = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }
                if response_schema is not None:
                    parsed = getattr(resp, "parsed", None)
                    if parsed is None:
                        parsed = json.loads(resp.text or "{}")
                    return {
                        "text": None,
                        "json": parsed,
                        "raw": resp,
                        "usage": usage,
                    }
                return {
                    "text": resp.text or "",
                    "json": None,
                    "raw": resp,
                    "usage": usage,
                }
            except Exception as exc:
                last_err = exc
                if attempt < 2 and _is_retryable(exc):
                    time.sleep(2 ** attempt)
                    continue
                break
        raise RuntimeError(f"Vertex Gemini call failed: {last_err}") from last_err


class GeminiVertexADCAdapter(GeminiVertexAdapter):
    provider_code = "gemini_vertex_adc"

    def resolve_credentials(self, user_id, *, media_kind: str | None = None):
        provider_code = credential_provider_for_adapter(
            "gemini_vertex_adc",
            media_kind=media_kind,
        )
        cfg = require_provider_config(provider_code)
        extra = cfg.extra_config or {}
        project = (extra.get("project") or "").strip()
        location = (extra.get("location") or "global").strip() or "global"
        if not project:
            raise ProviderConfigError(
                f"缺少供应商配置 {provider_code}.extra_config.project，"
                f"请在 /settings 的「服务商接入」页填写（{cfg.display_name}）。"
            )
        return {
            "api_key": "",
            "base_url": None,
            "extra": dict(extra),
            "provider_code": provider_code,
            "project": project,
            "location": location,
        }

    def _call(self, *, model, messages, response_format, temperature, max_output_tokens):
        return self._call_with_adc(
            model=model,
            messages=messages,
            response_format=response_format,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

    def _call_with_adc(self, *, model, messages, response_format, temperature,
                       max_output_tokens):
        from google.genai import types as genai_types
        from appcore.llm_providers._helpers.vertex_json import (
            _extract_gemini_schema,
            _split_oai_messages,
            parse_json_content,
        )

        creds = self.resolve_credentials(None, media_kind="text")
        system_prompt, user_content = _split_oai_messages(messages)
        schema = _extract_gemini_schema(response_format)

        cfg_kwargs: dict = {
            "temperature": temperature if temperature is not None else 0.2,
            "max_output_tokens": max_output_tokens or 4096,
        }
        if system_prompt:
            cfg_kwargs["system_instruction"] = system_prompt
        if schema is not None:
            cfg_kwargs["response_mime_type"] = "application/json"
            cfg_kwargs["response_schema"] = schema
        cfg = genai_types.GenerateContentConfig(**cfg_kwargs)

        client = _get_client("", creds["project"], creds["location"])
        resp = client.models.generate_content(
            model=model,
            contents=user_content,
            config=cfg,
        )
        raw = resp.text or ""
        parsed = getattr(resp, "parsed", None)
        if isinstance(parsed, (dict, list)):
            payload = parsed
        elif schema is not None:
            payload = parse_json_content(raw)
        else:
            payload = raw

        usage = None
        meta = getattr(resp, "usage_metadata", None)
        if meta is not None:
            usage = {
                "input_tokens": getattr(meta, "prompt_token_count", None),
                "output_tokens": getattr(meta, "candidates_token_count", None),
            }
        return payload, usage, raw
