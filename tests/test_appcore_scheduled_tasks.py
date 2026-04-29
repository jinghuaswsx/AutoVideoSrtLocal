from datetime import datetime


def test_latest_failure_alert_only_returns_failed_latest_run(monkeypatch):
    from appcore import scheduled_tasks

    monkeypatch.setattr(
        scheduled_tasks,
        "latest_run",
        lambda task_code: {"id": 9, "task_code": task_code, "status": "failed"},
    )

    assert scheduled_tasks.latest_failure_alert() == {
        "id": 9,
        "task_code": "shopifyid",
        "status": "failed",
    }

    monkeypatch.setattr(
        scheduled_tasks,
        "latest_run",
        lambda task_code: {"id": 10, "task_code": task_code, "status": "success"},
    )

    assert scheduled_tasks.latest_failure_alert() is None


def test_normalize_row_decodes_summary_json():
    from appcore import scheduled_tasks

    row = scheduled_tasks._normalize_row(
        {
            "id": 1,
            "task_code": "shopifyid",
            "summary_json": '{"updated": 3, "fetched": 404}',
            "started_at": datetime(2026, 4, 25, 12, 10),
        }
    )

    assert row["summary"] == {"updated": 3, "fetched": 404}
    assert "summary_json" not in row


def test_task_definitions_include_tos_backup():
    from appcore import scheduled_tasks

    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}

    assert definitions["tos_backup"]["schedule"] == "每天 02:00"


def test_task_definitions_include_server_and_app_timers():
    from appcore import scheduled_tasks

    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}

    assert definitions["shopifyid"]["source_ref"] == "autovideosrt-shopifyid-sync.timer"
    assert definitions["roi_hourly_sync"]["source_ref"] == "autovideosrt-roi-realtime-sync.timer"
    assert "autovideosrt-meta-daily-final-sync.timer" in definitions["meta_daily_final"]["source_ref"]
    assert definitions["product_cover_backfill_tick"]["schedule"] == "每 10 分钟"
    assert definitions["tts_convergence_stats"]["source_type"] == "cron"


def test_list_runs_all_merges_scheduled_task_and_roi_tables(monkeypatch):
    from appcore import scheduled_tasks

    def fake_query(sql, params):
        if "roi_hourly_sync_runs" in sql:
            return [
                {
                    "id": 2,
                    "task_code": "roi_hourly_sync",
                    "status": "success",
                    "started_at": datetime(2026, 4, 29, 19, 20),
                    "finished_at": datetime(2026, 4, 29, 19, 21),
                    "duration_seconds": 60,
                    "summary_json": '{"order_hours_upserted": 3}',
                    "error_message": None,
                    "output_file": None,
                }
            ]
        return [
            {
                "id": 1,
                "task_code": "shopifyid",
                "task_name": "Shopify ID 获取",
                "status": "success",
                "scheduled_for": None,
                "started_at": datetime(2026, 4, 29, 12, 10),
                "finished_at": datetime(2026, 4, 29, 12, 11),
                "duration_seconds": 60,
                "summary_json": '{"updated": 5}',
                "error_message": None,
                "output_file": None,
            }
        ]

    monkeypatch.setattr(scheduled_tasks, "query", fake_query)

    runs = scheduled_tasks.list_runs("all")

    assert [run["task_code"] for run in runs] == ["roi_hourly_sync", "shopifyid"]
    assert runs[0]["task_name"] == "店小秘订单与 ROAS 实时同步"
    assert runs[0]["summary"] == {"order_hours_upserted": 3}
