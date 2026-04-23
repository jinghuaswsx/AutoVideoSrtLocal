"""
分镜级翻译：字符数上限约束 + 超限缩写重试
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from appcore.gemini import generate as gemini_generate

TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {"translated_text": {"type": "string"}},
    "required": ["translated_text"],
}

TRANSLATE_PROMPT = (
    "你是专业的影视本土化翻译。请将下面的分镜原文翻译为 {target_language}。\n\n"
    "分镜画面描述：{description}\n"
    "分镜时长：{duration} 秒\n"
    "原文：{source_text}\n"
    "前一句译文：{prev_translation}\n"
    "后一句原文：{next_source}\n\n"
    "硬性要求：\n"
    "- 译文的字符数必须 ≤ {char_limit} 字符\n"
    "- 保留核心语义，保持与前后句的连贯\n"
    "- 本土化表达，地道自然，不要直译\n"
    "- 只输出译文，不要解释\n\n"
    "以 JSON 输出：{{\"translated_text\": \"...\"}}"
)

RETRY_PROMPT = (
    "上一版译文「{previous}」超出了 {char_limit} 字符上限，实际 {actual} 字符。"
    "请缩写为 ≤ {char_limit} 字符，保留核心含义。"
    "以 JSON 输出：{{\"translated_text\": \"...\"}}"
)


def compute_char_limit(shot_duration: float, chars_per_second: float,
                        tolerance: float = 0.9) -> int:
    """根据分镜时长、语速和容忍度计算字符数上限。"""
    limit = shot_duration * tolerance * chars_per_second
    return int(limit)


def _call_llm(prompt: str, user_id: int) -> str:
    """调用 Gemini 生成 JSON 翻译，返回 translated_text。"""
    resp = gemini_generate(
        prompt,
        user_id=user_id,
        response_schema=TRANSLATE_SCHEMA,
        service="translate_lab.shot_translate",
    )
    text = (resp or {}).get("translated_text", "") or ""
    return text.strip()


def translate_shot(
    shot: Dict[str, Any],
    *,
    target_language: str,
    char_limit: int,
    prev_translation: Optional[str],
    next_source: Optional[str],
    user_id: int,
    max_retries: int = 2,
) -> Dict[str, Any]:
    """翻译单个分镜，超字符上限时请求缩写重试。"""
    initial_prompt = TRANSLATE_PROMPT.format(
        target_language=target_language,
        description=shot.get("description", ""),
        duration=shot.get("duration", 0.0),
        source_text=shot.get("source_text", ""),
        prev_translation=prev_translation or "（无）",
        next_source=next_source or "（无）",
        char_limit=char_limit,
    )
    text = _call_llm(initial_prompt, user_id)

    retries = 0
    while len(text) > char_limit and retries < max_retries:
        retry_prompt = RETRY_PROMPT.format(
            previous=text,
            char_limit=char_limit,
            actual=len(text),
        )
        text = _call_llm(retry_prompt, user_id)
        retries += 1

    return {
        "shot_index": shot.get("index"),
        "translated_text": text,
        "char_count": len(text),
        "over_limit": len(text) > char_limit,
        "retries": retries,
    }
