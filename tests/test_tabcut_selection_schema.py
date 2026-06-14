from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tabcut_schema_defines_required_tables_and_indexes():
    sql = (
        ROOT / "db" / "migrations" / "2026_05_12_tabcut_selection.sql"
    ).read_text(encoding="utf-8")

    for table in [
        "tabcut_crawl_runs",
        "tabcut_videos",
        "tabcut_video_snapshots",
        "tabcut_goods",
        "tabcut_goods_snapshots",
        "tabcut_video_candidates",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

    assert "uniq_tabcut_video_snapshot" in sql
    assert "uniq_tabcut_goods_snapshot" in sql
    assert "uniq_tabcut_video_candidate" in sql


def test_tabcut_video_candidate_price_migration_defines_columns_and_index():
    sql = (
        ROOT / "db" / "migrations" / "2026_05_13_tabcut_video_candidate_price.sql"
    ).read_text(encoding="utf-8")

    assert "ALTER TABLE tabcut_video_candidates" in sql
    assert "primary_item_price_min DECIMAL(18, 4) NULL" in sql
    assert "primary_item_price_max DECIMAL(18, 4) NULL" in sql
    assert "price_currency VARCHAR(16) NULL" in sql
    assert "idx_tabcut_video_candidates_price" in sql


def test_tabcut_video_candidate_video_id_dedup_migration_keeps_earliest_record():
    sql = (
        ROOT / "db" / "migrations" / "2026_05_18_tabcut_video_candidate_video_id_dedup.sql"
    ).read_text(encoding="utf-8")

    assert "DELETE c" in sql
    assert "tabcut_video_candidates" in sql
    assert "MIN(id) AS keep_id" in sql
    assert "GROUP BY video_id" in sql
    assert "uniq_tabcut_video_candidate_video_id" in sql
    assert "UNIQUE KEY" in sql


def test_tabcut_mark_status_migration_adds_video_and_goods_annotations():
    sql = (
        ROOT / "db" / "migrations" / "2026_05_19_tabcut_mark_status.sql"
    ).read_text(encoding="utf-8")

    assert "ALTER TABLE tabcut_videos" in sql
    assert "ALTER TABLE tabcut_goods" in sql
    assert "ADD COLUMN is_marked TINYINT(1) NOT NULL DEFAULT 0" in sql
    assert "ADD COLUMN mark_status VARCHAR(16) NULL" in sql
    assert "ADD COLUMN marked_at DATETIME DEFAULT NULL" in sql
    assert "ADD COLUMN marked_by INT DEFAULT NULL" in sql
    assert "idx_tabcut_videos_mark_status" in sql
    assert "idx_tabcut_goods_mark_status" in sql


def test_tabcut_local_import_binding_migration_adds_video_mapping_columns():
    sql = (
        ROOT / "db" / "migrations" / "2026_06_10_tabcut_local_import_bindings.sql"
    ).read_text(encoding="utf-8")

    assert "ALTER TABLE tabcut_videos" in sql
    assert "ADD COLUMN local_product_id INT UNSIGNED NULL" in sql
    assert "ADD COLUMN local_media_item_id INT UNSIGNED NULL" in sql
    assert "idx_tabcut_videos_local_product_id" in sql
    assert "idx_tabcut_videos_local_media_item_id" in sql


def test_tabcut_goods_chinese_info_migration_adds_translation_fields_and_binding():
    sql = (
        ROOT / "db" / "migrations" / "2026_06_11_tabcut_goods_chinese_info.sql"
    ).read_text(encoding="utf-8")

    assert "ALTER TABLE tabcut_goods" in sql
    assert "item_name_zh TEXT NULL" in sql
    assert "item_name_zh_short VARCHAR(255) NULL" in sql
    assert "category_name_zh VARCHAR(255) NULL" in sql
    assert "category_l1_name_zh VARCHAR(255) NULL" in sql
    assert "category_l2_name_zh VARCHAR(255) NULL" in sql
    assert "category_l3_name_zh VARCHAR(255) NULL" in sql
    assert "zh_translation_status VARCHAR(16) NOT NULL DEFAULT ''pending''" in sql
    assert "zh_translation_attempts INT UNSIGNED NOT NULL DEFAULT 0" in sql
    assert "idx_tabcut_goods_zh_translation_status" in sql
    assert "'tabcut.translate_goods_info'" in sql
    assert "'openrouter'" in sql
    assert "'google/gemini-3.1-flash-lite'" in sql


def test_tabcut_video_chinese_info_migration_adds_translation_fields_and_binding():
    sql = (
        ROOT / "db" / "migrations" / "2026_06_14_tabcut_video_chinese_info.sql"
    ).read_text(encoding="utf-8")

    assert "Docs-anchor: docs/superpowers/specs/2026-06-14-tabcut-video-translation-task-design.md" in sql
    assert "ALTER TABLE tabcut_videos" in sql
    assert "video_desc_zh MEDIUMTEXT NULL" in sql
    assert "primary_item_name_zh TEXT NULL" in sql
    assert "zh_translation_status VARCHAR(16) NOT NULL DEFAULT ''pending''" in sql
    assert "zh_translation_attempts INT UNSIGNED NOT NULL DEFAULT 0" in sql
    assert "idx_tabcut_videos_zh_translation_status" in sql
    assert "'tabcut.translate_video_info'" in sql
    assert "'openrouter'" in sql
    assert "'google/gemini-2.5-flash'" in sql


def test_tabcut_video_openrouter_model_slug_fix_updates_binding_and_pricing():
    sql = (
        ROOT / "db" / "migrations" / "2026_06_14_tabcut_video_openrouter_model_slug_fix.sql"
    ).read_text(encoding="utf-8")

    assert "UPDATE llm_use_case_bindings" in sql
    assert "'tabcut.translate_video_info'" in sql
    assert "model_id = 'google/gemini-2.5-flash'" in sql
    assert "INSERT INTO ai_model_prices" in sql
    assert "'openrouter'" in sql
    assert "'google/gemini-2.5-flash'" in sql


def test_tabcut_video_openrouter_runtime_model_fix_updates_binding_and_pricing():
    sql = (
        ROOT / "db" / "migrations" / "2026_06_14_tabcut_video_openrouter_runtime_model_fix.sql"
    ).read_text(encoding="utf-8")

    assert "google/gemini-1.5-flash is invalid" in sql
    assert "google/gemini-flash-1.5 has no endpoints" in sql
    assert "UPDATE llm_use_case_bindings" in sql
    assert "'tabcut.translate_video_info'" in sql
    assert "model_id = 'google/gemini-2.5-flash'" in sql
    assert "INSERT INTO ai_model_prices" in sql
    assert "'openrouter'" in sql
    assert "'google/gemini-2.5-flash'" in sql


def test_tabcut_video_openrouter_user_requested_gemini31_flash_lite_fix_updates_binding_and_pricing():
    sql = (
        ROOT / "db" / "migrations" / "2026_06_14_tabcut_video_openrouter_user_requested_gemini31_flash_lite.sql"
    ).read_text(encoding="utf-8")

    assert "user requested OpenRouter Gemini 3.1 Flash Lite" in sql
    assert "INSERT INTO llm_use_case_bindings" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "'tabcut.translate_video_info'" in sql
    assert "'google/gemini-3.1-flash-lite'" in sql
    assert "INSERT INTO ai_model_prices" in sql
    assert "'openrouter'" in sql
    assert "'google/gemini-3.1-flash-lite'" in sql


def test_tabcut_video_translation_batch_size_setting_migration_seeds_default():
    sql = (
        ROOT / "db" / "migrations" / "2026_06_14_tabcut_video_translation_batch_size_setting.sql"
    ).read_text(encoding="utf-8")

    assert "Docs-anchor: docs/superpowers/specs/2026-06-14-tabcut-video-translation-task-design.md" in sql
    assert "INSERT IGNORE INTO system_settings" in sql
    assert "'tabcut_video_translation_batch_size'" in sql
    assert "'250'" in sql


def test_tabcut_daily_selection_registered():
    from appcore import scheduled_tasks

    task = scheduled_tasks.get_task_definition("tabcut_daily_selection")
    listed = {item["code"]: item for item in scheduled_tasks.task_definitions()}["tabcut_daily_selection"]

    assert task["runner"] == "python -m tools.tabcut_crawler.main --mode recent7 --days 30"
    assert "08:00" in task["schedule"]
    assert task["source_ref"] == "autovideosrt-tabcut-daily-selection.timer"
    assert "autovideosrt-tabcut-vnc.service" in task["deployment"]
    assert task["log_table"] == "scheduled_task_runs"
    assert listed["control_strategy"] == "systemd"
    assert listed["log_source"] == "db:scheduled_task_runs"
    assert listed["log_link_available"] is True


def test_tabcut_deploy_units_use_dedicated_browser_runtime_and_daily_8am_timer():
    service = (
        ROOT / "deploy" / "server_browser" / "autovideosrt-tabcut-daily-selection.service"
    ).read_text(encoding="utf-8")
    timer = (
        ROOT / "deploy" / "server_browser" / "autovideosrt-tabcut-daily-selection.timer"
    ).read_text(encoding="utf-8")
    installer = (
        ROOT / "deploy" / "server_browser" / "install_tabcut_daily_selection_timer.sh"
    ).read_text(encoding="utf-8")

    assert "User=cjh" in service
    assert "WorkingDirectory=/opt/autovideosrt" in service
    assert "Wants=network-online.target autovideosrt-tabcut-vnc.service" in service
    assert "After=network-online.target autovideosrt-tabcut-vnc.service" in service
    assert "TABCUT_CDP_URL=http://127.0.0.1:9227" in service
    assert "python -m tools.tabcut_crawler.main --mode recent7 --days 30" in service
    assert "OnCalendar=*-*-* 08:00:00" in timer
    assert "Unit=autovideosrt-tabcut-daily-selection.service" in timer
    assert "/data/autovideosrt/tabcut/daily" in installer
    assert "tabcut-daily-selection.timer" in installer
