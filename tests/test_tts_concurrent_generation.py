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
