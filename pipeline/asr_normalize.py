"""ASR 后置 en-US 标准化步骤。

接 ASR 输出的 `utterances`（带时间戳的逐句文本，可能是任意语言），先用 Gemini Flash
检测原文语言，再按路由：
- en → 跳过（直接用原 utterances）
- zh → 跳过（保留中文路径）
- es → 走西语精修 prompt 翻译为 en-US 句级 utterances_en
- pt/fr/it/ja/nl/sv/fi → 走通用兜底 prompt
- other（白名单外） → 抛 UnsupportedSourceLanguageError

句级输出 1:1 映射回原 utterances 的 start/end 时间戳；调用方将结果写到
task["utterances_en"]，下游 alignment 入口走 utterances_en or utterances fallback。
"""
from __future__ import annotations

import json
import time
from typing import Any

from appcore import llm_client
from appcore.llm_prompt_configs import resolve_prompt_config


DETECT_SUPPORTED_LANGS: tuple[str, ...] = (
    "en", "zh", "es", "pt", "fr", "it", "ja", "nl", "sv", "fi",
)

LOW_CONFIDENCE_THRESHOLD: float = 0.6

LANG_LABELS: dict[str, str] = {
    "en": "英语",
    "zh": "中文",
    "es": "西班牙语",
    "pt": "葡萄牙语",
    "fr": "法语",
    "it": "意大利语",
    "ja": "日语",
    "nl": "荷兰语",
    "sv": "瑞典语",
    "fi": "芬兰语",
}


class DetectLanguageFailedError(RuntimeError):
    """detect API 重试耗尽仍失败。"""


class UnsupportedSourceLanguageError(RuntimeError):
    """detect 出 language='other'，超出当前流水线支持范围。"""


class TranslateOutputInvalidError(RuntimeError):
    """Claude 翻译输出 schema 不合法（长度对不上 / index 缺漏 / text_en 为空）。"""


def detect_language(full_text: str, *, task_id: str, user_id: int | None) -> tuple[dict, dict]:
    """detect_language 占位 — Task 4 实现。"""
    raise NotImplementedError


def translate_to_en(
    utterances: list[dict],
    detected_language: str,
    *,
    route: str,
    task_id: str,
    user_id: int | None,
) -> tuple[list[dict], dict]:
    """translate_to_en 占位 — Task 5 实现。"""
    raise NotImplementedError


def run_asr_normalize(
    *,
    task_id: str,
    user_id: int | None,
    utterances: list[dict],
) -> dict:
    """run_asr_normalize 占位 — Task 6 实现。"""
    raise NotImplementedError
