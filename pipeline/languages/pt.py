"""葡萄牙语（pt）字幕/TTS 规则。默认目标 pt-PT；pt-BR 用户可通过 prompt 覆盖。"""
from __future__ import annotations

TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "pt"

MAX_CHARS_PER_LINE = 42
MAX_CHARS_PER_SECOND = 17
MAX_LINES = 2

WEAK_STARTERS = {
    # 冠词
    "o", "a", "os", "as",
    "um", "uma", "uns", "umas",
    # 介词 / 连词
    "de", "do", "da", "dos", "das",
    "em", "no", "na", "nos", "nas",
    "por", "para", "com", "sem", "sobre",
    "e", "ou", "mas", "se", "que", "como", "porque",
    "ao", "aos", "à", "às",
    # 代词
    "eu", "tu", "ele", "ela", "nós", "vós", "eles", "elas",
    "me", "te", "se", "nos", "vos", "lhe", "lhes",
    # 物主
    "meu", "teu", "seu", "nosso", "vosso",
    "minha", "tua", "sua", "nossa", "vossa",
    # 缩合
    "d'", "n'",
}
WEAK_STARTER_PHRASES: list[str] = []


def pre_process(text: str) -> str:
    """葡语无翻译层前处理。"""
    return text


def post_process_srt(srt_content: str) -> str:
    """葡语无字幕后处理。"""
    return srt_content
