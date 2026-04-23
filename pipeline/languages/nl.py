"""荷兰语（nl）字幕/TTS 规则。"""
from __future__ import annotations

TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "nl"

MAX_CHARS_PER_LINE = 42
MAX_CHARS_PER_SECOND = 17
MAX_LINES = 2

WEAK_STARTERS = {
    "en", "of", "maar", "dus", "want", "als", "dat", "die", "dit",
    "de", "het", "een", "voor", "met", "van", "naar", "op", "in", "aan",
    "te", "om", "er", "ook", "nog", "wel", "niet", "je", "we", "ze",
}
WEAK_STARTER_PHRASES: list[str] = []


def pre_process(text: str) -> str:
    """荷兰语无翻译层前处理。"""
    return text


def post_process_srt(srt_content: str) -> str:
    """荷兰语无字幕后处理。"""
    return srt_content
