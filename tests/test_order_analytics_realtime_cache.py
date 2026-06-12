from __future__ import annotations

from appcore import order_analytics as oa
from appcore.order_analytics import realtime_cache


def test_realtime_cache_freshness_marker_includes_profit_and_product_cost_state(monkeypatch):
    queries: list[str] = []

    def fake_query(sql, args=()):
        queries.append(sql)
        if "FROM roi_realtime_daily_snapshots" in sql:
            return [{"max_id": 11, "max_snap": "2026-06-12 20:20:00"}]
        if "FROM dianxiaomi_order_lines" in sql:
            return [{"max_id": 22}]
        if "FROM order_profit_lines" in sql:
            return [{"max_id": 33, "max_updated": "2026-06-12 20:22:00"}]
        if "FROM media_products" in sql:
            return [{"max_updated": "2026-06-12 20:23:00"}]
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(realtime_cache, "_freshness_cache", (0.0, ""))

    marker = realtime_cache.get_freshness_marker()

    assert any("FROM order_profit_lines" in sql for sql in queries)
    assert any("FROM media_products" in sql for sql in queries)
    assert "p:33:2026-06-12 20:22:00" in marker
    assert "m:2026-06-12 20:23:00" in marker
