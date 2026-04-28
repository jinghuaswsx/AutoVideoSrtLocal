"""ASR 路由层。

职责：
  1. 按 stage（``asr_main`` / ``subtitle_asr``）选 adapter，配置存于
     ``system_settings.asr_stage_routing``，可在 ``/settings?tab=asr_routing`` 后台覆盖
  2. 调 adapter 拿 utterances
  3. 返回 utterances + adapter 元数据（provider_code / model_id / display_name）

**不在这一层做"删除非主语言段"的清理**。早期版本会跑 ``asr_purify.purify_language``
按 fast-langdetect 删段，但当 ``source_language`` 与实际音频不一致时（用户填错 /
豆包对非中-英文音频硬识别成乱码）会把整段 ASR 都删光，下游报"未检测到语音"。
同语言规整、错字修正、错语言段恢复一律下沉到 LLM 步骤：

  - omni 流水线：``runtime_omni._step_asr_clean`` → ``pipeline.asr_clean``
  - multi 流水线：``runtime_multi._step_asr_normalize`` → ``pipeline.asr_normalize``

LLM 步骤只改文本不删段，时间戳一一对应保留，不会丢内容。

新增 stage 时同步扩展 :mod:`appcore.asr_routing_config` 的 ``STAGES``。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict

from appcore.asr_providers import REGISTRY as _ADAPTER_REGISTRY, BaseASRAdapter, build_adapter
from appcore.asr_providers.base import Utterance
from appcore.asr_purify import _normalize_lang_code
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

    自动 fallback：``/settings?tab=asr_routing`` 选的 provider 如果不支持当前
    ``source_language``（比如选了豆包但音频是西语），会自动切到 REGISTRY 里
    第一个支持的 adapter。豆包只支持 zh/en，非中-英文源语言会自动落到 Scribe。

    Returns:
        adapter: 已实例化的 BaseASRAdapter
        force_language: 传给 ``adapter.transcribe(language=...)``；adapter 不支持
                        force language 或 source_language 为 auto/空时为 None。
    """
    main_lang = _normalize_lang_code(source_language or "")
    if stage == "asr_main" and main_lang == "en":
        requested = "elevenlabs_tts"
    else:
        requested = get_stage_provider(stage)

    provider_code = requested
    adapter = build_adapter(provider_code)

    # 用户选的 adapter 不支持这个 source_language → 找 REGISTRY 里支持的兜底。
    # auto / 空主语言不触发 fallback（adapter 自识别）。
    if main_lang and main_lang != "auto" and not adapter.capabilities.supports_language(main_lang):
        for code in _ADAPTER_REGISTRY:
            if code == provider_code:
                continue
            cand = build_adapter(code)
            if cand.capabilities.supports_language(main_lang):
                log.info(
                    "[ASR-Router] auto-fallback stage=%s source_language=%s: %s 不支持 → 切到 %s",
                    stage, main_lang, provider_code, code,
                )
                provider_code = code
                adapter = cand
                break
        else:
            log.warning(
                "[ASR-Router] no adapter in REGISTRY supports source_language=%s; "
                "keeping user-selected %s and hoping for the best",
                main_lang, provider_code,
            )

    if not main_lang or main_lang == "auto":
        force = None
    elif adapter.capabilities.supports_force_language:
        force = main_lang
    else:
        force = None

    log.info(
        "[ASR-Router] stage=%s source_language=%s requested=%s → provider=%s force_language=%s",
        stage, source_language, requested, provider_code, force,
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
    # 不在这一层删段：交给下游 _step_asr_clean / _step_asr_normalize 用 LLM 规整。
    return {
        "utterances": utterances,
        "provider_code": adapter.provider_code,
        "model_id": adapter.model_id,
        "display_name": adapter.display_name,
        "stage": stage,
    }
