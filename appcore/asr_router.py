"""ASR 路由层。

职责：
  1. 根据 source_language 选 ASR adapter（当前临时全部走豆包 SeedASR）
  2. 调 adapter 拿 utterances
  3. 跑 purify_language 清理语言污染段
  4. 返回 utterances

路由表硬编码。当前临时配置：所有源语言一律走豆包 SeedASR；Scribe / 其他
adapter 代码与配置完整保留，需要时把 ``__default__`` 改为
``"elevenlabs_tts"`` 等 provider_code 即可恢复多 provider 路由。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict

from appcore.asr_providers import BaseASRAdapter, build_adapter
from appcore.asr_providers.base import Utterance
from appcore.asr_purify import _normalize_lang_code, purify_language


class TranscribeResult(TypedDict):
    utterances: list[Utterance]
    provider_code: str
    model_id: str

log = logging.getLogger(__name__)


# 路由表：source_language（归一化后） → provider_code
# 未列出的语言一律走兜底（"__default__"）。
#
# 当前临时配置：所有源语言全部走豆包 SeedASR（用户手动指定）。Scribe 等其它
# adapter 代码与凭据保留，需要切回多 provider 路由时改 ``__default__`` 即可。
DEFAULT_ROUTE_TABLE: dict[str, str] = {
    "__default__": "doubao_asr",
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
) -> TranscribeResult:
    """主入口：路由 + ASR 调用 + 语言污染清理。

    Args:
        local_audio_path: 本地音频文件。
        source_language: 主语言（ISO-639-1，如 "zh"/"es"）；"auto"/None 表示
                         不强制。

    Returns:
        ``{"utterances": [...], "provider_code": ..., "model_id": ...}``。
        ``utterances`` 为清理后的 utterance 列表；``provider_code`` /
        ``model_id`` 是实际命中的 adapter 元数据，调用方用于
        ``ai_billing.log_request``。
    """
    adapter, force = resolve_adapter(source_language)
    utterances = adapter.transcribe(Path(local_audio_path), language=force)
    purified = purify_language(utterances, source_language=source_language)
    return {
        "utterances": purified,
        "provider_code": adapter.provider_code,
        "model_id": adapter.model_id,
    }
