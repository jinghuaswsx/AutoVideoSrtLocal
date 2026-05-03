"""纯文本翻译层。"""

from __future__ import annotations

import logging

from appcore import llm_bindings, llm_client
from appcore.llm_models import (
    LEGACY_PROVIDER_MODEL_MAP,
    legacy_provider_to_model,
    legacy_provider_to_provider_code,
)

log = logging.getLogger(__name__)

_LANG_NAME = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "ja": "Japanese",
    "pt": "Portuguese",
    "zh": "Chinese",
}


def _lang_name(code: str) -> str:
    return _LANG_NAME.get(code, code)


def _resolve_provider_and_model(
    *,
    provider: str,
    user_id: int | None,
    openrouter_api_key: str | None,  # 保留入参签名，仅向后兼容；当前实现忽略
) -> tuple[str, str]:
    """解析最终走哪个 provider+model。

    支持入参形态：
      - use_case code（含 '.'）→ 直接查 llm_bindings
      - 老 provider 字符串（vertex_* / openrouter / doubao 等）→ 用
        appcore.llm_models 的 LEGACY_PROVIDER_MODEL_MAP 拿 model_id +
        legacy_provider_to_provider_code 拿 adapter provider_code
      - 其它 → 走 text_translate.generate binding 默认 model
    """
    del user_id, openrouter_api_key  # noqa: F841 — 兼容签名
    if isinstance(provider, str) and "." in provider:
        binding = llm_bindings.resolve(provider)
        return binding["provider"], binding["model"]

    mapped_model = legacy_provider_to_model(provider)
    if mapped_model:
        return legacy_provider_to_provider_code(provider) or "openrouter", mapped_model

    binding = llm_bindings.resolve("text_translate.generate")
    if provider == "doubao":
        return "doubao", binding["model"]
    return "openrouter", binding["model"]


def _invoke_translation_chat(
    *,
    provider: str,
    user_id: int | None,
    openrouter_api_key: str | None,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
) -> dict:
    provider_code, model = _resolve_provider_and_model(
        provider=provider,
        user_id=user_id,
        openrouter_api_key=openrouter_api_key,
    )
    return llm_client.invoke_chat(
        "text_translate.generate",
        messages=messages,
        user_id=user_id,
        temperature=temperature,
        max_tokens=max_tokens,
        provider_override=provider_code,
        model_override=model,
    )


def translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
) -> dict:
    """翻译一段纯文本。"""
    if not text or not text.strip():
        return {"text": "", "input_tokens": 0, "output_tokens": 0}

    src_name = _lang_name(source_lang)
    tgt_name = _lang_name(target_lang)
    system_prompt = (
        f"You are a translation engine. Translate the user message into {tgt_name}.\n\n"
        f"Source language: {src_name}. Target language: {tgt_name}.\n\n"
        f"STRICT RULES (violation = task failure):\n"
        f"1. Output ONLY the translation. No preamble, no disclaimer, no meta commentary, no markdown code fences.\n"
        f"2. Do NOT say 'I notice', 'The text', 'Note that', 'Here is', 'Translation:', or any similar preface.\n"
        f"3. Preserve the original structure: line breaks, blank lines, labels, lists, and numbers.\n"
        f"4. Do NOT translate structural labels. Translate only the content after each label.\n"
        f"5. If the input is already in {tgt_name}, return it unchanged.\n"
        f"6. Never add explanations even if the input seems ambiguous; produce your best translation silently."
    )

    result = _invoke_translation_chat(
        provider=provider,
        user_id=user_id,
        openrouter_api_key=openrouter_api_key,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        temperature=0.0,
        max_tokens=4096,
    )

    out = (result.get("text") or "").strip()
    usage = result.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)

    log.info(
        "text_translate provider=%s src=%s tgt=%s in_tokens=%d out_tokens=%d",
        provider,
        source_lang,
        target_lang,
        input_tokens,
        output_tokens,
    )

    return {
        "text": out,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
