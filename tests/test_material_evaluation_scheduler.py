def test_material_evaluation_scheduler_registers_interval_job():
    from appcore import material_evaluation_scheduler

    calls = []

    class FakeScheduler:
        def add_job(self, func, trigger, **kwargs):
            calls.append((func, trigger, kwargs))

    material_evaluation_scheduler.register(FakeScheduler())

    assert len(calls) == 1
    func, trigger, kwargs = calls[0]
    assert func is material_evaluation_scheduler.tick_once
    assert trigger == "interval"
    assert kwargs["minutes"] == 5
    assert kwargs["id"] == "material_evaluation_tick"
    assert kwargs["replace_existing"] is True


def test_material_evaluation_scheduler_tick_evaluates_ready_products(monkeypatch):
    from appcore import material_evaluation_scheduler

    evaluated = []
    monkeypatch.setattr(
        material_evaluation_scheduler.material_evaluation,
        "find_ready_product_ids",
        lambda limit=5: [7, 8],
    )
    monkeypatch.setattr(
        material_evaluation_scheduler.material_evaluation,
        "evaluate_product_if_ready",
        lambda product_id: evaluated.append(product_id),
    )

    material_evaluation_scheduler.tick_once()

    assert evaluated == [7, 8]
