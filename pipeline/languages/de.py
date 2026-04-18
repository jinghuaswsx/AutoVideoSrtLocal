"""德语字幕/TTS 规则。Prompt 见 llm_prompt_configs.slot='base_*' lang='de'。"""
from __future__ import annotations

TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "de"

# 字幕 — 德语词长，每行短一点
MAX_CHARS_PER_LINE = 38
MAX_CHARS_PER_SECOND = 17          # Netflix 规范
MAX_LINES = 2

WEAK_STARTERS = {
    "und", "oder", "der", "die", "das", "ein", "eine", "einem", "einen", "einer",
    "für", "mit", "von", "zu", "zum", "zur", "aber", "auch", "wenn", "dass",
    "den", "dem", "des", "auf", "aus", "bei", "bis", "nach", "über", "unter",
}
WEAK_STARTER_PHRASES: list[str] = []


def pre_process(text: str) -> str:
    """德语无需前处理。"""
    return text


def post_process_srt(srt_content: str) -> str:
    """德语无需后处理。"""
    return srt_content
