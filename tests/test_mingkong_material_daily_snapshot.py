from __future__ import annotations

import tools.mingkong_material_daily_snapshot as runner


def test_arg_parser_defaults_to_top500_source_and_product_interval():
    args = runner.build_arg_parser().parse_args([])

    assert args.source_limit == 500
    assert args.batch_size == 10
    assert args.sleep_after_products == 1
    assert args.sleep_seconds == 1
    assert args.health_check_max_seconds == 3600
    assert args.health_check_interval_seconds == 10
    assert args.health_check_request_timeout_seconds == 10
    assert args.snapshot_at == ""


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

    assert runner.main([
        "--source-limit",
        "3",
        "--sleep-seconds",
        "0",
        "--health-check-max-seconds",
        "30",
        "--health-check-interval-seconds",
        "2",
        "--health-check-request-timeout-seconds",
        "1",
        "--snapshot-at",
        "2026-05-19 18:00:00",
    ]) == 0
    assert called["source_limit"] == 3
    assert called["sleep_seconds"] == 0
    assert called["health_check_max_seconds"] == 30
    assert called["health_check_interval_seconds"] == 2
    assert called["health_check_request_timeout_seconds"] == 1
    assert called["snapshot_at"] == "2026-05-19 18:00:00"
