from __future__ import annotations


def test_push_quality_check_scheduler_registers_ten_minute_controlled_job():
    from appcore import push_quality_check_scheduler

    calls = []

    class FakeScheduler:
        def add_job(self, func, trigger, **kwargs):
            calls.append((func, trigger, kwargs))

    push_quality_check_scheduler.register(FakeScheduler())

    assert len(calls) == 1
    func, trigger, kwargs = calls[0]
    assert getattr(func, "__wrapped__", None) is push_quality_check_scheduler.tick_once
    assert trigger == "interval"
    assert kwargs["minutes"] == 10
    assert kwargs["id"] == "push_quality_check_tick"
    assert kwargs["replace_existing"] is True
    assert kwargs["max_instances"] == 1


def test_push_quality_check_scheduler_tick_evaluates_ready_pending_and_pushed_items(monkeypatch):
    from appcore import push_quality_check_scheduler

    items = [
        {"id": 7, "product_id": 3, "lang": "de"},
        {"id": 8, "product_id": 3, "lang": "fr"},
    ]
    product = {"id": 3}
    evaluated = []

    monkeypatch.setattr(
        push_quality_check_scheduler.pushes,
        "list_items_for_push",
        lambda limit=None: (items, len(items)),
    )
    monkeypatch.setattr(
        push_quality_check_scheduler.pushes,
        "compute_status",
        lambda item_shape, product_shape: "pushed" if item_shape["id"] == 8 else "pending",
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

    summary = push_quality_check_scheduler.tick_once()

    assert evaluated == [(7, "auto"), (8, "auto")]
    assert summary["evaluated"] == 2


def test_push_quality_check_scheduler_tick_skips_failed_push_status(monkeypatch):
    from appcore import push_quality_check_scheduler

    item = {"id": 9, "product_id": 4, "lang": "es"}

    monkeypatch.setattr(
        push_quality_check_scheduler.pushes,
        "list_items_for_push",
        lambda limit=None: ([item], 1),
    )
    monkeypatch.setattr(
        push_quality_check_scheduler.pushes,
        "compute_status",
        lambda item_shape, product_shape: "failed",
    )
    monkeypatch.setattr(
        push_quality_check_scheduler.push_quality_checks,
        "has_reusable_auto_result_for_item",
        lambda item_shape, product_shape: (_ for _ in ()).throw(AssertionError("not eligible")),
    )
    monkeypatch.setattr(
        push_quality_check_scheduler.push_quality_checks,
        "evaluate_item",
        lambda item_id, source="auto": (_ for _ in ()).throw(AssertionError("not eligible")),
    )

    summary = push_quality_check_scheduler.tick_once()

    assert summary["eligible"] == 0
    assert summary["skipped_status"] == 1
    assert summary["evaluated"] == 0


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

    summary = push_quality_check_scheduler.tick_once()

    assert summary["skipped_existing"] == 1
    assert summary["evaluated"] == 0


def test_push_quality_check_scheduler_tick_evaluates_all_current_candidates(monkeypatch):
    from appcore import push_quality_check_scheduler

    items = [
        {"id": 1, "product_id": 101, "lang": "es"},
        {"id": 2, "product_id": 102, "lang": "fr"},
        {"id": 3, "product_id": 103, "lang": "de"},
        {"id": 4, "product_id": 104, "lang": "it"},
        {"id": 5, "product_id": 105, "lang": "pt"},
        {"id": 6, "product_id": 106, "lang": "ja"},
    ]
    evaluated = []

    monkeypatch.setattr(
        push_quality_check_scheduler.pushes,
        "list_items_for_push",
        lambda limit=None: (items, len(items)),
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

    summary = push_quality_check_scheduler.tick_once()

    assert evaluated == [(item["id"], "auto") for item in items]
    assert summary["evaluated"] == len(items)
