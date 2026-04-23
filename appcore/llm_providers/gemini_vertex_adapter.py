"""Google Cloud Vertex AI (Express Mode) adapter."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

from google import genai

from appcore.api_keys import resolve_key
from appcore.llm_providers.base import LLMAdapter
from config import (
    GEMINI_CLOUD_API_KEY,
    GEMINI_CLOUD_LOCATION,
    GEMINI_CLOUD_PROJECT,
)


_clients: dict[str, genai.Client] = {}


def _normalize_media(media):
    if not media:
        return None
    if isinstance(media, (str, Path)):
        return [media]
    return list(media)


def _get_client(api_key: str) -> genai.Client:
    if GEMINI_CLOUD_PROJECT:
        cache_key = f"project:{GEMINI_CLOUD_PROJECT}:{GEMINI_CLOUD_LOCATION}"
    elif api_key:
        key_hash = hashlib.sha1(api_key.encode("utf-8")).hexdigest()[:16]
        cache_key = f"api_key:{key_hash}"
    else:
        raise RuntimeError(
            "Vertex AI channel is not configured: missing GEMINI_CLOUD_API_KEY or GEMINI_CLOUD_PROJECT"
        )
    client = _clients.get(cache_key)
    if client is None:
        if GEMINI_CLOUD_PROJECT:
            client = genai.Client(
                vertexai=True,
                project=GEMINI_CLOUD_PROJECT,
                location=GEMINI_CLOUD_LOCATION,
            )
        else:
            client = genai.Client(vertexai=True, api_key=api_key)
        _clients[cache_key] = client
    return client


class GeminiVertexAdapter(LLMAdapter):
    provider_code = "gemini_vertex"

    def resolve_credentials(self, user_id):
        key = (
            resolve_key(user_id, "gemini_cloud", "GEMINI_CLOUD_API_KEY")
            if user_id is not None else None
        )
        key = key or GEMINI_CLOUD_API_KEY
        return {"api_key": key or "", "base_url": None, "extra": {}}

    def _call(self, *, model, messages, response_format, temperature, max_output_tokens):
        # Keep text-only behavior aligned with the legacy translation path.
        from pipeline.translate import _call_vertex_json

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
                 max_output_tokens=None):
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
                             max_output_tokens=None):
        # Reuse shared Gemini media/schema helpers, but avoid appcore.gemini.generate()
        # because llm_client owns use-case routing and billing for provider adapters.
        from appcore import gemini as gemini_api

        creds = self.resolve_credentials(user_id)
        client = _get_client(creds["api_key"])
        contents = gemini_api._build_contents(client, prompt, media)
        cfg = gemini_api._build_config(
            system=system,
            temperature=temperature,
            response_schema=response_schema,
            max_output_tokens=max_output_tokens,
        )

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=cfg,
                )
                input_tokens, output_tokens = gemini_api._extract_gemini_tokens(resp)
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
                if attempt < 2 and gemini_api._is_retryable(exc):
                    time.sleep(2 ** attempt)
                    continue
                break
        raise RuntimeError(f"Vertex Gemini call failed: {last_err}") from last_err
