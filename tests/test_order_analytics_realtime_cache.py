from __future__ import annotations

from appcore.order_analytics import realtime_cache


def test_realtime_cache_freshness_marker_disabled_for_per_range_ttl(monkeypatch):
    # Docs-anchor: docs/superpowers/specs/2026-06-14-realtime-dashboard-load-optimization-design.md
    def fail_query(*args, **kwargs):
        raise AssertionError("per-range TTL cache should not query global freshness state")

    monkeypatch.setattr(realtime_cache, "query", fail_query)
    marker = realtime_cache.get_freshness_marker()

    assert marker == ""
