"""西班牙语（es）字幕/TTS 规则。"""
from __future__ import annotations

import re

TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "es"

MAX_CHARS_PER_LINE = 42
MAX_CHARS_PER_SECOND = 17
MAX_LINES = 2

WEAK_STARTERS = {
    # 冠词
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    # 介词 / 连词
    "de", "del", "a", "al", "en", "con", "por", "para",
    "que", "y", "o", "u", "ni", "pero", "sino", "como",
    "si", "cuando", "donde",
    # 代词
    "yo", "tú", "él", "ella", "nosotros", "vosotros", "ellos", "ellas",
    "me", "te", "se", "nos", "os", "le", "les", "lo", "los", "la", "las",
    # 物主
    "mi", "tu", "su", "nuestro", "vuestro", "sus",
}
WEAK_STARTER_PHRASES: list[str] = []


def pre_process(text: str) -> str:
    """西语无翻译层前处理——倒标点由 LLM 输出保证，兜底在 post_process_srt。"""
    return text


def post_process_srt(srt_content: str) -> str:
    """西语字幕后处理：若字幕行以 ? 或 ! 结尾但行首没有倒标点，自动补上 ¿ / ¡。

    这是兜底机制——LLM prompt 已要求输出倒标点，但偶尔会遗漏。
    只处理字幕文本行，跳过序号行和时间戳行。
    """
    lines = srt_content.split("\n")
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            out.append(line)
            continue
        # 检测行尾标点
        if stripped.endswith("?"):
            # 已有倒问号则跳过
            if not stripped.startswith("¿"):
                line = re.sub(r"^(\s*)", r"\1¿", line, count=1)
        elif stripped.endswith("!"):
            if not stripped.startswith("¡"):
                line = re.sub(r"^(\s*)", r"\1¡", line, count=1)
        out.append(line)
    return "\n".join(out)
