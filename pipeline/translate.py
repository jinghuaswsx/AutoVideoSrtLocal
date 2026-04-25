import json
import logging
from typing import Dict, List

from openai import OpenAI

log = logging.getLogger(__name__)

from appcore.llm_provider_configs import (
    ProviderConfigError,
    require_provider_config,
)
from config import (
    DOUBAO_LLM_BASE_URL_DEFAULT,
    OPENROUTER_BASE_URL_DEFAULT,
)

# 默认 model_id 作为 DB 行为空时的兜底
_DEFAULT_CLAUDE_MODEL = "anthropic/claude-sonnet-4-5"
_DEFAULT_DOUBAO_MODEL = "doubao-seed-2-0-pro-260215"
from pipeline.localization import (
    LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
    TTS_SCRIPT_RESPONSE_FORMAT,
    build_localized_translation_messages,
    build_tts_script_messages,
    validate_localized_translation,
    validate_tts_script,
)


# 走 OpenRouter 的 provider 值 → 具体模型 ID
_OPENROUTER_PREF_MODELS = {
    "gemini_31_flash":  "google/gemini-3.1-flash-lite-preview",
    "gemini_31_pro":    "google/gemini-3.1-pro-preview",
    "gemini_3_flash":   "google/gemini-3-flash-preview",
    "gpt_5_mini":       "openai/gpt-5-mini",
    "claude_sonnet":    "anthropic/claude-sonnet-4.6",
    "openrouter":       "anthropic/claude-sonnet-4.6",  # legacy 值回落 claude
}

# 走 Vertex AI（Google Cloud Express Mode）的 provider 值 → Gemini model ID
_VERTEX_PREF_MODELS = {
    "vertex_gemini_31_flash_lite": "gemini-3.1-flash-lite-preview",
    "vertex_gemini_3_flash":       "gemini-3-flash-preview",
    "vertex_gemini_31_pro":        "gemini-3.1-pro-preview",
}


# ---------------------------------------------------------------------------
# use_case code 前置解析（对接 appcore.llm_bindings）
# ---------------------------------------------------------------------------

def _binding_lookup_for_use_case(code: str) -> dict | None:
    """如果入参看起来像 use_case code（含 '.'），查 bindings 表；否则 None。

    返回 {provider, model, extra, source} 或 None。
    """
    if not isinstance(code, str) or "." not in code:
        return None
    try:
        from appcore import llm_bindings
        return llm_bindings.resolve(code)
    except KeyError:
        return None


def _resolve_use_case_provider(provider_arg: str) -> str:
    """入口映射：use_case code → 老式 provider 字符串（保留业务函数 vertex_* 分流不变）。

    映射规则：
      gemini_vertex + 模型命中 _VERTEX_PREF_MODELS 反向表 → 返 vertex_*
      gemini_vertex + 未命中 → 写入 _VERTEX_PREF_MODELS["vertex_custom"] 并返 "vertex_custom"
      gemini_aistudio → translate.py 无此分支；best-effort 走 OpenRouter 的 google/<model>
      openrouter / doubao → 原样返回
    """
    binding = _binding_lookup_for_use_case(provider_arg)
    if not binding:
        return provider_arg

    p = binding["provider"]
    m = binding["model"]
    if p == "gemini_vertex":
        reverse = {v: k for k, v in _VERTEX_PREF_MODELS.items()}
        if m in reverse:
            return reverse[m]
        _VERTEX_PREF_MODELS["vertex_custom"] = m
        return "vertex_custom"
    if p == "gemini_aistudio":
        # translate.py 内没有 AIStudio 分支；回退到 OpenRouter 并补 google/ 前缀
        model_id = m if m.startswith("google/") else f"google/{m}"
        _OPENROUTER_PREF_MODELS["_gemini_aistudio_fallback"] = model_id
        return "_gemini_aistudio_fallback"
    # openrouter / doubao 原样
    return p


def resolve_provider_config(
    provider: str,
    user_id: int | None = None,
    api_key_override: str | None = None,
) -> tuple[OpenAI, str]:
    """Return (client, model_id) for the given provider (OpenAI-compatible only).

    Vertex provider 不走这里——由 _call_vertex_json 单独处理。
    """
    from appcore.api_keys import resolve_extra, resolve_key

    if provider == "doubao":
        try:
            cfg = require_provider_config("doubao_llm")
            key = api_key_override or cfg.require_api_key()
            base_url = cfg.require_base_url(default=DOUBAO_LLM_BASE_URL_DEFAULT)
        except ProviderConfigError as exc:
            raise RuntimeError(str(exc)) from exc
        extra = resolve_extra(user_id, "doubao_llm") if user_id else {}
        model = extra.get("model_id") or cfg.model_id or _DEFAULT_DOUBAO_MODEL
    else:
        # 非 doubao 统一走 openrouter；根据 provider 字符串选模型
        try:
            cfg = require_provider_config("openrouter_text")
            key = api_key_override or cfg.require_api_key()
            base_url = cfg.require_base_url(default=OPENROUTER_BASE_URL_DEFAULT)
        except ProviderConfigError as exc:
            raise RuntimeError(str(exc)) from exc
        extra = resolve_extra(user_id, "openrouter") if user_id else {}
        # 优先级：用户在 OpenRouter 设置里显式 override 的 model_id > provider 映射 > legacy 默认
        user_override = (extra.get("model_id") or cfg.model_id or "").strip()
        if user_override:
            model = user_override
        else:
            model = _OPENROUTER_PREF_MODELS.get(provider, _DEFAULT_CLAUDE_MODEL)

    return OpenAI(api_key=key, base_url=base_url), model


def get_model_display_name(provider: str, user_id: int | None = None) -> str:
    """Return the model ID string for logging/display."""
    if provider.startswith("vertex_"):
        return _VERTEX_PREF_MODELS.get(provider, "gemini-3.1-flash-lite-preview")
    _, model = resolve_provider_config(provider, user_id)
    return model


def parse_json_content(raw: str):
    if raw is None:
        raise TypeError("LLM 返回内容为 None")
    content = raw.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    return json.loads(content.strip())


# ---------------------------------------------------------------------------
# Vertex AI (Google Cloud Express Mode) 分支 —— 复用图片翻译模块的授权方式
# ---------------------------------------------------------------------------

def _strip_unsupported_schema(obj):
    """Gemini response_schema 不认识 OpenAI 的 additionalProperties / strict，递归剥掉。"""
    if isinstance(obj, dict):
        return {
            k: _strip_unsupported_schema(v)
            for k, v in obj.items()
            if k not in ("additionalProperties", "strict", "$schema")
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
):
    """走 Vertex AI 返回 (parsed_payload, usage_dict, raw_text)。

    凭据从 llm_provider_configs.gemini_cloud_text 读取：api_key 或
    extra_config.project（Vertex 官方项目形式）至少一项非空即可。
    """
    from google import genai
    from google.genai import types as genai_types

    try:
        provider_cfg = require_provider_config("gemini_cloud_text")
    except ProviderConfigError as exc:
        raise RuntimeError(str(exc)) from exc

    api_key = (provider_cfg.api_key or "").strip()
    extra = provider_cfg.extra_config or {}
    project = (extra.get("project") or "").strip()
    location = (extra.get("location") or "global").strip() or "global"

    if not (api_key or project):
        raise RuntimeError(
            "缺少供应商配置 gemini_cloud_text.api_key 或 extra_config.project，"
            "请在 /settings 的「服务商接入」页填写。"
        )

    system_prompt, user_content = _split_oai_messages(messages)
    schema = _extract_gemini_schema(response_format)

    cfg_kwargs: dict = {"temperature": temperature, "max_output_tokens": max_output_tokens}
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


def _vertex_model_id(provider: str) -> str:
    return _VERTEX_PREF_MODELS.get(provider, "gemini-3.1-flash-lite-preview")


# ---------------------------------------------------------------------------
# OpenAI-兼容分支（OpenRouter / 豆包）
# ---------------------------------------------------------------------------

def _call_openai_compat(
    messages: list[dict],
    *,
    provider: str,
    user_id: int | None,
    api_key_override: str | None,
    response_format: dict | None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
):
    """走 OpenAI 兼容接口返回 (parsed_payload, usage_dict, raw_text, model_id)。"""
    client, model = resolve_provider_config(provider, user_id, api_key_override=api_key_override)
    extra_body: dict = {}
    if provider != "doubao" and response_format is not None:
        extra_body["response_format"] = response_format
    if provider == "openrouter" or provider in _OPENROUTER_PREF_MODELS:
        # 非 doubao 都走 OpenRouter，启用 response-healing 让 JSON 更稳
        if provider != "doubao":
            extra_body["plugins"] = [{"id": "response-healing"}]

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        **({"extra_body": extra_body} if extra_body else {}),
    )
    raw_content = response.choices[0].message.content
    log.info("openai-compat raw response (provider=%s, model=%s): %s",
             provider, model, (raw_content or "")[:2000])
    payload = parse_json_content(raw_content)
    usage_obj = getattr(response, "usage", None)
    usage = None
    if usage_obj is not None:
        usage = {
            "input_tokens": getattr(usage_obj, "prompt_tokens", None),
            "output_tokens": getattr(usage_obj, "completion_tokens", None),
        }
    return payload, usage, raw_content, model


# ---------------------------------------------------------------------------
# 对外业务函数
# ---------------------------------------------------------------------------

def generate_localized_translation(
    source_full_text_zh: str,
    script_segments: list[dict],
    variant: str = "normal",
    custom_system_prompt: str | None = None,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
    source_language: str = "zh",
) -> dict:
    provider = _resolve_use_case_provider(provider)
    messages = build_localized_translation_messages(
        source_full_text_zh,
        script_segments,
        variant=variant,
        custom_system_prompt=custom_system_prompt,
        source_language=source_language,
    )

    if provider.startswith("vertex_"):
        payload, usage, _ = _call_vertex_json(
            messages, _vertex_model_id(provider), LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
        )
    else:
        payload, usage, _, _ = _call_openai_compat(
            messages, provider=provider, user_id=user_id,
            api_key_override=openrouter_api_key,
            response_format=LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
        )

    log.info("localized_translation parsed payload type=%s keys=%s",
             type(payload).__name__,
             list(payload.keys()) if isinstance(payload, dict) else f"list[{len(payload)}]")
    result = validate_localized_translation(payload)
    if usage:
        result["_usage"] = usage
        log.info("localized_translation token usage: input=%s, output=%s",
                 usage["input_tokens"], usage["output_tokens"])
    # 把实际发送给 LLM 的 messages 也回传，方便调用方落盘供 UI 审计
    result["_messages"] = messages
    return result


def generate_tts_script(
    localized_translation: dict,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
    messages_builder=None,
    response_format_override=None,
    validator=None,
) -> dict:
    provider = _resolve_use_case_provider(provider)
    builder = messages_builder or build_tts_script_messages
    messages = builder(localized_translation)
    rf = response_format_override or TTS_SCRIPT_RESPONSE_FORMAT

    if provider.startswith("vertex_"):
        payload, usage, _ = _call_vertex_json(messages, _vertex_model_id(provider), rf)
    else:
        payload, usage, _, _ = _call_openai_compat(
            messages, provider=provider, user_id=user_id,
            api_key_override=openrouter_api_key,
            response_format=rf,
        )

    log.info("tts_script parsed payload type=%s keys=%s",
             type(payload).__name__,
             list(payload.keys()) if isinstance(payload, dict) else f"list[{len(payload)}]")
    validate_fn = validator or validate_tts_script
    result = validate_fn(payload)
    if usage:
        result["_usage"] = usage
        log.info("tts_script token usage: input=%s, output=%s",
                 usage["input_tokens"], usage["output_tokens"])
    result["_messages"] = messages
    return result


def generate_localized_rewrite(
    source_full_text: str,
    prev_localized_translation: dict,
    target_words: int,
    direction: str,
    source_language: str,
    messages_builder,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
) -> dict:
    """Rewrite an existing localized_translation to a target word count.

    provider 可以是 openrouter 派生值、vertex_* 或 doubao；所有路径都把实际发给
    LLM 的 messages 放在 result["_messages"] 里，供 UI/审计。
    """
    provider = _resolve_use_case_provider(provider)
    messages = messages_builder(
        source_full_text=source_full_text,
        prev_localized_translation=prev_localized_translation,
        target_words=target_words,
        direction=direction,
        source_language=source_language,
    )

    if provider.startswith("vertex_"):
        payload, usage, _ = _call_vertex_json(
            messages, _vertex_model_id(provider), LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
        )
    else:
        payload, usage, _, _ = _call_openai_compat(
            messages, provider=provider, user_id=user_id,
            api_key_override=openrouter_api_key,
            response_format=LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
        )

    log.info(
        "localized_rewrite parsed (provider=%s, direction=%s, target_words=%d)",
        provider, direction, target_words,
    )
    result = validate_localized_translation(payload)
    if usage:
        result["_usage"] = usage
    result["_messages"] = messages
    return result
