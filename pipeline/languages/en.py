"""English (en-US) 字幕/TTS 规则。Prompt 见 llm_prompt_configs slot='base_*' lang='en'。"""
from __future__ import annotations

TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "en"

# 字幕 — Netflix EN 标准
MAX_CHARS_PER_LINE = 42
MAX_CHARS_PER_SECOND = 17
MAX_LINES = 2

# 弱起始词：避免字幕断在 the/a/to 这类附着前置词之前
WEAK_STARTERS = {
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "at",
    "for", "with", "from", "by", "as", "that", "this",
    "is", "are", "was", "were", "be",
    "i", "you", "we", "they", "he", "she", "it",
}
WEAK_STARTER_PHRASES: list[str] = []


def pre_process(text: str) -> str:
    """English 无需前处理。"""
    return text


def post_process_srt(srt_content: str) -> str:
    """English 无需后处理。"""
    return srt_content
