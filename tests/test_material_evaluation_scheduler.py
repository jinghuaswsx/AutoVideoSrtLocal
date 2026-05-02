def test_material_evaluation_scheduler_registers_interval_job():
    from appcore import material_evaluation_scheduler

    calls = []

    class FakeScheduler:
        def add_job(self, func, trigger, **kwargs):
            calls.append((func, trigger, kwargs))

    material_evaluation_scheduler.register(FakeScheduler())

    assert len(calls) == 1
    func, trigger, kwargs = calls[0]
    assert getattr(func, "__wrapped__", None) is material_evaluation_scheduler.tick_once
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
        lambda limit=10: [7, 8],
    )
    monkeypatch.setattr(
        material_evaluation_scheduler.material_evaluation,
        "evaluate_product_if_ready",
        lambda product_id: evaluated.append(product_id),
    )

    material_evaluation_scheduler.tick_once()

    assert evaluated == [7, 8]


def test_material_evaluation_scheduler_tick_tracks_active_product(monkeypatch):
    from appcore import material_evaluation_scheduler, task_recovery

    evaluated = []
    registrations = []
    unregistered = []
    monkeypatch.setattr(
        material_evaluation_scheduler.material_evaluation,
        "find_ready_product_ids",
        lambda limit=10: [7],
    )
    monkeypatch.setattr(
        material_evaluation_scheduler.material_evaluation,
        "evaluate_product_if_ready",
        lambda product_id: evaluated.append(product_id),
    )
    monkeypatch.setattr(
        task_recovery,
        "try_register_active_task",
        lambda *args, **kwargs: registrations.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        task_recovery,
        "unregister_active_task",
        lambda *args: unregistered.append(args),
    )

    material_evaluation_scheduler.tick_once()

    assert evaluated == [7]
    assert registrations == [
        (
            ("material_evaluation", "7"),
            {
                "runner": "appcore.material_evaluation_scheduler.tick_once",
                "entrypoint": "material_evaluation_tick",
                "stage": "running_evaluation",
                "details": {"source": "scheduler"},
            },
        )
    ]
    assert unregistered == [("material_evaluation", "7")]


def test_material_evaluation_scheduler_tick_skips_active_product(monkeypatch):
    from appcore import material_evaluation_scheduler, task_recovery

    evaluated = []
    monkeypatch.setattr(
        material_evaluation_scheduler.material_evaluation,
        "find_ready_product_ids",
        lambda limit=10: [7],
    )
    monkeypatch.setattr(
        material_evaluation_scheduler.material_evaluation,
        "evaluate_product_if_ready",
        lambda product_id: evaluated.append(product_id),
    )
    monkeypatch.setattr(task_recovery, "try_register_active_task", lambda *args, **kwargs: False)

    material_evaluation_scheduler.tick_once()

    assert evaluated == []


def test_material_evaluation_scheduler_tick_uses_batch_size_10(monkeypatch):
    from appcore import material_evaluation_scheduler

    captured = {}

    def fake_find_ready_product_ids(limit=10):
        captured["limit"] = limit
        return []

    monkeypatch.setattr(
        material_evaluation_scheduler.material_evaluation,
        "find_ready_product_ids",
        fake_find_ready_product_ids,
    )
    monkeypatch.setattr(
        material_evaluation_scheduler.material_evaluation,
        "evaluate_product_if_ready",
        lambda product_id: None,
    )

    material_evaluation_scheduler.tick_once()

    assert captured["limit"] == 10
