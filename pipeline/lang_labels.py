"""Language code → human-readable label mapping.

Single source of truth used by translation prompts and per-language localization
modules. All multi-source-language work (Plan B "treatment") routes through
``lang_label()`` so adding a new source language only requires extending this
table — prompts and message builders stay parameterized.
"""
from __future__ import annotations


LANG_LABELS_EN: dict[str, str] = {
    "zh": "Chinese",
    "en": "English",
    "es": "Spanish",
    "de": "German",
    "fr": "French",
    "ja": "Japanese",
    "pt": "Portuguese",
    "it": "Italian",
    "nl": "Dutch",
    "sv": "Swedish",
    "fi": "Finnish",
}

LANG_LABELS_ZH: dict[str, str] = {
    "zh": "中文",
    "en": "英文",
    "es": "西班牙语",
    "de": "德语",
    "fr": "法语",
    "ja": "日语",
    "pt": "葡萄牙语",
    "it": "意大利语",
    "nl": "荷兰语",
    "sv": "瑞典语",
    "fi": "芬兰语",
}


def lang_label(code: str, *, in_chinese: bool = False) -> str:
    """Return a human-readable label for ``code``.

    Falls back to the code itself for unrecognized inputs so prompts still
    convey *something* meaningful instead of silently dropping context.
    """
    table = LANG_LABELS_ZH if in_chinese else LANG_LABELS_EN
    return table.get(code, code)
