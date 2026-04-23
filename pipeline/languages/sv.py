"""瑞典语（sv）字幕/TTS 规则。"""
from __future__ import annotations

TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "sv"

MAX_CHARS_PER_LINE = 42
MAX_CHARS_PER_SECOND = 17
MAX_LINES = 2

WEAK_STARTERS = {
    "och", "eller", "men", "så", "att", "om", "som", "för", "av", "på",
    "i", "till", "med", "från", "en", "ett", "den", "det", "de", "du",
    "vi", "ni", "man", "inte", "bara", "också",
}
WEAK_STARTER_PHRASES: list[str] = []


def pre_process(text: str) -> str:
    """瑞典语无翻译层前处理。"""
    return text


def post_process_srt(srt_content: str) -> str:
    """瑞典语无字幕后处理。"""
    return srt_content
