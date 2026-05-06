"""语言规则注册中心。Batch 1 = de/fr；Batch 2 = es/it/pt；Batch 3 = ja；Batch 4 = nl/sv/fi；Batch 5 = en。

扩展新语言：加一个 pipeline/languages/<lang>.py + 在 SUPPORTED_LANGS 加一项。
"""
from __future__ import annotations

import importlib
from collections.abc import Iterable
from types import ModuleType

SUPPORTED_LANGS = ("de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en")
SOURCE_LANGS = ("zh", "en", "es", "pt", "fr", "it", "ja", "de", "nl", "sv", "fi")


def normalize_enabled_target_langs(enabled_codes: Iterable[str]) -> tuple[str, ...]:
    """Return supported target languages from an enabled-code list.

    `media_languages` can contain non-video languages. Multi/omni creation and
    list filters should only expose target languages that have rule modules, and
    English remains a forced fallback at the tail.
    """
    enabled = {str(code).strip() for code in (enabled_codes or ()) if str(code).strip()}
    filtered = [code for code in SUPPORTED_LANGS if code in enabled]
    if not filtered:
        return SUPPORTED_LANGS
    filtered = [code for code in filtered if code != "en"]
    filtered.append("en")
    return tuple(filtered)


def get_rules(lang: str) -> ModuleType:
    if lang not in SUPPORTED_LANGS:
        raise LookupError(f"unsupported language: {lang}")
    return importlib.import_module(f"pipeline.languages.{lang}")
