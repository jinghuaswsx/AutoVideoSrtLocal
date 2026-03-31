import json
from typing import Dict, List

from openai import OpenAI

from config import CLAUDE_MODEL, OPENROUTER_API_KEY, OPENROUTER_BASE_URL
from pipeline.localization import (
    LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
    TTS_SCRIPT_RESPONSE_FORMAT,
    build_localized_translation_messages,
    build_tts_script_messages,
    validate_localized_translation,
    validate_tts_script,
)

client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url=OPENROUTER_BASE_URL,
)

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


def _model_name() -> str:
    return CLAUDE_MODEL.replace("claude-sonnet-4-5", "claude-sonnet-4.5")


def _parse_json_content(raw: str):
    content = raw.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    return json.loads(content.strip())


def generate_localized_translation(source_full_text_zh: str, script_segments: list[dict]) -> dict:
    response = client.chat.completions.create(
        model=_model_name(),
        messages=build_localized_translation_messages(source_full_text_zh, script_segments),
        temperature=0.2,
        max_tokens=4096,
        extra_body={
            "response_format": LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
            "plugins": [{"id": "response-healing"}],
        },
    )
    payload = _parse_json_content(response.choices[0].message.content)
    return validate_localized_translation(payload)


def generate_tts_script(localized_translation: dict) -> dict:
    response = client.chat.completions.create(
        model=_model_name(),
        messages=build_tts_script_messages(localized_translation),
        temperature=0.2,
        max_tokens=4096,
        extra_body={
            "response_format": TTS_SCRIPT_RESPONSE_FORMAT,
            "plugins": [{"id": "response-healing"}],
        },
    )
    payload = _parse_json_content(response.choices[0].message.content)
    return validate_tts_script(payload)


def translate_segments(segments: List[Dict]) -> List[Dict]:
    if not segments:
        return segments

    items = [{"index": i, "text": seg["text"]} for i, seg in enumerate(segments)]
    user_prompt = f"""Translate these Chinese TikTok ad script segments to native American English.
Each segment is one spoken sentence or phrase. Keep the same count and order.

Segments:
{json.dumps(items, ensure_ascii=False, indent=2)}

Remember: output only the JSON array."""

    response = client.chat.completions.create(
        model=_model_name(),
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
