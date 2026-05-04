"""TTS 并发生成相关单测：线程池单例、配置解析、429 退避、并发提交、回调状态机。"""
from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from pipeline import tts


def test_resolve_tts_max_concurrency_default(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: None)
    assert tts._resolve_tts_max_concurrency() == 12


def test_resolve_tts_max_concurrency_user_override(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "8")
    assert tts._resolve_tts_max_concurrency() == 8


def test_resolve_tts_max_concurrency_clamps_above_hard_cap(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "100")
    assert tts._resolve_tts_max_concurrency() == 15  # ElevenLabs Business 套餐硬上限


def test_resolve_tts_max_concurrency_clamps_below_one(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "0")
    assert tts._resolve_tts_max_concurrency() == 1


def test_resolve_tts_max_concurrency_invalid_string(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "not-a-number")
    assert tts._resolve_tts_max_concurrency() == 12  # 兜底默认


def test_get_tts_pool_is_singleton(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: None)
    # 重置模块级单例
    tts._TTS_POOL = None
    pool1 = tts._get_tts_pool()
    pool2 = tts._get_tts_pool()
    assert pool1 is pool2
    assert pool1._max_workers == 12  # 默认值


def test_get_tts_pool_uses_resolved_max_workers(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "5")
    tts._TTS_POOL = None
    pool = tts._get_tts_pool()
    assert pool._max_workers == 5


# ===== Task 2: 429 / concurrent_limit_exceeded throttle retry =====


class _Fake429Error(Exception):
    """模拟 ElevenLabs SDK 抛出的 429 异常对象。"""
    def __init__(self, body: str = "concurrent_limit_exceeded"):
        super().__init__(body)
        self.status_code = 429
        self.body = body


class _Fake500Error(Exception):
    def __init__(self):
        super().__init__("server error")
        self.status_code = 500


def test_is_concurrent_limit_429_status_code():
    assert tts._is_concurrent_limit_429(_Fake429Error("concurrent_limit_exceeded")) is True
    assert tts._is_concurrent_limit_429(_Fake429Error("rate_limit_exceeded")) is True
    assert tts._is_concurrent_limit_429(_Fake500Error()) is False
    assert tts._is_concurrent_limit_429(ValueError("nope")) is False


def test_call_with_throttle_retry_succeeds_eventually(monkeypatch):
    """前两次 429，第三次成功。"""
    sleeps: list[float] = []
    monkeypatch.setattr(tts.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Fake429Error()
        return "ok"

    assert tts._call_with_throttle_retry(flaky) == "ok"
    assert calls["n"] == 3
    assert sleeps == [0.5, 1.0]  # 头两次失败的退避


def test_call_with_throttle_retry_exhausts(monkeypatch):
    """全部尝试都 429 → 最终抛 429 异常。"""
    monkeypatch.setattr(tts.time, "sleep", lambda s: None)

    def always_429():
        raise _Fake429Error()

    with pytest.raises(_Fake429Error):
        tts._call_with_throttle_retry(always_429)


def test_call_with_throttle_retry_passes_non_429_through(monkeypatch):
    """非 429 错误立即透传，不重试。"""
    sleeps: list[float] = []
    monkeypatch.setattr(tts.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}
    def boom():
        calls["n"] += 1
        raise _Fake500Error()

    with pytest.raises(_Fake500Error):
        tts._call_with_throttle_retry(boom)
    assert calls["n"] == 1
    assert sleeps == []
