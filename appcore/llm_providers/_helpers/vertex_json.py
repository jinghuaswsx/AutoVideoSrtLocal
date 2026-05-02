"""Vertex AI 文本 JSON 调用 helper。

把 OpenAI 风格 messages 转给 Google `genai` Vertex 客户端做一次 generate_content，
返回 (parsed_payload, usage, raw_text)。

历史上这段代码住在 `pipeline/translate.py`，被两个地方使用：
1. `pipeline.translate.generate_localized_translation` 等业务函数（纯文本 chat 旧路径）。
2. `appcore/llm_providers/gemini_vertex_adapter.py` 的 `_call`。

为了让 adapter 不再反向 import `pipeline.translate`，统一搬到这里；`pipeline.translate`
内通过 re-export 保持对外签名不变。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from appcore.llm_provider_configs import (
    ProviderConfigError,
    require_provider_config,
)

log = logging.getLogger(__name__)


_GEMINI_VERTEX_UNSUPPORTED_SCHEMA_KEYS = frozenset({
    "additionalProperties",
    "additional_properties",
    "strict",
    "$schema",
})


def parse_json_content(raw: str):
    if raw is None:
        raise TypeError("LLM 返回内容为 None")
    content = raw.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    return json.loads(content.strip())


def _strip_unsupported_schema(obj):
    """Gemini response_schema 不认识部分 OpenAI JSON Schema 关键字，递归剥掉。"""
    if isinstance(obj, dict):
        return {
            k: _strip_unsupported_schema(v)
            for k, v in obj.items()
            if k not in _GEMINI_VERTEX_UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(obj, list):
        return [_strip_unsupported_schema(x) for x in obj]
    return obj


def _extract_gemini_schema(response_format: dict | None) -> dict | None:
    """把 OpenAI json_schema response_format 提取成 Gemini response_schema 需要的结构。"""
    if not response_format:
        return None
    schema = response_format.get("json_schema", {}).get("schema", response_format)
    return _strip_unsupported_schema(schema)


def _split_oai_messages(messages: list[dict]) -> tuple[str, str]:
    """拆 OpenAI 风格 [{system},{user}] 为 (system_prompt, user_content)。"""
    system_parts: list[str] = []
    user_parts: list[str] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "system":
            system_parts.append(content)
        else:
            user_parts.append(content)
    return "\n\n".join(system_parts), "\n\n".join(user_parts)


def _call_vertex_json(
    messages: list[dict],
    model_id: str,
    response_format: dict | None,
    temperature: float = 0.2,
    max_output_tokens: int = 4096,
    provider_config_code: str = "gemini_cloud_text",
):
    """走 Vertex AI 返回 (parsed_payload, usage_dict, raw_text)。

    凭据从 llm_provider_configs.gemini_cloud_text 读取：api_key 或
    extra_config.project（Vertex 官方项目形式）至少一项非空即可。
    """
    from google import genai
    from google.genai import types as genai_types

    try:
        provider_cfg = require_provider_config(provider_config_code)
    except ProviderConfigError as exc:
        raise RuntimeError(str(exc)) from exc

    api_key = (provider_cfg.api_key or "").strip()
    extra = provider_cfg.extra_config or {}
    project = (extra.get("project") or "").strip()
    location = (extra.get("location") or "global").strip() or "global"

    if provider_config_code == "gemini_vertex_adc_text":
        api_key = ""
        if not project:
            raise RuntimeError(
                "Missing provider config gemini_vertex_adc_text.extra_config.project; "
                "set it in /settings provider access."
            )

    if provider_config_code != "gemini_vertex_adc_text" and not (api_key or project):
        raise RuntimeError(
            "缺少供应商配置 gemini_cloud_text.api_key 或 extra_config.project，"
            "请在 /settings 的「服务商接入」页填写。"
        )

    system_prompt, user_content = _split_oai_messages(messages)
    schema = _extract_gemini_schema(response_format)

    cfg_kwargs: dict[str, Any] = {"temperature": temperature, "max_output_tokens": max_output_tokens}
    if system_prompt:
        cfg_kwargs["system_instruction"] = system_prompt
    if schema:
        cfg_kwargs["response_mime_type"] = "application/json"
        cfg_kwargs["response_schema"] = schema
    cfg = genai_types.GenerateContentConfig(**cfg_kwargs)

    if project:
        client = genai.Client(vertexai=True, project=project, location=location)
    else:
        client = genai.Client(vertexai=True, api_key=api_key)
    resp = client.models.generate_content(
        model=model_id,
        contents=user_content,
        config=cfg,
    )
    raw = resp.text or ""
    log.info("vertex raw response (model=%s): %s", model_id, raw[:2000])

    parsed = getattr(resp, "parsed", None)
    payload = parsed if isinstance(parsed, (dict, list)) else parse_json_content(raw)

    usage = None
    meta = getattr(resp, "usage_metadata", None)
    if meta is not None:
        usage = {
            "input_tokens": getattr(meta, "prompt_token_count", None),
            "output_tokens": getattr(meta, "candidates_token_count", None),
        }
        log.info(
            "vertex token usage (model=%s): input=%s, output=%s",
            model_id, usage["input_tokens"], usage["output_tokens"],
        )
    return payload, usage, raw


__all__ = [
    "parse_json_content",
    "_strip_unsupported_schema",
    "_extract_gemini_schema",
    "_split_oai_messages",
    "_call_vertex_json",
]
