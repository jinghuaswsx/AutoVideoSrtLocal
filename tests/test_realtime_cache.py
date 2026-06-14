import time
import pytest
from appcore.order_analytics import realtime_cache


@pytest.fixture(autouse=True)
def _clean():
    realtime_cache.invalidate_all()
    yield
    realtime_cache.invalidate_all()


def test_closed_range_not_invalidated_by_global_marker():
    realtime_cache.put("k_closed", {"v": 1}, "marker_v1", is_open_day=False)
    got = realtime_cache.get("k_closed", "marker_v2_changed", is_open_day=False)
    assert got == {"v": 1}


def test_closed_range_expires_after_ttl(monkeypatch):
    base = time.time()
    monkeypatch.setattr(time, "time", lambda: base)
    realtime_cache.put("k_closed", {"v": 1}, "m", is_open_day=False)
    monkeypatch.setattr(time, "time", lambda: base + realtime_cache._CLOSED_TTL_SECONDS + 1)
    assert realtime_cache.get("k_closed", "m", is_open_day=False) is None


def test_open_range_marker_change_invalidates_after_window(monkeypatch):
    base = time.time()
    monkeypatch.setattr(time, "time", lambda: base)
    realtime_cache.put("k_open", {"v": 1}, "m1", is_open_day=True)
    monkeypatch.setattr(time, "time", lambda: base + realtime_cache._MIN_RECHECK_SECONDS + 1)
    assert realtime_cache.get("k_open", "m2", is_open_day=True) is None
    realtime_cache.put("k_open2", {"v": 2}, "m1", is_open_day=True)
    monkeypatch.setattr(time, "time", lambda: base + realtime_cache._MIN_RECHECK_SECONDS + 1)
    assert realtime_cache.get("k_open2", "m1", is_open_day=True) == {"v": 2}


def test_open_range_fast_path_within_window(monkeypatch):
    base = time.time()
    monkeypatch.setattr(time, "time", lambda: base)
    realtime_cache.put("k_open", {"v": 1}, "m1", is_open_day=True)
    monkeypatch.setattr(time, "time", lambda: base + 5)
    assert realtime_cache.get("k_open", "different_marker", is_open_day=True) == {"v": 1}
