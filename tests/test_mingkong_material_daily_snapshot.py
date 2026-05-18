from __future__ import annotations

import tools.mingkong_material_daily_snapshot as runner


def test_arg_parser_defaults_to_top300_and_sleep_policy():
    args = runner.build_arg_parser().parse_args([])

    assert args.source_limit == 300
    assert args.batch_size == 10
    assert args.sleep_after_products == 2
    assert args.sleep_seconds == 30


def test_main_invokes_service_run(monkeypatch):
    called = {}

    def fake_run_daily_snapshot(**kwargs):
        called.update(kwargs)
        return {"processed_product_count": 3}

    monkeypatch.setattr(
        runner.mingkong_materials,
        "run_daily_snapshot",
        fake_run_daily_snapshot,
    )

    assert runner.main(["--source-limit", "3", "--sleep-seconds", "0"]) == 0
    assert called["source_limit"] == 3
    assert called["sleep_seconds"] == 0
