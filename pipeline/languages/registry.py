"""语言规则注册中心。第 1 批只含 de / fr。

扩展第 7 种语言：加一个 pipeline/languages/<lang>.py + 在 SUPPORTED_LANGS 加一行。
"""
from __future__ import annotations

import importlib
from types import ModuleType

SUPPORTED_LANGS = ("de", "fr")


def get_rules(lang: str) -> ModuleType:
    if lang not in SUPPORTED_LANGS:
        raise LookupError(f"unsupported language: {lang}")
    return importlib.import_module(f"pipeline.languages.{lang}")
