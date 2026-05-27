from datetime import datetime

import pytest


def test_latest_failure_alert_only_returns_failed_latest_run(monkeypatch):
    from appcore import scheduled_tasks

    monkeypatch.setattr(
        scheduled_tasks,
        "_should_dispatch_failure_alert_for_run",
        lambda row: True,
    )
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


def test_latest_failure_alert_suppresses_unworthy_failed_latest_run(monkeypatch):
    from appcore import scheduled_tasks

    monkeypatch.setattr(
        scheduled_tasks,
        "_should_dispatch_failure_alert_for_run",
        lambda row: False,
    )
    monkeypatch.setattr(
        scheduled_tasks,
        "latest_run",
        lambda task_code: {"id": 9, "task_code": task_code, "status": "failed"},
    )

    assert scheduled_tasks.latest_failure_alert() is None


def _failure_run_row_mock(sql, params=()):
    """Mock for `_scheduled_task_run_by_id` query. Streak / dedup decisions
    are mocked via `feishu_alerts.should_dispatch_failure` directly."""
    if "WHERE id = %s" in sql:
        return [
            {
                "id": params[0],
                "task_code": "shopifyid",
                "task_name": "Shopify ID 获取",
                "status": "failed",
                "started_at": "2026-05-08 10:00:00",
                "finished_at": "2026-05-08 10:00:02",
                "duration_seconds": 2,
                "summary_json": '{"updated": 0}',
                "error_message": "boom",
                "output_file": None,
            }
        ]
    return []


def test_finish_run_injects_consecutive_failures_when_streak_ge_2(monkeypatch):
    """When AUT-21's dedup decides to send AND streak >= 2, the failure
    row must carry `consecutive_failures` so feishu_alerts renders the
    `连续失败：N 次` line. See:
    docs/superpowers/specs/2026-05-09-meta-daily-final-permission-recovery.md"""
    from appcore import feishu_alerts, scheduled_tasks

    sent = []
    monkeypatch.setattr(scheduled_tasks, "execute", lambda *a, **k: 1)
    monkeypatch.setattr(scheduled_tasks, "query", _failure_run_row_mock)
    monkeypatch.setattr(
        feishu_alerts,
        "should_dispatch_failure",
        lambda task_code, *, current_run_id, immediate=False: (True, 20),
    )
    monkeypatch.setattr(
        feishu_alerts,
        "send_scheduled_task_failure",
        lambda row: sent.append(row),
    )

    scheduled_tasks.finish_run(42, status="failed", error_message="boom")

    assert sent and sent[0]["id"] == 42
    assert sent[0]["consecutive_failures"] == 20


def test_finish_run_sample_gate_first_failure_sends_without_consecutive(monkeypatch):
    from appcore import feishu_alerts, scheduled_tasks

    sent = []
    monkeypatch.setattr(scheduled_tasks, "execute", lambda *a, **k: 1)
    monkeypatch.setattr(
        scheduled_tasks,
        "query",
        lambda sql, params=(): [
            {
                "id": params[0],
                "task_code": "roi_hourly_sync",
                "task_name": "ROI sync",
                "status": "failed",
                "summary_json": '{"total": 21, "failed": 17}',
                "error_message": "sample batch failed",
            }
        ],
    )

    def fake_should_dispatch(task_code, *, current_run_id, immediate=False):
        assert immediate is True
        return True, 1

    monkeypatch.setattr(
        feishu_alerts,
        "should_dispatch_failure",
        fake_should_dispatch,
    )
    monkeypatch.setattr(
        feishu_alerts,
        "send_scheduled_task_failure",
        lambda row: sent.append(row),
    )

    scheduled_tasks.finish_run(42, status="failed", error_message="boom")

    assert sent and sent[0]["id"] == 42
    assert "consecutive_failures" not in sent[0]


def test_finish_run_suppresses_feishu_failure_when_dedup_says_no(monkeypatch):
    from appcore import feishu_alerts, scheduled_tasks

    sent = []
    monkeypatch.setattr(scheduled_tasks, "execute", lambda *a, **k: 1)
    monkeypatch.setattr(
        scheduled_tasks,
        "query",
        lambda sql, params=(): [
            {
                "id": params[0],
                "task_code": "roi_hourly_sync",
                "task_name": "ROI sync",
                "status": "failed",
                "summary_json": "{}",
            }
        ],
    )
    monkeypatch.setattr(
        feishu_alerts,
        "should_dispatch_failure",
        lambda task_code, *, current_run_id, immediate=False: (False, 3),
    )
    monkeypatch.setattr(
        feishu_alerts,
        "send_scheduled_task_failure",
        lambda row: sent.append(row),
    )

    scheduled_tasks.finish_run(7, status="failed", error_message="boom")

    assert sent == []


def test_failure_alert_policy_suppresses_single_failed_run_without_samples(monkeypatch):
    from appcore import feishu_alerts, scheduled_tasks

    monkeypatch.setattr(
        feishu_alerts,
        "consecutive_failure_count",
        lambda task_code, *, current_run_id: 1,
    )

    assert not scheduled_tasks._should_dispatch_failure_alert_for_run(
        {
            "id": 19810,
            "task_code": "meta_hot_posts_video_analysis_queue_tick",
            "status": "failed",
            "summary": {
                "running_run_replaced": 19810,
                "running_age_seconds": 4094,
                "running_us_copyability_reset": 1,
                "running_europe_fit_reset": 0,
            },
        }
    )


def test_failure_alert_policy_allows_sample_failure_rate_above_threshold(monkeypatch):
    from appcore import feishu_alerts, scheduled_tasks

    monkeypatch.setattr(
        feishu_alerts,
        "consecutive_failure_count",
        lambda task_code, *, current_run_id: 1,
    )

    assert scheduled_tasks._should_dispatch_failure_alert_for_run(
        {
            "id": 120,
            "task_code": "roi_hourly_sync",
            "status": "failed",
            "summary": {"total": 21, "failed": 17},
        }
    )


def test_failure_alert_policy_suppresses_sample_failure_rate_at_eighty_percent(monkeypatch):
    from appcore import feishu_alerts, scheduled_tasks

    monkeypatch.setattr(
        feishu_alerts,
        "consecutive_failure_count",
        lambda task_code, *, current_run_id: 1,
    )

    assert not scheduled_tasks._should_dispatch_failure_alert_for_run(
        {
            "id": 121,
            "task_code": "roi_hourly_sync",
            "status": "failed",
            "summary": {"total": 25, "failed": 20},
        }
    )


def _video_localization_alert_query(daily_summary_rows):
    def fake_query(sql, params=()):
        if "WHERE id = %s" in sql:
            return [
                {
                    "id": params[0],
                    "task_code": "meta_hot_posts_video_localization_tick",
                    "task_name": "Meta hot posts video localization",
                    "status": "failed",
                    "summary_json": '{"scanned": 5, "downloaded": 0, "failed": 5}',
                    "error_message": "5 video(s) failed",
                }
            ]
        if "task_code = %s" in sql and "CURDATE()" in sql:
            return daily_summary_rows
        return []

    return fake_query


def test_video_localization_failure_alert_suppressed_until_daily_attempts_exceed_20(monkeypatch):
    from appcore import feishu_alerts, scheduled_tasks

    sent = []
    daily_rows = [
        {"id": 41, "summary_json": '{"scanned": 10, "downloaded": 0, "failed": 10}'},
        {"id": 42, "summary_json": '{"scanned": 10, "downloaded": 0, "failed": 10}'},
    ]
    monkeypatch.setattr(scheduled_tasks, "execute", lambda *a, **k: 1)
    monkeypatch.setattr(scheduled_tasks, "query", _video_localization_alert_query(daily_rows))
    monkeypatch.setattr(
        feishu_alerts,
        "should_dispatch_failure",
        lambda task_code, *, current_run_id, immediate=False: (immediate, 1),
    )
    monkeypatch.setattr(
        feishu_alerts,
        "send_scheduled_task_failure",
        lambda row: sent.append(row),
    )

    scheduled_tasks.finish_run(42, status="failed", error_message="boom")

    assert sent == []


def test_video_localization_failure_alert_suppressed_at_eighty_percent_failure_rate(monkeypatch):
    from appcore import feishu_alerts, scheduled_tasks

    sent = []
    daily_rows = [
        {"id": 43, "summary_json": '{"scanned": 25, "downloaded": 5, "failed": 20}'},
    ]
    monkeypatch.setattr(scheduled_tasks, "execute", lambda *a, **k: 1)
    monkeypatch.setattr(scheduled_tasks, "query", _video_localization_alert_query(daily_rows))
    monkeypatch.setattr(
        feishu_alerts,
        "should_dispatch_failure",
        lambda task_code, *, current_run_id, immediate=False: (immediate, 1),
    )
    monkeypatch.setattr(
        feishu_alerts,
        "send_scheduled_task_failure",
        lambda row: sent.append(row),
    )

    scheduled_tasks.finish_run(43, status="failed", error_message="boom")

    assert sent == []


def test_video_localization_failure_alert_sends_when_daily_failure_rate_above_eighty_percent(monkeypatch):
    from appcore import feishu_alerts, scheduled_tasks

    sent = []
    daily_rows = [
        {"id": 44, "summary_json": '{"scanned": 21, "downloaded": 4, "failed": 17}'},
    ]
    monkeypatch.setattr(scheduled_tasks, "execute", lambda *a, **k: 1)
    monkeypatch.setattr(scheduled_tasks, "query", _video_localization_alert_query(daily_rows))
    monkeypatch.setattr(
        feishu_alerts,
        "should_dispatch_failure",
        lambda task_code, *, current_run_id, immediate=False: (immediate, 1),
    )
    monkeypatch.setattr(
        feishu_alerts,
        "send_scheduled_task_failure",
        lambda row: sent.append(row),
    )

    scheduled_tasks.finish_run(44, status="failed", error_message="boom")

    assert sent and sent[0]["id"] == 44


def test_finish_run_dispatches_recovery_alert_when_prior_failures(monkeypatch):
    from appcore import feishu_alerts, scheduled_tasks

    sent = []
    monkeypatch.setattr(scheduled_tasks, "execute", lambda *a, **k: 1)
    monkeypatch.setattr(
        scheduled_tasks,
        "query",
        lambda sql, params=(): [
            {
                "id": params[0],
                "task_code": "roi_hourly_sync",
                "task_name": "ROI sync",
                "status": "success",
                "summary_json": "{}",
                "started_at": "2026-05-09 18:00:00",
                "finished_at": "2026-05-09 18:01:00",
                "duration_seconds": 60,
            }
        ],
    )
    monkeypatch.setattr(
        feishu_alerts,
        "prior_consecutive_failures_before_run",
        lambda task_code, *, current_run_id: 20,
    )
    monkeypatch.setattr(
        feishu_alerts,
        "send_scheduled_task_recovery",
        lambda row, *, prior_failures: sent.append((row["id"], prior_failures)),
    )

    scheduled_tasks.finish_run(80, status="success")

    assert sent == [(80, 20)]


def test_finish_run_does_not_dispatch_recovery_when_no_prior_failures(monkeypatch):
    from appcore import feishu_alerts, scheduled_tasks

    sent = []
    monkeypatch.setattr(scheduled_tasks, "execute", lambda *a, **k: 1)
    monkeypatch.setattr(
        scheduled_tasks,
        "query",
        lambda sql, params=(): [
            {
                "id": params[0],
                "task_code": "roi_hourly_sync",
                "task_name": "ROI sync",
                "status": "success",
                "summary_json": "{}",
            }
        ],
    )
    monkeypatch.setattr(
        feishu_alerts,
        "prior_consecutive_failures_before_run",
        lambda task_code, *, current_run_id: 0,
    )
    monkeypatch.setattr(
        feishu_alerts,
        "send_scheduled_task_recovery",
        lambda row, *, prior_failures: sent.append("called"),
    )

    scheduled_tasks.finish_run(81, status="success")

    assert sent == []


def test_video_localization_success_does_not_dispatch_recovery_alert(monkeypatch):
    from appcore import feishu_alerts, scheduled_tasks

    sent = []
    monkeypatch.setattr(scheduled_tasks, "execute", lambda *a, **k: 1)
    monkeypatch.setattr(
        scheduled_tasks,
        "query",
        lambda sql, params=(): [
            {
                "id": params[0],
                "task_code": "meta_hot_posts_video_localization_tick",
                "task_name": "Meta hot posts video localization",
                "status": "success",
                "summary_json": '{"scanned": 3, "downloaded": 3, "failed": 0}',
            }
        ],
    )
    monkeypatch.setattr(
        feishu_alerts,
        "prior_consecutive_failures_before_run",
        lambda task_code, *, current_run_id: 4,
    )
    monkeypatch.setattr(
        feishu_alerts,
        "send_scheduled_task_recovery",
        lambda row, *, prior_failures: sent.append((row["id"], prior_failures)),
    )

    scheduled_tasks.finish_run(90, status="success")

    assert sent == []


def test_finish_run_feishu_alert_error_does_not_block_update(monkeypatch):
    from appcore import feishu_alerts, scheduled_tasks

    updates = []
    monkeypatch.setattr(scheduled_tasks, "execute", lambda *a, **k: updates.append(a) or 1)
    monkeypatch.setattr(scheduled_tasks, "query", _failure_run_row_mock)
    monkeypatch.setattr(
        feishu_alerts,
        "should_dispatch_failure",
        lambda task_code, *, current_run_id, immediate=False: (True, 1),
    )

    def fake_send(row):
        raise RuntimeError("feishu down")

    monkeypatch.setattr(feishu_alerts, "send_scheduled_task_failure", fake_send)

    scheduled_tasks.finish_run(42, status="failed", error_message="boom")

    assert updates


def test_format_scheduled_task_failure_renders_consecutive_count():
    from appcore import feishu_alerts

    text = feishu_alerts.format_scheduled_task_failure(
        {
            "id": 99,
            "task_code": "meta_daily_final",
            "task_name": "Meta 收盘日数据",
            "status": "failed",
            "duration_seconds": 12,
            "error_message": "boom",
            "consecutive_failures": 3,
            "summary": {},
        }
    )

    assert "连续失败：3 次" in text
    assert "任务：Meta 收盘日数据" in text


def test_format_scheduled_task_failure_omits_consecutive_for_single_failure():
    from appcore import feishu_alerts

    text = feishu_alerts.format_scheduled_task_failure(
        {
            "id": 1,
            "task_code": "shopifyid",
            "task_name": "Shopify ID 获取",
            "status": "failed",
            "duration_seconds": 5,
            "error_message": "boom",
            "summary": {},
        }
    )

    assert "连续失败" not in text


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


def test_task_definitions_include_push_status_cache_refresh():
    from appcore import scheduled_tasks

    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}

    task = definitions["push_status_cache_refresh"]
    assert task["source_type"] == "apscheduler"
    assert task["source_ref"] == "push_status_cache_refresh"
    assert task["runner"] == "appcore.push_status_cache_scheduler.tick_once"
    assert task["log_table"] == "scheduled_task_runs"
    assert "2026-05-22-pushes-status-cache-design.md" in task["description"]


def test_task_definitions_include_apimart_balance_watchdog():
    from appcore import scheduled_tasks

    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}

    task = definitions["apimart_balance_watchdog"]
    assert task["schedule"] == "每小时"
    assert task["source_type"] == "apscheduler"
    assert task["runner"] == "appcore.apimart_balance_watchdog.run_scheduled_check"
    assert task["log_table"] == "scheduled_task_runs"
    assert task["failure_alert_immediate"] is True
    assert "2026-05-15-apimart-balance-watchdog-design.md" in task["description"]


def test_failure_alert_policy_allows_immediate_task_first_failure(monkeypatch):
    from appcore import feishu_alerts, scheduled_tasks

    monkeypatch.setattr(
        feishu_alerts,
        "consecutive_failure_count",
        lambda task_code, current_run_id=None: 1,
    )

    assert scheduled_tasks._should_dispatch_failure_alert_for_run(
        {
            "id": 77,
            "task_code": "apimart_balance_watchdog",
            "status": "failed",
            "summary": {"reason": "usage_gap"},
        }
    )


def test_task_definitions_include_meta_hot_posts_tasks():
    from appcore import scheduled_tasks

    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}

    sync_task = definitions["meta_hot_posts_sync_tick"]
    assert sync_task["schedule"] == "每天 07:00（北京时间），按上游接口全集采集"
    assert sync_task["source_type"] == "apscheduler"
    assert sync_task["runner"] == "appcore.meta_hot_posts.scheduler.sync_tick_once"
    assert sync_task["log_table"] == "scheduled_task_runs"
    assert "按上游接口 total/空页停止条件采集全集" in sync_task["description"]
    assert "2026-05-15-meta-hot-posts-full-sync-design.md" in sync_task["description"]

    analysis_task = definitions["meta_hot_posts_analysis_tick"]
    assert analysis_task["schedule"] == "每 10 分钟"
    assert analysis_task["source_type"] == "apscheduler"
    assert analysis_task["runner"] == "appcore.meta_hot_posts.scheduler.analysis_tick_once"
    assert analysis_task["log_table"] == "scheduled_task_runs"
    assert "30 个" in analysis_task["description"]
    assert "20 秒" in analysis_task["description"]

    video_task = definitions["meta_hot_posts_video_localization_tick"]
    assert video_task["schedule"]
    assert video_task["source_type"] == "apscheduler"
    assert video_task["runner"] == "appcore.meta_hot_posts.scheduler.video_localization_tick_once"
    assert video_task["log_table"] == "scheduled_task_runs"
    assert "2026-05-14-meta-hot-posts-video-localization-design.md" in video_task["description"]

    tos_video_task = definitions["meta_hot_posts_tos_video_sync_tick"]
    assert tos_video_task["schedule"]
    assert tos_video_task["source_type"] == "apscheduler"
    assert tos_video_task["runner"] == "appcore.meta_hot_posts.tos_sync.run_scheduled_tos_video_sync"
    assert tos_video_task["log_table"] == "scheduled_task_runs"
    assert "2026-05-16-meta-hot-posts-tos-video-sync-design.md" in tos_video_task["description"]

    queue_task = definitions["meta_hot_posts_video_analysis_queue_tick"]
    assert queue_task["schedule"] == "Every 10 minutes"
    assert queue_task["source_type"] == "apscheduler"
    assert queue_task["runner"] == "appcore.meta_hot_posts.scheduler.video_analysis_queue_tick_once"
    assert queue_task["log_table"] == "scheduled_task_runs"
    assert "10" in queue_task["description"]
    assert "560-second window" in queue_task["description"]
    assert "40-second hard per-item timeout" in queue_task["description"]
    assert "first rate-limit" in queue_task["description"]
    assert "rate-limit" in queue_task["description"]
    assert "Vertex ADC" not in queue_task["description"]
    assert "OpenRouter" in queue_task["description"]
    assert "google/gemini-3-flash-preview" in queue_task["description"]
    assert "2026-05-15-meta-hot-posts-unified-video-analysis-queue-design.md" in queue_task["description"]

    translation_task = definitions["meta_hot_posts_translate_messages_tick"]
    assert translation_task["schedule"] == "每 10 分钟"
    assert translation_task["source_type"] == "apscheduler"
    assert translation_task["runner"] == "appcore.meta_hot_posts.scheduler.translation_tick_once"
    assert translation_task["log_table"] == "scheduled_task_runs"
    assert "product_title_zh" in translation_task["description"]
    assert "2026-05-19-meta-hot-posts-product-title-translation-design.md" in translation_task["description"]
    assert "30 条" in translation_task["description"]
    assert "中文" in translation_task["description"]

    assert "meta_hot_posts_video_copyability_tick" not in definitions
    assert "meta_hot_posts_europe_fit_tick" not in definitions


def test_task_definitions_include_server_and_app_timers():
    from appcore import scheduled_tasks

    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}

    assert definitions["shopifyid"]["schedule"] == "每天 12:11（与 ROI :00/:20/:40 错峰）"
    assert definitions["roi_hourly_sync"]["schedule"] == "每 20 分钟（每小时 :00/:20/:40）"
    assert definitions["shopifyid"]["source_ref"] == "autovideosrt-shopifyid-sync.timer"
    assert definitions["roi_hourly_sync"]["source_ref"] == "autovideosrt-roi-realtime-sync.timer"
    assert "autovideosrt-meta-daily-final-sync.timer" in definitions["meta_daily_final"]["source_ref"]
    assert definitions["cdp_environment_watchdog"]["schedule"] == "每 1 分钟"
    assert definitions["cdp_environment_watchdog"]["source_ref"] == "autovideosrt-cdp-environment-watchdog.timer"
    assert definitions["cdp_environment_watchdog"]["log_table"] == "scheduled_task_runs"
    assert definitions["product_cover_backfill_tick"]["schedule"] == "每 10 分钟"
    assert definitions["task_center_raw_niuma_watch"]["source_type"] == "apscheduler"
    assert definitions["task_center_raw_niuma_watch"]["schedule"] == "每 1 分钟"
    assert definitions["task_center_raw_niuma_watch"]["runner"] == "appcore.task_center_raw_niuma_scheduler.tick_once"
    assert definitions["task_center_raw_niuma_watch"]["log_table"] == "task_events"
    assert definitions["tts_convergence_stats"]["source_type"] == "cron"


def test_task_definitions_include_sku_actual_breakeven_roas():
    from appcore import scheduled_tasks

    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}

    task = definitions["sku_actual_breakeven_roas"]
    assert task["schedule"] == "每天 01:00（北京时间）"
    assert task["source_type"] == "systemd"
    assert task["source_ref"] == "autovideosrt-sku-actual-roas.timer"
    assert task["runner"] == "tools/sku_actual_roas_snapshot.py"
    assert task["log_table"] == "scheduled_task_runs"
    assert "2026-05-10-sku-actual-breakeven-roas-design.md" in task["description"]


def test_task_definitions_include_dianxiaomi_listing_ranking_sync():
    from appcore import scheduled_tasks

    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}

    task = definitions["dianxiaomi_listing_ranking_sync"]
    assert task["schedule"] == "每天 12:40（北京时间，刷新最近 7 天最新榜单）"
    assert task["source_type"] == "systemd"
    assert task["source_ref"] == "autovideosrt-dianxiaomi-listing-ranking-sync.timer"
    assert task["runner"] == "tools/dianxiaomi_listing_ranking_sync.py"
    assert task["log_table"] == "scheduled_task_runs"
    assert "近7天销量 Top500 归档" in task["name"]
    assert "2026-05-18-dianxiaomi-full-listing-archive-design.md" in task["description"]


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
    assert by_code["roi_hourly_sync"]["log_link_available"] is True
    assert by_code["active_task_pre_restart_check"]["log_link_available"] is True
    assert by_code["tts_convergence_stats"]["log_link_available"] is False


def test_task_definitions_include_active_task_pre_restart_check():
    from appcore import scheduled_tasks

    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}

    task = definitions["active_task_pre_restart_check"]
    assert task["source_type"] == "manual_ops"
    assert task["runner"] == "python -m appcore.ops.active_tasks pre-restart"
    assert task["control_strategy"] == "readonly"
    assert task["log_source"] == "db:runtime_active_task_snapshots"


def test_list_runs_supports_active_task_snapshot_logs(monkeypatch):
    from appcore import scheduled_tasks

    def fake_query(sql, params):
        assert "runtime_active_task_snapshots" in sql
        assert params == (60,)
        return [
            {
                "id": 17,
                "snapshot_reason": "pre_restart",
                "project_type": "image_translate",
                "task_id": "task-100",
                "user_id": 9,
                "runner": "image_translate_runner",
                "entrypoint": "web.routes.image_translate.start",
                "stage": "uploading",
                "thread_name": "worker-1",
                "process_id": 1234,
                "interrupt_policy": "block_restart",
                "started_at": datetime(2026, 5, 2, 9, 0),
                "last_heartbeat_at": datetime(2026, 5, 2, 9, 3),
                "captured_at": datetime(2026, 5, 2, 9, 5),
                "details_json": '{"product_id": "p-1"}',
            }
        ]

    monkeypatch.setattr(scheduled_tasks, "query", fake_query)

    runs = scheduled_tasks.list_runs("active_task_pre_restart_check")

    assert runs[0]["task_code"] == "active_task_pre_restart_check"
    assert runs[0]["task_name"] == "Active task pre-restart check"
    assert runs[0]["status"] == "failed"
    assert runs[0]["started_at"] == datetime(2026, 5, 2, 9, 5)
    assert runs[0]["duration_seconds"] is None
    assert runs[0]["summary"]["snapshot_reason"] == "pre_restart"
    assert runs[0]["summary"]["project_type"] == "image_translate"
    assert runs[0]["summary"]["task_id"] == "task-100"
    assert runs[0]["summary"]["interrupt_policy"] == "block_restart"
    assert runs[0]["summary"]["details"] == {"product_id": "p-1"}


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
        if "runtime_active_task_snapshots" in sql:
            return []
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


def test_list_runs_all_includes_active_task_snapshot_logs(monkeypatch):
    from appcore import scheduled_tasks

    def fake_query(sql, params):
        if "runtime_active_task_snapshots" in sql:
            return [
                {
                    "id": 21,
                    "snapshot_reason": "shutdown_signal",
                    "project_type": "copywriting",
                    "task_id": "task-200",
                    "user_id": None,
                    "runner": "copywriting_runner",
                    "entrypoint": "",
                    "stage": "generating",
                    "thread_name": "worker-2",
                    "process_id": 4321,
                    "interrupt_policy": "block_restart",
                    "started_at": datetime(2026, 5, 2, 10, 0),
                    "last_heartbeat_at": datetime(2026, 5, 2, 10, 3),
                    "captured_at": datetime(2026, 5, 2, 10, 5),
                    "details_json": None,
                }
            ]
        return []

    monkeypatch.setattr(scheduled_tasks, "query", fake_query)

    runs = scheduled_tasks.list_runs("all")

    assert [run["task_code"] for run in runs] == ["active_task_pre_restart_check"]
    assert runs[0]["summary"]["snapshot_reason"] == "shutdown_signal"
    assert runs[0]["summary"]["project_type"] == "copywriting"


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

    result = scheduled_tasks.set_task_enabled(
        "shopifyid",
        False,
        actor="admin",
        confirmation="shopifyid",
    )

    assert result["control_state"] == "disabled"
    assert commands == [["systemctl", "disable", "--now", "autovideosrt-shopifyid-sync.timer"]]
    assert any(params[0] == "shopifyid" and params[1] == 0 for _, params in writes if params)


def test_set_task_enabled_requires_confirmation_for_systemd_timer(monkeypatch):
    from appcore import scheduled_tasks

    commands = []

    monkeypatch.setattr(
        scheduled_tasks,
        "_run_control_command",
        lambda command: commands.append(command) or {"ok": True, "message": "ok"},
    )
    monkeypatch.setattr(scheduled_tasks, "execute", lambda *args, **kwargs: 1)

    with pytest.raises(ValueError, match="确认"):
        scheduled_tasks.set_task_enabled("shopifyid", False, actor="admin")

    assert commands == []


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


def test_sync_scheduler_job_state_preserves_active_next_run_time(monkeypatch):
    from appcore import scheduled_tasks

    class Job:
        next_run_time = datetime(2026, 5, 14, 18, 0, 5)

    class FakeScheduler:
        def __init__(self):
            self.calls = []

        def get_job(self, task_code):
            return Job()

        def resume_job(self, task_code):
            self.calls.append(("resume", task_code))

        def pause_job(self, task_code):
            self.calls.append(("pause", task_code))

    fake = FakeScheduler()
    monkeypatch.setattr(scheduled_tasks, "is_task_enabled", lambda task_code: True)

    scheduled_tasks.sync_scheduler_job_state(fake, "meta_hot_posts_video_localization_tick")

    assert fake.calls == []


def test_sync_scheduler_job_state_resumes_paused_enabled_job(monkeypatch):
    from appcore import scheduled_tasks

    class Job:
        next_run_time = None

    class FakeScheduler:
        def __init__(self):
            self.calls = []

        def get_job(self, task_code):
            return Job()

        def resume_job(self, task_code):
            self.calls.append(("resume", task_code))

        def pause_job(self, task_code):
            self.calls.append(("pause", task_code))

    fake = FakeScheduler()
    monkeypatch.setattr(scheduled_tasks, "is_task_enabled", lambda task_code: True)

    scheduled_tasks.sync_scheduler_job_state(fake, "meta_hot_posts_video_localization_tick")

    assert fake.calls == [("resume", "meta_hot_posts_video_localization_tick")]
