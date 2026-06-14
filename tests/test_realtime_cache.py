import time
import pytest
from appcore.order_analytics import realtime_cache


@pytest.fixture(autouse=True)
def _clean():
    realtime_cache.invalidate_all()
    yield
    realtime_cache.invalidate_all()


def test_put_get_within_ttl(monkeypatch):
    base = time.time()
    monkeypatch.setattr(time, "time", lambda: base)
    realtime_cache.put("k", {"v": 1}, ttl_seconds=60)
    monkeypatch.setattr(time, "time", lambda: base + 30)
    assert realtime_cache.get("k") == {"v": 1}


def test_expires_after_ttl(monkeypatch):
    base = time.time()
    monkeypatch.setattr(time, "time", lambda: base)
    realtime_cache.put("k", {"v": 1}, ttl_seconds=60)
    monkeypatch.setattr(time, "time", lambda: base + 61)
    assert realtime_cache.get("k") is None


def test_per_entry_ttl_independent(monkeypatch):
    """每个条目带自己的 TTL，互不影响。"""
    base = time.time()
    monkeypatch.setattr(time, "time", lambda: base)
    realtime_cache.put("short", {"v": 1}, ttl_seconds=60)
    realtime_cache.put("long", {"v": 2}, ttl_seconds=660)
    monkeypatch.setattr(time, "time", lambda: base + 120)
    assert realtime_cache.get("short") is None
    assert realtime_cache.get("long") == {"v": 2}


def test_closed_long_ttl_not_affected_by_global_data(monkeypatch):
    """纯 TTL 模型：历史区间长 TTL 内稳定命中，不被任何全局数据变化误伤。"""
    base = time.time()
    monkeypatch.setattr(time, "time", lambda: base)
    realtime_cache.put("hist", {"v": 1}, ttl_seconds=realtime_cache.TTL_CLOSED)
    monkeypatch.setattr(time, "time", lambda: base + 1700)
    assert realtime_cache.get("hist") == {"v": 1}


def test_ttl_constants():
    assert realtime_cache.TTL_SINGLE_DAY_OPEN == 60
    assert realtime_cache.TTL_MULTI_DAY_OPEN == 660
    assert realtime_cache.TTL_CLOSED == 1800


def test_miss_returns_none():
    assert realtime_cache.get("nonexistent") is None
