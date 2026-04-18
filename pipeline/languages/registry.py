"""语言规则注册中心。Batch 1 = de/fr；Batch 2 = es/it/pt；Batch 3 = ja。

扩展新语言：加一个 pipeline/languages/<lang>.py + 在 SUPPORTED_LANGS 加一项。
"""
from __future__ import annotations

import importlib
from types import ModuleType

SUPPORTED_LANGS = ("de", "fr", "es", "it", "pt", "ja")


def get_rules(lang: str) -> ModuleType:
    if lang not in SUPPORTED_LANGS:
        raise LookupError(f"unsupported language: {lang}")
    return importlib.import_module(f"pipeline.languages.{lang}")
