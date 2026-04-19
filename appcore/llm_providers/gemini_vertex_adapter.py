"""Google Cloud Vertex AI（Express Mode）适配器。

本期复用 pipeline.translate._call_vertex_json 的底层实现以保证行为一致——
将来可把该函数抽到本模块，本期避免动主线代码以减风险。
"""
from __future__ import annotations

from pathlib import Path

from appcore.api_keys import resolve_key
from appcore.llm_providers.base import LLMAdapter
from config import GEMINI_CLOUD_API_KEY


class GeminiVertexAdapter(LLMAdapter):
    provider_code = "gemini_vertex"

    def resolve_credentials(self, user_id):
        key = (resolve_key(user_id, "gemini_cloud", "GEMINI_CLOUD_API_KEY")
               if user_id is not None else GEMINI_CLOUD_API_KEY)
        return {"api_key": key or "", "base_url": None, "extra": {}}

    def _call(self, *, model, messages, response_format, temperature, max_output_tokens):
        # 函数内 import 避免模块级循环引用（pipeline.translate 之后可能 import 本 adapter）
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
        import json as _json
        text_out = raw if isinstance(raw, str) else _json.dumps(payload, ensure_ascii=False)
        return {
            "text": text_out,
            "json": payload if not isinstance(payload, str) else None,
            "raw": raw,
            "usage": usage or {"input_tokens": None, "output_tokens": None},
        }

    def generate(self, *, model, prompt, user_id=None, system=None,
                 media=None, response_schema=None, temperature=None,
                 max_output_tokens=None):
        if media:
            raise NotImplementedError(
                "GeminiVertexAdapter 不支持多模态 media；"
                "请改用 gemini_aistudio（Files API）或 appcore.gemini_image。"
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
