import json
from appcore.order_analytics import realtime_cache


def test_put_writes_replace_with_ttl(monkeypatch):
    calls = {}
    monkeypatch.setattr(realtime_cache, "execute", lambda sql, args=(): calls.update(sql=sql, args=args) or 1)
    realtime_cache.put("k", {"v": 1}, 660)
    assert "REPLACE INTO" in calls["sql"]
    assert "NOW(3) + INTERVAL" in calls["sql"]
    assert calls["args"][0] == "k"
    assert json.loads(calls["args"][1]) == {"v": 1}
    assert calls["args"][2] == 660


def test_get_hit_decodes_payload(monkeypatch):
    monkeypatch.setattr(realtime_cache, "query", lambda sql, args=(): [{"payload": '{"v": 1}'}])
    assert realtime_cache.get("k") == {"v": 1}


def test_get_miss_returns_none(monkeypatch):
    monkeypatch.setattr(realtime_cache, "query", lambda sql, args=(): [])
    assert realtime_cache.get("k") is None


def test_get_filters_by_expiry(monkeypatch):
    cap = {}
    monkeypatch.setattr(realtime_cache, "query", lambda sql, args=(): cap.update(sql=sql) or [])
    realtime_cache.get("k")
    assert "expires_at > NOW(3)" in cap["sql"]


def test_get_swallows_db_error(monkeypatch):
    def boom(sql, args=()):
        raise RuntimeError("db down")
    monkeypatch.setattr(realtime_cache, "query", boom)
    assert realtime_cache.get("k") is None  # 容错：DB 故障不崩，按 MISS 处理


def test_put_swallows_db_error(monkeypatch):
    def boom(sql, args=()):
        raise RuntimeError("db down")
    monkeypatch.setattr(realtime_cache, "execute", boom)
    realtime_cache.put("k", {"v": 1}, 60)  # 不抛异常


def test_ttl_constants():
    assert realtime_cache.TTL_SINGLE_DAY_OPEN == 60
    assert realtime_cache.TTL_MULTI_DAY_OPEN == 660
    assert realtime_cache.TTL_CLOSED == 1800


def test_make_cache_key_ignores_empty():
    assert realtime_cache.make_cache_key({"a": 1, "b": None, "c": ""}) == realtime_cache.make_cache_key({"a": 1})
