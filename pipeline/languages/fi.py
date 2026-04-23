"""芬兰语（fi）字幕/TTS 规则。"""
from __future__ import annotations

TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "fi"

# 芬兰语词形变化长，字幕行宽略收紧。
MAX_CHARS_PER_LINE = 38
MAX_CHARS_PER_SECOND = 15
MAX_LINES = 2

WEAK_STARTERS = {
    "ja", "tai", "mutta", "että", "kun", "jos", "kuin", "sekä", "myös",
    "se", "ne", "tämä", "tuo", "joka", "jossa", "joten", "vain", "nyt",
    "niin", "on", "ei", "voit", "me", "te", "he",
}
WEAK_STARTER_PHRASES: list[str] = []


def pre_process(text: str) -> str:
    """芬兰语无翻译层前处理。"""
    return text


def post_process_srt(srt_content: str) -> str:
    """芬兰语无字幕后处理。"""
    return srt_content
