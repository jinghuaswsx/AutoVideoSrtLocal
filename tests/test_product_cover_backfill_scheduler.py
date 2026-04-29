def test_product_cover_backfill_scheduler_registers_ten_minute_job():
    from appcore import product_cover_backfill_scheduler

    calls = []

    class FakeScheduler:
        def add_job(self, func, trigger, **kwargs):
            calls.append((func, trigger, kwargs))

    product_cover_backfill_scheduler.register(FakeScheduler())

    assert len(calls) == 1
    func, trigger, kwargs = calls[0]
    assert getattr(func, "__wrapped__", None) is product_cover_backfill_scheduler.tick_once
    assert trigger == "interval"
    assert kwargs["minutes"] == 10
    assert kwargs["id"] == "product_cover_backfill_tick"
    assert kwargs["replace_existing"] is True
    assert kwargs["max_instances"] == 1


def test_product_cover_backfill_scheduler_tick_runs_backfill(monkeypatch):
    from appcore import product_cover_backfill_scheduler

    called = {}
    monkeypatch.setattr(
        product_cover_backfill_scheduler.product_cover_backfill,
        "backfill_all_missing_covers",
        lambda: called.setdefault("ran", True),
    )

    product_cover_backfill_scheduler.tick_once()

    assert called["ran"] is True


def test_global_scheduler_registers_product_cover_backfill_job():
    from pathlib import Path

    source = Path("appcore/scheduler.py").read_text(encoding="utf-8")

    assert "product_cover_backfill_scheduler" in source
    assert "product_cover_backfill_scheduler.register(_scheduler)" in source
