"""纯文本翻译层。

用于 copywriting_translate 子任务:把 media_copywritings.lang='en' 的文案
翻译到目标语言。不做 JSON 分段/rewrite 等视频字幕专用处理——那些在
pipeline.translate.generate_localized_translation / generate_localized_rewrite 里。

设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md 第 1.2 节
"""
from __future__ import annotations

import logging

from pipeline.translate import resolve_provider_config

log = logging.getLogger(__name__)

# 语言代码 → LLM prompt 里的英文全称。未知 code 原样透传。
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


def translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
) -> dict:
    """翻译一段纯文本。

    返回: {"text": 译文, "input_tokens": int, "output_tokens": int}
    空输入直接返回空结果,不调 LLM。
    """
    if not text or not text.strip():
        return {"text": "", "input_tokens": 0, "output_tokens": 0}

    client, model = resolve_provider_config(
        provider, user_id, api_key_override=openrouter_api_key,
    )

    src_name = _lang_name(source_lang)
    tgt_name = _lang_name(target_lang)
    system_prompt = (
        f"You are a translation engine. Translate the user message into "
        f"{tgt_name}.\n\n"
        f"STRICT RULES (violation = task failure):\n"
        f"1. Output ONLY the translation. No preamble, no disclaimer, "
        f"   no meta commentary, no markdown code fences.\n"
        f"2. Do NOT say 'I notice', 'The text', 'Note that', 'Here is', "
        f"   'Translation:', or any similar preface. Just emit the "
        f"   translated text directly.\n"
        f"3. Preserve the original structure: line breaks, blank lines, "
        f"   labels (e.g. 'Title:', '标题:', 'Body:'), lists, numbers.\n"
        f"4. If a label is in a language other than {src_name} "
        f"   (e.g. Chinese labels inside {src_name} marketing copy), "
        f"   translate the label AND its content into {tgt_name}.\n"
        f"5. If the input is already in {tgt_name}, return it unchanged.\n"
        f"6. Never add explanations even if the input seems ambiguous — "
        f"   produce your best translation silently."
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        temperature=0.0,
        max_tokens=4096,
    )

    out = (response.choices[0].message.content or "").strip()
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0

    log.info(
        "text_translate provider=%s src=%s tgt=%s in_tokens=%d out_tokens=%d",
        provider, source_lang, target_lang, input_tokens, output_tokens,
    )

    return {
        "text": out,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
