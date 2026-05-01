from datetime import datetime

import pytest


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


def test_task_definitions_include_push_quality_check():
    from appcore import scheduled_tasks

    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}

    task = definitions["push_quality_check_tick"]
    assert task["schedule"] == "每 10 分钟"
    assert "待推送和已推送" in task["description"]
    assert "每个素材最多自动检查一次" in task["description"]
    assert task["source_type"] == "apscheduler"
    assert task["runner"] == "appcore.push_quality_check_scheduler.tick_once"
    assert task["log_table"] == "scheduled_task_runs"


def test_task_definitions_include_server_and_app_timers():
    from appcore import scheduled_tasks

    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}

    assert definitions["shopifyid"]["schedule"] == "每天 12:11（与 ROI :02/:22/:42 错峰）"
    assert definitions["roi_hourly_sync"]["schedule"] == "每 20 分钟（每小时 :02/:22/:42）"
    assert definitions["shopifyid"]["source_ref"] == "autovideosrt-shopifyid-sync.timer"
    assert definitions["roi_hourly_sync"]["source_ref"] == "autovideosrt-roi-realtime-sync.timer"
    assert "autovideosrt-meta-daily-final-sync.timer" in definitions["meta_daily_final"]["source_ref"]
    assert definitions["product_cover_backfill_tick"]["schedule"] == "每 10 分钟"
    assert definitions["tts_convergence_stats"]["source_type"] == "cron"


def test_task_definitions_expose_control_strategy_and_log_source():
    from appcore import scheduled_tasks

    definitions = scheduled_tasks.task_definitions()

    missing_control = [item["code"] for item in definitions if not item.get("control_strategy")]
    missing_log_source = [item["code"] for item in definitions if not item.get("log_source")]

    assert missing_control == []
    assert missing_log_source == []

    by_code = {item["code"]: item for item in definitions}
    assert by_code["cleanup"]["log_source"] == "service:autovideosrt"
    assert by_code["roi_hourly_sync"]["log_source"] == "db:roi_hourly_sync_runs"
    assert by_code["tts_convergence_stats"]["log_source"] == "file:/var/log/tts_convergence.log"


def test_task_definitions_include_audited_external_and_in_process_timers():
    from appcore import scheduled_tasks

    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}

    assert definitions["shopifyid_windows_daily"]["source_type"] == "windows"
    assert (
        definitions["shopifyid_windows_daily"]["source_ref"]
        == "AutoVideoSrtLocal-ShopifyIdDianxiaomiSyncDaily"
    )
    assert definitions["dianxiaomi_order_import"]["log_table"] == "dianxiaomi_order_import_batches"
    assert definitions["meta_realtime_import"]["log_table"] == "meta_ad_realtime_import_runs"
    assert definitions["medias_detail_fetch_cleanup"]["source_type"] == "in_process"
    assert definitions["medias_detail_fetch_cleanup"]["schedule"] == "每 60 秒"
    assert definitions["voice_match_cleanup"]["source_type"] == "in_process"
    assert definitions["voice_match_cleanup"]["schedule"] == "每 60 秒"


def test_list_runs_all_merges_scheduled_task_and_roi_tables(monkeypatch):
    from appcore import scheduled_tasks

    def fake_query(sql, params):
        if "dianxiaomi_order_import_batches" in sql:
            return [
                {
                    "id": 4,
                    "status": "success",
                    "started_at": datetime(2026, 4, 29, 19, 21),
                    "finished_at": datetime(2026, 4, 29, 19, 22),
                    "duration_seconds": 55,
                    "summary_json": '{"fetched_orders": 12}',
                    "error_message": None,
                    "output_file": None,
                    "date_from": "2026-04-29",
                    "date_to": "2026-04-29",
                    "total_pages": 2,
                    "fetched_orders": 12,
                    "fetched_lines": 18,
                    "inserted_lines": 9,
                    "updated_lines": 9,
                    "skipped_lines": 0,
                    "included_shopify_ids_count": 3,
                }
            ]
        if "meta_ad_realtime_import_runs" in sql:
            return [
                {
                    "id": 3,
                    "status": "success",
                    "started_at": datetime(2026, 4, 29, 19, 22),
                    "finished_at": datetime(2026, 4, 29, 19, 23),
                    "duration_seconds": 45,
                    "summary_json": '{"rows_imported": 10}',
                    "error_message": None,
                    "output_file": None,
                    "business_date": "2026-04-29",
                    "snapshot_at": datetime(2026, 4, 29, 19, 20),
                    "ad_account_ids": '["act_1"]',
                    "rows_imported": 10,
                    "spend_usd": "24.50",
                }
            ]
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

    assert [run["task_code"] for run in runs] == [
        "meta_realtime_import",
        "dianxiaomi_order_import",
        "roi_hourly_sync",
        "shopifyid",
    ]
    assert runs[0]["task_name"] == "Meta 实时广告导入"
    assert runs[0]["summary"]["rows_imported"] == 10
    assert runs[1]["task_name"] == "店小秘订单导入"
    assert runs[1]["summary"]["fetched_orders"] == 12
    assert runs[2]["summary"] == {"order_hours_upserted": 3}


def test_list_runs_supports_dianxiaomi_order_import_batches(monkeypatch):
    from appcore import scheduled_tasks

    def fake_query(sql, params):
        assert "dianxiaomi_order_import_batches" in sql
        assert params == (60,)
        return [
            {
                "id": 8,
                "status": "success",
                "started_at": datetime(2026, 4, 29, 20, 20),
                "finished_at": datetime(2026, 4, 29, 20, 21),
                "duration_seconds": 40,
                "summary_json": None,
                "error_message": None,
                "output_file": None,
                "date_from": "2026-04-29",
                "date_to": "2026-04-29",
                "total_pages": 4,
                "fetched_orders": 18,
                "fetched_lines": 28,
                "inserted_lines": 20,
                "updated_lines": 8,
                "skipped_lines": 0,
                "included_shopify_ids_count": 5,
            }
        ]

    monkeypatch.setattr(scheduled_tasks, "query", fake_query)

    runs = scheduled_tasks.list_runs("dianxiaomi_order_import")

    assert runs[0]["task_code"] == "dianxiaomi_order_import"
    assert runs[0]["task_name"] == "店小秘订单导入"
    assert runs[0]["summary"]["fetched_lines"] == 28
    assert runs[0]["summary"]["inserted_lines"] == 20


def test_list_runs_supports_meta_realtime_import_runs(monkeypatch):
    from appcore import scheduled_tasks

    def fake_query(sql, params):
        assert "meta_ad_realtime_import_runs" in sql
        assert params == (60,)
        return [
            {
                "id": 9,
                "status": "skipped",
                "started_at": datetime(2026, 4, 29, 20, 20),
                "finished_at": datetime(2026, 4, 29, 20, 21),
                "duration_seconds": 35,
                "summary_json": None,
                "error_message": None,
                "output_file": None,
                "business_date": "2026-04-29",
                "snapshot_at": datetime(2026, 4, 29, 20, 20),
                "ad_account_ids": '["act_1", "act_2"]',
                "rows_imported": 0,
                "spend_usd": "0.00",
            }
        ]

    monkeypatch.setattr(scheduled_tasks, "query", fake_query)

    runs = scheduled_tasks.list_runs("meta_realtime_import")

    assert runs[0]["task_code"] == "meta_realtime_import"
    assert runs[0]["task_name"] == "Meta 实时广告导入"
    assert runs[0]["summary"]["business_date"] == "2026-04-29"
    assert runs[0]["summary"]["rows_imported"] == 0


def test_list_runs_for_special_table_includes_scheduled_failure_fallback(monkeypatch):
    from appcore import scheduled_tasks

    def fake_query(sql, params):
        if "FROM roi_hourly_sync_runs" in sql:
            return []
        if "FROM scheduled_task_runs" in sql and "WHERE task_code = %s" in sql:
            assert params == ("roi_hourly_sync", 60)
            return [
                {
                    "id": 12,
                    "task_code": "roi_hourly_sync",
                    "task_name": "ROI sync",
                    "status": "failed",
                    "scheduled_for": None,
                    "started_at": datetime(2026, 4, 29, 20, 40),
                    "finished_at": datetime(2026, 4, 29, 20, 50),
                    "duration_seconds": 600,
                    "summary_json": '{"reason": "browser_lock_timeout"}',
                    "error_message": "browser automation lock timeout",
                    "output_file": None,
                }
            ]
        return []

    monkeypatch.setattr(scheduled_tasks, "query", fake_query)

    runs = scheduled_tasks.list_runs("roi_hourly_sync")

    assert runs[0]["task_code"] == "roi_hourly_sync"
    assert runs[0]["status"] == "failed"
    assert runs[0]["summary"]["reason"] == "browser_lock_timeout"


def test_management_tasks_adds_control_state_from_control_table(monkeypatch):
    from appcore import scheduled_tasks

    def fake_query(sql, params=()):
        if "scheduled_task_controls" in sql:
            return [
                {
                    "task_code": "product_cover_backfill_tick",
                    "enabled": 0,
                    "last_action_status": "success",
                    "last_action_message": "paused",
                    "updated_by": "admin",
                    "updated_at": datetime(2026, 4, 29, 21, 0),
                }
            ]
        return []

    monkeypatch.setattr(scheduled_tasks, "execute", lambda *args, **kwargs: 1)
    monkeypatch.setattr(scheduled_tasks, "query", fake_query)

    tasks = {item["code"]: item for item in scheduled_tasks.management_tasks()}

    assert tasks["product_cover_backfill_tick"]["control_state"] == "disabled"
    assert tasks["product_cover_backfill_tick"]["control_label"] == "已停用"
    assert tasks["product_cover_backfill_tick"]["control_supported"] is True
    assert tasks["shopifyid"]["control_state"] == "enabled"
    assert tasks["tts_convergence_stats"]["control_state"] == "enabled"
    assert tasks["tts_convergence_stats"]["control_supported"] is False
    assert tasks["shopifyid_windows_daily"]["control_supported"] is False
    assert "Windows" in tasks["shopifyid_windows_daily"]["control_unavailable_reason"]
    assert tasks["meta_realtime_local_sync"]["control_supported"] is False
    assert "Windows" in tasks["meta_realtime_local_sync"]["control_unavailable_reason"]


def test_set_task_enabled_runs_systemctl_for_systemd_timer(monkeypatch):
    from appcore import scheduled_tasks

    commands = []
    writes = []

    def fake_run_command(command):
        commands.append(command)
        return {"ok": True, "message": "ok", "command": " ".join(command)}

    monkeypatch.setattr(scheduled_tasks, "_run_control_command", fake_run_command)
    monkeypatch.setattr(scheduled_tasks, "execute", lambda sql, params=(): writes.append((sql, params)) or 1)

    result = scheduled_tasks.set_task_enabled("shopifyid", False, actor="admin")

    assert result["control_state"] == "disabled"
    assert commands == [["systemctl", "disable", "--now", "autovideosrt-shopifyid-sync.timer"]]
    assert any(params[0] == "shopifyid" and params[1] == 0 for _, params in writes if params)


def test_set_task_enabled_writes_guarded_subtask_without_external_command(monkeypatch):
    from appcore import scheduled_tasks

    writes = []

    def fail_run_command(command):
        raise AssertionError(f"guarded task should not run external command: {command}")

    monkeypatch.setattr(scheduled_tasks, "_run_control_command", fail_run_command)
    monkeypatch.setattr(scheduled_tasks, "execute", lambda sql, params=(): writes.append((sql, params)) or 1)

    result = scheduled_tasks.set_task_enabled("dianxiaomi_order_import", False, actor="admin")

    assert result["control_state"] == "disabled"
    assert result["last_action_status"] == "success"
    assert "控制开关已写入" in result["last_action_message"]
    assert any(params[0] == "dianxiaomi_order_import" and params[1] == 0 for _, params in writes if params)


def test_set_task_enabled_rejects_local_windows_task(monkeypatch):
    from appcore import scheduled_tasks

    commands = []

    monkeypatch.setattr(
        scheduled_tasks,
        "_run_control_command",
        lambda command: commands.append(command) or {"ok": True, "message": "ok"},
    )
    monkeypatch.setattr(scheduled_tasks, "execute", lambda *args, **kwargs: 1)

    with pytest.raises(ValueError, match="不支持"):
        scheduled_tasks.set_task_enabled("shopifyid_windows_daily", False, actor="admin")

    assert commands == []


def test_set_task_enabled_rejects_readonly_cron_task(monkeypatch):
    from appcore import scheduled_tasks

    monkeypatch.setattr(scheduled_tasks, "execute", lambda *args, **kwargs: 1)

    with pytest.raises(ValueError, match="不支持"):
        scheduled_tasks.set_task_enabled("tts_convergence_stats", False, actor="admin")


def test_run_if_enabled_skips_disabled_task(monkeypatch):
    from appcore import scheduled_tasks

    monkeypatch.setattr(scheduled_tasks, "is_task_enabled", lambda task_code: False)

    def should_not_run():
        raise AssertionError("disabled task should be skipped")

    assert scheduled_tasks.run_if_enabled("product_cover_backfill_tick", should_not_run) == {
        "skipped": True,
        "reason": "scheduled task disabled",
        "task_code": "product_cover_backfill_tick",
    }
