"""投放素材AI分析专用：google_wj 通道自适应节流 + 任务级重试 + 可观测。

与 adapter 层（gemini_vertex_adapter 的 3 次瞬时重试，退避 1s/2s）分层互补：
adapter 扛秒级抖动；本模块管「调用间主动节流」「通道级持续限流的长退避重试」「可观测」。
仅对 provider_code == "google_wj" 启用强节流；其它 provider 退化为只走调用级最小间隔。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

log = logging.getLogger(__name__)

GOOGLE_WJ_PROVIDER = "google_wj"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_RATE_LIMIT_KEYWORDS = (
    "429", "resource_exhausted", "rate limit", "rate_limit", "quota",
    "too many requests", "unavailable", "deadline", "timeout",
    "vertex gemini call failed",
)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    try:
        return float(raw) if raw.strip() else default
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        return int(raw) if raw.strip() else default
    except (TypeError, ValueError):
        return default


@dataclass
class ThrottleConfig:
    product_spacing: float = 10.0     # 产品级基线间隔（秒）
    call_spacing: float = 2.0         # 调用级最小间隔（秒）
    max_retries: int = 4              # 上层任务级重试次数（adapter 3 次之外）
    backoff_base: float = 10.0        # 任务级退避基数（秒）
    backoff_max: float = 120.0        # 退避封顶（秒）
    factor: float = 2.0               # 自适应放大/回落系数
    adaptive_max: float = 60.0        # 自适应间隔封顶（秒）
    recover_successes: int = 3        # 连续成功几次回落一档

    @classmethod
    def from_env(cls) -> "ThrottleConfig":
        return cls(
            product_spacing=_env_float("AD_MATERIAL_AI_ANALYSIS_PRODUCT_SPACING_SECONDS", 10.0),
            call_spacing=_env_float("AD_MATERIAL_AI_ANALYSIS_LLM_SPACING_SECONDS", 2.0),
            max_retries=_env_int("AD_MATERIAL_AI_ANALYSIS_LLM_MAX_RETRIES", 4),
            backoff_base=_env_float("AD_MATERIAL_AI_ANALYSIS_LLM_BACKOFF_BASE_SECONDS", 10.0),
            backoff_max=_env_float("AD_MATERIAL_AI_ANALYSIS_LLM_BACKOFF_MAX_SECONDS", 120.0),
            factor=_env_float("AD_MATERIAL_AI_ANALYSIS_THROTTLE_FACTOR", 2.0),
            adaptive_max=_env_float("AD_MATERIAL_AI_ANALYSIS_THROTTLE_MAX_SECONDS", 60.0),
            recover_successes=_env_int("AD_MATERIAL_AI_ANALYSIS_THROTTLE_RECOVER_SUCCESSES", 3),
        )


def is_rate_limit_error(exc: BaseException) -> bool:
    """遍历异常链，识别限流/可重试错误（adapter 已把原始 429 包装成 RuntimeError）。"""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        code = getattr(cur, "code", None) or getattr(cur, "status_code", None)
        if isinstance(code, int) and code in _RETRYABLE_STATUS:
            return True
        text = str(cur).lower()
        if any(kw in text for kw in _RATE_LIMIT_KEYWORDS):
            return True
        cur = cur.__cause__ or cur.__context__
    return False
