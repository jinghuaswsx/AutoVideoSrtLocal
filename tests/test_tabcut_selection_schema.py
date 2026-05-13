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
