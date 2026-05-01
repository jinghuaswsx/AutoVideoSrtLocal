from __future__ import annotations


def test_db_pool_uses_configured_connection_limit(monkeypatch):
    import appcore.db as db

    captured = {}

    class FakePool:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "DB_POOL_MAX_CONNECTIONS", 48)
    monkeypatch.setattr(db, "PooledDB", FakePool)

    pool = db._get_pool()

    assert isinstance(pool, FakePool)
    assert captured["maxconnections"] == 48
