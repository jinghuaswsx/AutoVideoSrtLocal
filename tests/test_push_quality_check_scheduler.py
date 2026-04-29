from __future__ import annotations


def test_push_quality_check_scheduler_registers_ten_minute_job():
    from appcore import push_quality_check_scheduler

    calls = []

    class FakeScheduler:
        def add_job(self, func, trigger, **kwargs):
            calls.append((func, trigger, kwargs))

    push_quality_check_scheduler.register(FakeScheduler())

    assert len(calls) == 1
    func, trigger, kwargs = calls[0]
    assert func is push_quality_check_scheduler.tick_once
    assert trigger == "interval"
    assert kwargs["minutes"] == 10
    assert kwargs["id"] == "push_quality_check_tick"
    assert kwargs["replace_existing"] is True
    assert kwargs["max_instances"] == 1


def test_push_quality_check_scheduler_tick_evaluates_ready_pending_items(monkeypatch):
    from appcore import push_quality_check_scheduler

    item = {"id": 7, "product_id": 3, "lang": "de"}
    product = {"id": 3}
    evaluated = []

    monkeypatch.setattr(
        push_quality_check_scheduler.pushes,
        "list_items_for_push",
        lambda limit=None: ([item], 1),
    )
    monkeypatch.setattr(
        push_quality_check_scheduler.pushes,
        "compute_status",
        lambda item_shape, product_shape: "pending",
    )
    monkeypatch.setattr(
        push_quality_check_scheduler.push_quality_checks,
        "has_reusable_auto_result_for_item",
        lambda item_shape, product_shape: False,
    )
    monkeypatch.setattr(
        push_quality_check_scheduler.push_quality_checks,
        "evaluate_item",
        lambda item_id, source="auto": evaluated.append((item_id, source)),
    )

    summary = push_quality_check_scheduler.tick_once(limit=5)

    assert evaluated == [(7, "auto")]
    assert summary["evaluated"] == 1


def test_push_quality_check_scheduler_tick_skips_existing_auto_result(monkeypatch):
    from appcore import push_quality_check_scheduler

    item = {"id": 8, "product_id": 4, "lang": "fr"}

    monkeypatch.setattr(
        push_quality_check_scheduler.pushes,
        "list_items_for_push",
        lambda limit=None: ([item], 1),
    )
    monkeypatch.setattr(
        push_quality_check_scheduler.pushes,
        "compute_status",
        lambda item_shape, product_shape: "pending",
    )
    monkeypatch.setattr(
        push_quality_check_scheduler.push_quality_checks,
        "has_reusable_auto_result_for_item",
        lambda item_shape, product_shape: True,
    )
    monkeypatch.setattr(
        push_quality_check_scheduler.push_quality_checks,
        "evaluate_item",
        lambda item_id, source="auto": (_ for _ in ()).throw(AssertionError("already checked")),
    )

    summary = push_quality_check_scheduler.tick_once(limit=5)

    assert summary["skipped_existing"] == 1
    assert summary["evaluated"] == 0
