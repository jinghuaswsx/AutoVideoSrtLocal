"""意大利语（it）字幕/TTS 规则。"""
from __future__ import annotations

TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "it"

MAX_CHARS_PER_LINE = 42
MAX_CHARS_PER_SECOND = 17
MAX_LINES = 2

WEAK_STARTERS = {
    # 冠词
    "il", "lo", "la", "i", "gli", "le",
    "un", "uno", "una",
    # 介词 / 连词
    "di", "a", "da", "in", "con", "su", "per", "tra", "fra",
    "e", "o", "ma", "però", "se", "che", "come",
    # 代词
    "io", "tu", "lui", "lei", "noi", "voi", "loro",
    "mi", "ti", "si", "ci", "vi", "ne",
    # 物主
    "mio", "tuo", "suo", "nostro", "vostro", "loro",
    "mia", "tua", "sua", "nostra", "vostra",
    "miei", "tuoi", "suoi", "nostri", "vostri",
    # 缩合（单独出现时也不宜做行首）
    "l'", "d'", "c'", "n'", "un'",
}
WEAK_STARTER_PHRASES: list[str] = []


def pre_process(text: str) -> str:
    """意语无翻译层前处理。"""
    return text


def post_process_srt(srt_content: str) -> str:
    """意语无字幕后处理。"""
    return srt_content
