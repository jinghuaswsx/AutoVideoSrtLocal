"""ASR 路由配置（system_settings 持久化）。

把"哪个 ASR provider 跑哪个 stage"暴露给 ``/settings?tab=asr_routing`` UI，
避免硬编码。当前两个 stage：

  - ``asr_main``: 音频提取后的主 ASR
  - ``subtitle_asr``: TTS 合成音频后做字幕对齐用的 ASR

存储 key: ``asr_stage_routing``。值是 JSON：

    {"asr_main": "doubao_asr", "subtitle_asr": "doubao_asr"}

未配置或解析失败 → 退回 ``DEFAULT_STAGE_PROVIDERS``（全部豆包）。
新增 stage 时同步扩展 ``STAGES`` + ``STAGE_LABELS`` + ``DEFAULT_STAGE_PROVIDERS``。
"""
from __future__ import annotations

import json
import logging
from typing import Mapping

from appcore.asr_providers import REGISTRY as _ADAPTER_REGISTRY

log = logging.getLogger(__name__)

_SETTINGS_KEY = "asr_stage_routing"

# Stage 枚举：保持稳定，UI 与 runtime 都按这两个 key 取值。
STAGES: tuple[str, ...] = ("asr_main", "subtitle_asr")

STAGE_LABELS: Mapping[str, str] = {
    "asr_main": "音频提取后 ASR（识别原视频语音）",
    "subtitle_asr": "字幕生成 ASR（TTS 后再次识别给字幕对时间戳）",
}

DEFAULT_STAGE_PROVIDERS: dict[str, str] = {
    "asr_main": "doubao_asr",
    "subtitle_asr": "doubao_asr",
}


def list_available_providers() -> list[dict[str, str]]:
    """返回 [{provider_code, display_name}, ...]，按 REGISTRY 现有 adapter。

    供 settings UI 渲染 select 选项。新加 adapter 自动出现，无需改 UI 模板。
    """
    out = []
    for code, cls in _ADAPTER_REGISTRY.items():
        try:
            out.append({
                "provider_code": code,
                "display_name": getattr(cls, "display_name", "") or code,
            })
        except Exception:  # noqa: BLE001 - defensive
            out.append({"provider_code": code, "display_name": code})
    out.sort(key=lambda x: x["provider_code"])
    return out


def _load_raw() -> dict:
    try:
        from appcore.settings import get_setting
        raw = get_setting(_SETTINGS_KEY)
    except Exception:  # noqa: BLE001 - DB 不可用时退默认，不让流水线崩
        log.warning("[asr-routing] failed to read setting", exc_info=True)
        return {}
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        log.warning("[asr-routing] setting JSON malformed: %r", raw[:200])
        return {}
    return parsed if isinstance(parsed, dict) else {}


def get_stage_provider(stage: str) -> str:
    """返回该 stage 的 provider_code；未配置则用 DEFAULT_STAGE_PROVIDERS 兜底。"""
    cfg = _load_raw()
    raw = (cfg.get(stage) or "").strip()
    if raw and raw in _ADAPTER_REGISTRY:
        return raw
    return DEFAULT_STAGE_PROVIDERS.get(stage) or "doubao_asr"


def get_all_stage_providers() -> dict[str, str]:
    """返回 {stage → provider_code}，覆盖 STAGES 全部 key。"""
    cfg = _load_raw()
    out: dict[str, str] = {}
    for stage in STAGES:
        raw = (cfg.get(stage) or "").strip()
        if raw and raw in _ADAPTER_REGISTRY:
            out[stage] = raw
        else:
            out[stage] = DEFAULT_STAGE_PROVIDERS[stage]
    return out


def set_stage_providers(mapping: Mapping[str, str]) -> None:
    """覆盖式写入 stage 路由配置。

    只接受 STAGES 里的 key；未知 provider_code 被忽略；空字符串保留默认。
    """
    cleaned: dict[str, str] = {}
    for stage in STAGES:
        provider = (mapping.get(stage) or "").strip()
        if provider and provider in _ADAPTER_REGISTRY:
            cleaned[stage] = provider
    from appcore.settings import set_setting
    set_setting(_SETTINGS_KEY, json.dumps(cleaned, ensure_ascii=False))
