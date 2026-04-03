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


def _resolve_provider_config(
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
    _, model = _resolve_provider_config(provider, user_id)
    return model


SYSTEM_PROMPT = """You are an expert copywriter specializing in TikTok e-commerce advertising for the US market.

Your task is to translate Chinese short video scripts into English copy that:
1. Sounds completely native, written by an American creator, not translated
2. Matches the energy and style of the original
3. Uses natural spoken American English
4. Adapts cultural references into US TikTok equivalents
5. Maintains persuasive selling power
6. Keeps the same sentence count and rhythm as the original for audio and video sync

Output only a valid JSON array.
Format: [{"index": 0, "translated": "..."}, {"index": 1, "translated": "..."}]"""


def _parse_json_content(raw: str):
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
    client, model = _resolve_provider_config(provider, user_id, api_key_override=openrouter_api_key)
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
    payload = _parse_json_content(raw_content)
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
) -> dict:
    client, model = _resolve_provider_config(provider, user_id, api_key_override=openrouter_api_key)
    extra_body: dict = {}
    if provider != "doubao":
        extra_body["response_format"] = TTS_SCRIPT_RESPONSE_FORMAT
    if provider == "openrouter":
        extra_body["plugins"] = [{"id": "response-healing"}]

    response = client.chat.completions.create(
        model=model,
        messages=build_tts_script_messages(localized_translation),
        temperature=0.2,
        max_tokens=4096,
        **( {"extra_body": extra_body} if extra_body else {}),
    )
    raw_content = response.choices[0].message.content
    log.info("tts_script raw response (provider=%s): %s", provider, raw_content[:2000])
    payload = _parse_json_content(raw_content)
    log.info("tts_script parsed payload type=%s keys=%s", type(payload).__name__, list(payload.keys()) if isinstance(payload, dict) else f"list[{len(payload)}]")
    result = validate_tts_script(payload)
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


def translate_segments(
    segments: List[Dict],
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
) -> List[Dict]:
    if not segments:
        return segments

    client, model = _resolve_provider_config(provider, user_id, api_key_override=openrouter_api_key)

    items = [{"index": i, "text": seg["text"]} for i, seg in enumerate(segments)]
    user_prompt = f"""Translate these Chinese TikTok ad script segments to native American English.
Each segment is one spoken sentence or phrase. Keep the same count and order.

Segments:
{json.dumps(items, ensure_ascii=False, indent=2)}

Remember: output only the JSON array."""

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        max_tokens=4096,
    )

    translations = _parse_json_content(response.choices[0].message.content)
    translation_map = {item["index"]: item["translated"] for item in translations}

    result = []
    for i, seg in enumerate(segments):
        seg_copy = dict(seg)
        seg_copy["translated"] = translation_map.get(i, seg["text"])
        result.append(seg_copy)

    return result
