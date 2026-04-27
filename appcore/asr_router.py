"""ASR 路由层。

职责：
  1. 按 stage（``asr_main`` / ``subtitle_asr``）选 adapter，配置存于
     ``system_settings.asr_stage_routing``，可在 ``/settings?tab=asr_routing`` 后台覆盖
  2. 调 adapter 拿 utterances
  3. 跑 purify_language 清理语言污染段
  4. 返回 utterances + adapter 元数据（provider_code / model_id / display_name）

新增 stage 时同步扩展 :mod:`appcore.asr_routing_config` 的 ``STAGES``。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict

from appcore.asr_providers import BaseASRAdapter, build_adapter
from appcore.asr_providers.base import Utterance
from appcore.asr_purify import _normalize_lang_code, purify_language
from appcore.asr_routing_config import get_stage_provider


class TranscribeResult(TypedDict):
    utterances: list[Utterance]
    provider_code: str
    model_id: str
    display_name: str
    stage: str


log = logging.getLogger(__name__)


def resolve_adapter(stage: str, source_language: str | None) -> tuple[BaseASRAdapter, str | None]:
    """按 stage 选 adapter，再按 source_language 决定要不要 force_language。

    Returns:
        adapter: 已实例化的 BaseASRAdapter
        force_language: 传给 ``adapter.transcribe(language=...)``；adapter 不支持
                        force language 或 source_language 为 auto/空时为 None。
    """
    provider_code = get_stage_provider(stage)
    adapter = build_adapter(provider_code)

    main_lang = _normalize_lang_code(source_language or "")
    if not main_lang or main_lang == "auto":
        force = None
    elif adapter.capabilities.supports_force_language:
        force = main_lang
    else:
        force = None

    log.info(
        "[ASR-Router] stage=%s source_language=%s → provider=%s force_language=%s",
        stage, source_language, provider_code, force,
    )
    return adapter, force


def transcribe(
    local_audio_path: Path | str,
    source_language: str | None,
    *,
    stage: str = "asr_main",
) -> TranscribeResult:
    """主入口：路由 + ASR 调用 + 语言污染清理。

    Args:
        local_audio_path: 本地音频文件。
        source_language: 主语言（ISO-639-1，如 ``"zh"``/``"es"``）；``"auto"`` /
                         None = 不强制。
        stage: ASR 阶段，决定 provider 路由：
            - ``"asr_main"`` — 音频提取后的主 ASR（默认）
            - ``"subtitle_asr"`` — TTS 合成后做字幕对齐用的 ASR

    Returns:
        :class:`TranscribeResult` dict：``utterances``（清理后段落）+
        ``provider_code`` + ``model_id`` + ``display_name`` + ``stage``。
        ``display_name`` 给前端 step 卡片做 model_tag 展示，
        ``provider_code`` / ``model_id`` 给 ``ai_billing.log_request`` 用。
    """
    adapter, force = resolve_adapter(stage, source_language)
    utterances = adapter.transcribe(Path(local_audio_path), language=force)
    purified = purify_language(utterances, source_language=source_language)
    return {
        "utterances": purified,
        "provider_code": adapter.provider_code,
        "model_id": adapter.model_id,
        "display_name": adapter.display_name,
        "stage": stage,
    }
