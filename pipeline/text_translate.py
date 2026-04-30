"""纯文本翻译层。"""

from __future__ import annotations

import logging

from appcore import ai_billing, llm_bindings, llm_client
from pipeline.translate import (
    _resolve_use_case_provider,
    get_model_display_name,
    resolve_provider_config,
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
    openrouter_api_key: str | None,
) -> tuple[str, str]:
    if isinstance(provider, str) and "." in provider:
        binding = llm_bindings.resolve(provider)
        return binding["provider"], binding["model"]

    normalized = _resolve_use_case_provider(provider)
    if normalized.startswith("vertex_adc_"):
        return "gemini_vertex_adc", get_model_display_name(normalized, user_id)
    if normalized.startswith("vertex_"):
        return "gemini_vertex", get_model_display_name(normalized, user_id)

    _, model = resolve_provider_config(
        normalized,
        user_id,
        api_key_override=openrouter_api_key,
    )
    return ("doubao" if normalized == "doubao" else "openrouter"), model


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

    # 保留极少数显式覆盖 OpenRouter key 的兼容路径。
    if openrouter_api_key and provider_code == "openrouter":
        client, model = resolve_provider_config(
            "openrouter",
            user_id,
            api_key_override=openrouter_api_key,
        )
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = (response.choices[0].message.content or "").strip()
        usage = getattr(response, "usage", None)
        usage_dict = {
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0,
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0,
        }
        ai_billing.log_request(
            use_case_code="text_translate.generate",
            user_id=user_id,
            project_id=None,
            provider=provider_code,
            model=model,
            input_tokens=usage_dict["input_tokens"],
            output_tokens=usage_dict["output_tokens"],
            units_type="tokens",
            success=True,
            request_payload={
                "type": "chat",
                "use_case_code": "text_translate.generate",
                "provider": provider_code,
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            response_payload={"text": text, "usage": usage_dict},
        )
        return {"text": text, "usage": usage_dict}

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
