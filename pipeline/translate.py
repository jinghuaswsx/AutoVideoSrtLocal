import json
import logging
from typing import Dict, List

from openai import OpenAI

log = logging.getLogger(__name__)

from config import (
    CLAUDE_MODEL,
    DOUBAO_LLM_API_KEY,
    DOUBAO_LLM_BASE_URL,
    DOUBAO_LLM_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
)
from pipeline.localization import (
    LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
    TTS_SCRIPT_RESPONSE_FORMAT,
    build_localized_translation_messages,
    build_tts_script_messages,
    validate_localized_translation,
    validate_tts_script,
)


def resolve_provider_config(
    provider: str,
    user_id: int | None = None,
    api_key_override: str | None = None,
) -> tuple[OpenAI, str]:
    """Return (client, model_id) for the given provider."""
    from appcore.api_keys import resolve_extra, resolve_key

    if provider == "doubao":
        key = api_key_override or (
            resolve_key(user_id, "doubao_llm", "DOUBAO_LLM_API_KEY") if user_id else DOUBAO_LLM_API_KEY
        )
        extra = resolve_extra(user_id, "doubao_llm") if user_id else {}
        base_url = extra.get("base_url") or DOUBAO_LLM_BASE_URL
        model = extra.get("model_id") or DOUBAO_LLM_MODEL
    else:  # openrouter
        key = api_key_override or (
            resolve_key(user_id, "openrouter", "OPENROUTER_API_KEY") if user_id else OPENROUTER_API_KEY
        )
        extra = resolve_extra(user_id, "openrouter") if user_id else {}
        base_url = extra.get("base_url") or OPENROUTER_BASE_URL
        model = extra.get("model_id") or CLAUDE_MODEL

    return OpenAI(api_key=key, base_url=base_url), model


def get_model_display_name(provider: str, user_id: int | None = None) -> str:
    """Return the model ID string for logging/display."""
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


def generate_localized_translation(
    source_full_text_zh: str,
    script_segments: list[dict],
    variant: str = "normal",
    custom_system_prompt: str | None = None,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
) -> dict:
    client, model = resolve_provider_config(provider, user_id, api_key_override=openrouter_api_key)
    extra_body: dict = {}
    if provider != "doubao":
        extra_body["response_format"] = LOCALIZED_TRANSLATION_RESPONSE_FORMAT
    if provider == "openrouter":
        extra_body["plugins"] = [{"id": "response-healing"}]

    response = client.chat.completions.create(
        model=model,
        messages=build_localized_translation_messages(
            source_full_text_zh,
            script_segments,
            variant=variant,
            custom_system_prompt=custom_system_prompt,
        ),
        temperature=0.2,
        max_tokens=4096,
        **( {"extra_body": extra_body} if extra_body else {}),
    )
    raw_content = response.choices[0].message.content
    log.info("localized_translation raw response (provider=%s): %s", provider, raw_content[:2000])
    payload = parse_json_content(raw_content)
    log.info("localized_translation parsed payload type=%s keys=%s", type(payload).__name__, list(payload.keys()) if isinstance(payload, dict) else f"list[{len(payload)}]")
    result = validate_localized_translation(payload)
    # 提取 token 用量
    usage = getattr(response, "usage", None)
    if usage:
        result["_usage"] = {
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
        }
        log.info("localized_translation token usage: input=%s, output=%s",
                 result["_usage"]["input_tokens"], result["_usage"]["output_tokens"])
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
    client, model = resolve_provider_config(provider, user_id, api_key_override=openrouter_api_key)
    extra_body: dict = {}
    rf = response_format_override or TTS_SCRIPT_RESPONSE_FORMAT
    if provider != "doubao":
        extra_body["response_format"] = rf
    if provider == "openrouter":
        extra_body["plugins"] = [{"id": "response-healing"}]

    builder = messages_builder or build_tts_script_messages
    response = client.chat.completions.create(
        model=model,
        messages=builder(localized_translation),
        temperature=0.2,
        max_tokens=4096,
        **( {"extra_body": extra_body} if extra_body else {}),
    )
    raw_content = response.choices[0].message.content
    log.info("tts_script raw response (provider=%s): %s", provider, raw_content[:2000])
    payload = parse_json_content(raw_content)
    log.info("tts_script parsed payload type=%s keys=%s", type(payload).__name__, list(payload.keys()) if isinstance(payload, dict) else f"list[{len(payload)}]")
    validate_fn = validator or validate_tts_script
    result = validate_fn(payload)
    # 提取 token 用量
    usage = getattr(response, "usage", None)
    if usage:
        result["_usage"] = {
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
        }
        log.info("tts_script token usage: input=%s, output=%s",
                 result["_usage"]["input_tokens"], result["_usage"]["output_tokens"])
    return result


def generate_localized_rewrite(
    source_full_text: str,
    prev_localized_translation: dict,
    target_chars: int,
    direction: str,
    source_language: str,
    messages_builder,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
) -> dict:
    """Rewrite an existing localized_translation to a target character count.

    Args:
        source_full_text: original source text (Chinese or English).
        prev_localized_translation: previous round's translation dict
            ({full_text, sentences[...]}); supplied as reference for the LLM.
        target_chars: approximate character count target for the new full_text.
        direction: "shrink" or "expand".
        source_language: "zh" or "en" (used for lang_label in the prompt).
        messages_builder: language-specific callable, e.g.
            pipeline.localization_de.build_localized_rewrite_messages.
        provider: "openrouter" | "doubao".
        user_id: user id for key/extras resolution.
        openrouter_api_key: override api key.

    Returns:
        Same schema as generate_localized_translation:
        {"full_text": str, "sentences": [...], "_usage": {...}}
    """
    client, model = resolve_provider_config(provider, user_id, api_key_override=openrouter_api_key)
    extra_body: dict = {}
    if provider != "doubao":
        extra_body["response_format"] = LOCALIZED_TRANSLATION_RESPONSE_FORMAT
    if provider == "openrouter":
        extra_body["plugins"] = [{"id": "response-healing"}]

    messages = messages_builder(
        source_full_text=source_full_text,
        prev_localized_translation=prev_localized_translation,
        target_chars=target_chars,
        direction=direction,
        source_language=source_language,
    )

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.2,
        max_tokens=4096,
        **({"extra_body": extra_body} if extra_body else {}),
    )
    raw_content = response.choices[0].message.content
    log.info("localized_rewrite raw response (provider=%s, direction=%s, target_chars=%d): %s",
             provider, direction, target_chars, raw_content[:2000])
    payload = parse_json_content(raw_content)
    result = validate_localized_translation(payload)
    usage = getattr(response, "usage", None)
    if usage:
        result["_usage"] = {
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
        }
    return result


