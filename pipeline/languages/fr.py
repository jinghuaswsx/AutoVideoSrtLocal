"""法语字幕/TTS 规则。"""
from __future__ import annotations

import re

TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "fr"

MAX_CHARS_PER_LINE = 42
MAX_CHARS_PER_SECOND = 17
MAX_LINES = 2

WEAK_STARTERS = {
    "et", "ou", "de", "du", "des", "le", "la", "les", "un", "une",
    "pour", "avec", "dans", "mais", "aussi", "que", "qui", "sur",
    "par", "en", "au", "aux",
    "il", "elle", "ils", "elles", "on", "nous", "vous",
    "ne", "ni", "si", "car", "donc", "puis", "comme",
    "ce", "cette", "ces", "son", "sa", "ses",
    "mon", "ma", "mes", "ton", "ta", "tes", "leur", "leurs",
}
WEAK_STARTER_PHRASES = ["à partir de", "en train de", "afin de"]

_NBSP = "\u00A0"


def pre_process(text: str) -> str:
    """法语无需前处理——élision 由 LLM 输出；断行保护在字幕层做。"""
    return text


def post_process_srt(srt_content: str) -> str:
    """法语 SRT 后处理：? ! : ; 前加 nbsp；« » 内侧加 nbsp。只改字幕文本行。"""
    lines = srt_content.split("\n")
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            out.append(line)
            continue
        # ? ! : ; 前加不间断空格
        line = re.sub(r"\s*([?!;:])", rf"{_NBSP}\1", line)
        # guillemets 内侧加不间断空格
        line = re.sub(r"«\s*", f"«{_NBSP}", line)
        line = re.sub(r"\s*»", f"{_NBSP}»", line)
        out.append(line)
    return "\n".join(out)
