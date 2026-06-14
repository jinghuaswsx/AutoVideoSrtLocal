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


class GoogleWjThrottle:
    def __init__(
        self,
        *,
        provider_code: str,
        on_event: Callable[[dict], None] | None = None,
        config: ThrottleConfig | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = (provider_code or "").strip().lower() == GOOGLE_WJ_PROVIDER
        self.config = config or ThrottleConfig.from_env()
        self._on_event = on_event
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_call_at: float | None = None
        self._current_interval = self.config.product_spacing if self.enabled else self.config.call_spacing
        self._pending_min_interval = 0.0
        self._consecutive_success = 0
        self.rate_limit_hits = 0
        self.degraded = 0
        self.degraded_events: list[dict] = []
        self._retrying = False
        self._current_retry = 0
        self._last_event = ""

    def _emit(self, kind: str, message: str, *, level: str = "info") -> None:
        self._last_event = message
        if self._on_event is None:
            return
        try:
            self._on_event({"kind": kind, "message": message, "level": level, "throttle": self.snapshot()})
        except Exception:
            log.debug("throttle on_event callback failed", exc_info=True)

    def _wait_before_call(self) -> None:
        if self._last_call_at is None:
            wait = self._pending_min_interval
        else:
            elapsed = self._monotonic() - self._last_call_at
            base = self._current_interval if self.enabled else self.config.call_spacing
            wait = max(base, self._pending_min_interval) - elapsed
        if wait and wait > 0:
            self._sleep(wait)
        self._pending_min_interval = 0.0

    def _on_success(self) -> None:
        if not self.enabled:
            return
        self._consecutive_success += 1
        if (self._consecutive_success >= self.config.recover_successes
                and self._current_interval > self.config.product_spacing):
            self._current_interval = max(self._current_interval / self.config.factor, self.config.product_spacing)
            self._consecutive_success = 0
            self._emit("recover", f"通道平稳，节流间隔回落到 {self._current_interval:.0f}s")

    def _on_rate_limit(self) -> None:
        self.rate_limit_hits += 1
        self._consecutive_success = 0
        if self.enabled:
            self._current_interval = min(self._current_interval * self.config.factor, self.config.adaptive_max)

    def mark_product_boundary(self) -> None:
        """进入新产品前调用：下一次调用前至少等待一个产品级基线间隔。"""
        if self.enabled:
            self._pending_min_interval = max(self._pending_min_interval, self.config.product_spacing)

    def guarded_invoke(self, fn: Callable[[], dict], *, stage: str, product_id: Any = None) -> dict:
        attempt = 0
        while True:
            self._wait_before_call()
            try:
                result = fn()
            except Exception as exc:
                self._last_call_at = self._monotonic()
                if is_rate_limit_error(exc) and attempt < self.config.max_retries:
                    self._on_rate_limit()
                    backoff = min(self.config.backoff_base * (2 ** attempt), self.config.backoff_max)
                    attempt += 1
                    self._retrying = True
                    self._current_retry = attempt
                    self._emit("retry",
                               f"{stage} 命中限流，{backoff:.0f}s 后第 {attempt}/{self.config.max_retries} 次重试",
                               level="warning")
                    self._sleep(backoff)
                    continue
                self._retrying = False
                if is_rate_limit_error(exc):
                    self.degraded += 1
                    self.degraded_events.append({
                        "stage": stage,
                        "product_id": product_id,
                        "at": datetime.now().isoformat(sep=" ", timespec="seconds"),
                    })
                    self._emit("degraded",
                               f"{stage} 限流重试耗尽（{self.config.max_retries} 次），该环节降级兜底",
                               level="warning")
                raise
            else:
                self._last_call_at = self._monotonic()
                self._retrying = False
                self._current_retry = 0
                self._on_success()
                return result

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "base_interval": self.config.product_spacing,
            "current_interval": round(self._current_interval, 2),
            "retrying": self._retrying,
            "current_retry": self._current_retry,
            "max_retries": self.config.max_retries,
            "rate_limit_hits": self.rate_limit_hits,
            "degraded": self.degraded,
            "last_event": self._last_event,
            "updated_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        }
