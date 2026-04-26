"""ASR 路由层。

职责：
  1. 根据 source_language 选 ASR adapter（zh→豆包，其他→Scribe v2）
  2. 调 adapter 拿 utterances
  3. 跑 purify_language 清理语言污染段
  4. 返回 utterances

路由表硬编码：当前只有两个 ASR provider 且按语言分死，没有覆盖需求；如未来
再加 provider 或需要管理员级覆盖，再做 settings UI。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from appcore.asr_providers import BaseASRAdapter, build_adapter
from appcore.asr_providers.base import Utterance
from appcore.asr_purify import _normalize_lang_code, purify_language

log = logging.getLogger(__name__)


# 路由表：source_language（归一化后） → provider_code
# 未列出的语言一律走兜底（"__default__"）。
DEFAULT_ROUTE_TABLE: dict[str, str] = {
    "zh": "doubao_asr",
    "__default__": "elevenlabs_tts",
}


def resolve_adapter(source_language: str | None) -> tuple[BaseASRAdapter, str | None]:
    """解析 source_language → (adapter, force_language)。

    Returns:
        adapter: 已实例化的 BaseASRAdapter
        force_language: 传给 adapter.transcribe 的 language 参数；若 adapter
                        不支持强制语言或 source_language 为 auto/空，返回 None。
    """
    main_lang = _normalize_lang_code(source_language or "")
    provider_code = DEFAULT_ROUTE_TABLE.get(main_lang) or DEFAULT_ROUTE_TABLE["__default__"]
    adapter = build_adapter(provider_code)

    # auto / 空主语言 → 不强制
    if not main_lang or main_lang == "auto":
        force = None
    elif adapter.capabilities.supports_force_language:
        force = main_lang
    else:
        force = None

    log.info(
        "[ASR-Router] source_language=%s → provider=%s force_language=%s",
        source_language,
        provider_code,
        force,
    )
    return adapter, force


def transcribe(
    local_audio_path: Path | str,
    source_language: str | None,
) -> List[Utterance]:
    """主入口：路由 + ASR 调用 + 语言污染清理。

    Args:
        local_audio_path: 本地音频文件。
        source_language: 主语言（ISO-639-1，如 "zh"/"es"）；"auto"/None 表示
                         不强制。

    Returns:
        清理后的 utterance 列表。
    """
    adapter, force = resolve_adapter(source_language)
    utterances = adapter.transcribe(Path(local_audio_path), language=force)
    return purify_language(utterances, source_language=source_language)
