"""多语言视频翻译默认值工具。"""
from __future__ import annotations

import importlib
import logging

log = logging.getLogger(__name__)

# 语言代码 -> pipeline localization 模块名
_LANG_MODULE_MAP: dict[str, str] = {
    "de": "pipeline.localization_de",
    "fr": "pipeline.localization_fr",
}


def resolve_default_voice(lang: str) -> str | None:
    """返回给定目标语言的默认男声 voice_id（fallback 用）。

    从对应的 pipeline.localization_<lang> 模块读取 DEFAULT_MALE_VOICE_ID。
    若模块不存在或未定义该常量，返回 None。
    """
    module_name = _LANG_MODULE_MAP.get(lang)
    if not module_name:
        log.warning("resolve_default_voice: no module mapped for lang=%r", lang)
        return None
    try:
        mod = importlib.import_module(module_name)
        return getattr(mod, "DEFAULT_MALE_VOICE_ID", None)
    except ImportError:
        log.warning("resolve_default_voice: could not import %s", module_name)
        return None
